"""
STRAVA Local Legend Dashboard - Application Web Flask
======================================================
Version app multi-utilisateur avec toutes les fonctionnalites :
  - OAuth Strava par utilisateur
  - Cache activites / segments / traces GPS par utilisateur
  - ORS pour le routage (50 waypoints gratuits)
  - Boutons + pour selectionner les segments
  - Passages multiples selon distance cible
  - Export GPX
  - Filtre geographique Mont-Royal

Prérequis : pip install flask requests polyline
"""

import os, math, json, time, hashlib
import requests
import polyline as pl
from datetime import datetime, timedelta
from flask import Flask, redirect, request, session, render_template, jsonify
from urllib.parse import urlencode

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# ── CONFIG ───────────────────────────────────────────────────
STRAVA_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
ORS_KEY              = os.environ.get("ORS_KEY=", "")
BASE_URL             = "https://www.strava.com/api/v3"
DAYS                 = 45
CACHE_DIR            = "user_cache"

# Bounding box Montreal
MTL_LAT_MIN, MTL_LAT_MAX = 45.41, 45.70
MTL_LNG_MIN, MTL_LNG_MAX = -73.97, -73.47

# Filtre Mont-Royal
MONT_ROYAL_LAT = 45.5088
MONT_ROYAL_LNG = -73.5878
RAYON_KM       = 2.5
# ─────────────────────────────────────────────────────────────

os.makedirs(CACHE_DIR, exist_ok=True)


# ── Helpers cache par utilisateur ────────────────────────────
def user_cache_path(athlete_id, filename):
    d = os.path.join(CACHE_DIR, str(athlete_id))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)


def load_json(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ── Helpers Strava ────────────────────────────────────────────
def strava_get(path, token, params=None):
    while True:
        res = requests.get(
            f"{BASE_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if res.status_code == 429:
            wait = int(res.headers.get("X-RateLimit-Reset", 60))
            time.sleep(wait)
            continue
        res.raise_for_status()
        return res.json()


def is_montreal(act):
    coords = act.get("start_latlng") or []
    if len(coords) < 2:
        return False
    lat, lng = coords
    return MTL_LAT_MIN <= lat <= MTL_LAT_MAX and MTL_LNG_MIN <= lng <= MTL_LNG_MAX


def dist_mont_royal(lat, lng):
    if not lat or not lng:
        return 999
    R    = 6371
    dLat = math.radians(lat - MONT_ROYAL_LAT)
    dLng = math.radians(lng - MONT_ROYAL_LNG)
    a    = math.sin(dLat/2)**2 + math.cos(math.radians(MONT_ROYAL_LAT)) * \
           math.cos(math.radians(lat)) * math.sin(dLng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def indicateur_normalise(valeurs):
    arr = sorted([max(v, 0) for v in valeurs])
    p5  = arr[max(0, int(len(arr) * 0.05))]
    p95 = arr[min(len(arr)-1, int(len(arr) * 0.95))]
    if p95 == p5:
        return [0.5] * len(valeurs)
    return [round(min(max((v - p5) / (p95 - p5), 0.0), 1.0), 3)
            for v in [max(v, 0) for v in valeurs]]


def couleur_gradient(ratio):
    r = int(255 * ratio)
    g = int(255 * (1 - ratio))
    return f"#{r:02x}{g:02x}00"


# ── ROUTES AUTH ──────────────────────────────────────────────
@app.route("/")
def index():
    if "athlete" in session:
        return redirect("/dashboard")
    return render_template("login.html")


@app.route("/login")
def login():
    params = {
        "client_id":       STRAVA_CLIENT_ID,
        "redirect_uri": request.url_root.rstrip("/").replace("http://", "https://") + "/callback",
        "response_type":   "code",
        "approval_prompt": "auto",
        "scope":           "read,activity:read_all",
    }
    return redirect("https://www.strava.com/oauth/authorize?" + urlencode(params))


@app.route("/callback")
def callback():
    code  = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect("/?error=denied")
    res = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    })
    if not res.ok:
        return redirect("/?error=token")
    data    = res.json()
    athlete = data["athlete"]
    session["access_token"] = data["access_token"]
    session["athlete"]      = {
        "id":        athlete["id"],
        "firstname": athlete["firstname"],
        "lastname":  athlete["lastname"],
        "profile":   athlete.get("profile", ""),
    }
    return redirect("/dashboard")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/dashboard")
def dashboard():
    if "athlete" not in session:
        return redirect("/")
    return render_template("dashboard.html",
                           athlete=session["athlete"],
                           ors_key=ORS_KEY)


# ── API SEGMENTS ─────────────────────────────────────────────
@app.route("/api/segments")
def api_segments():
    if "access_token" not in session:
        return jsonify({"error": "Non connecte"}), 401

    token      = session["access_token"]
    athlete_id = session["athlete"]["id"]
    now        = datetime.utcnow()
    since_n    = now - timedelta(days=DAYS)

    # Chemins des caches utilisateur
    acts_cache_path   = user_cache_path(athlete_id, "activities.json")
    segs_cache_path   = user_cache_path(athlete_id, "segments.json")
    traces_cache_path = user_cache_path(athlete_id, "traces.json")

    activities_cache = load_json(acts_cache_path)
    segments_csv     = load_json(segs_cache_path)   # seg_id -> segment data
    traces_cache     = load_json(traces_cache_path)

    # 1. Activites recentes
    all_activities = []
    page = 1
    while True:
        try:
            batch = strava_get("/athlete/activities", token, params={
                "after":    int(since_n.timestamp()),
                "per_page": 100,
                "page":     page,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if not batch:
            break
        all_activities.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    rides     = [a for a in all_activities if a["type"] in ("Run", "TrailRun") and is_montreal(a)]
    new_rides = [a for a in rides if str(a["id"]) not in activities_cache]

    # 2. Extraction segments
    segment_efforts = {}

    # Depuis cache activites
    for act in rides:
        if str(act["id"]) in activities_cache:
            for sid, name in activities_cache[str(act["id"])]["segments"].items():
                if sid not in segment_efforts:
                    segment_efforts[sid] = {"name": name, "my_efforts": 0}
                segment_efforts[sid]["my_efforts"] += 1

    # Nouvelles activites
    for act in new_rides:
        try:
            detail      = strava_get(f"/activities/{act['id']}", token)
            segs_in_act = {}
            for eff in detail.get("segment_efforts", []):
                sid  = str(eff["segment"]["id"])
                name = eff["segment"]["name"]
                segs_in_act[sid] = name
                if sid not in segment_efforts:
                    segment_efforts[sid] = {"name": name, "my_efforts": 0}
                segment_efforts[sid]["my_efforts"] += 1
            activities_cache[str(act["id"])] = {
                "name":     act["name"],
                "date":     act["start_date_local"][:10],
                "segments": segs_in_act,
            }
        except Exception:
            pass

    save_json(acts_cache_path, activities_cache)

    # 3. Fusion segments CSV + nouveaux
    all_segments     = {}
    all_segments_map = {}

    for sid_str, seg in segments_csv.items():
        all_segments[sid_str] = seg
        all_segments_map[sid_str] = seg

    for sid_str, seg in segment_efforts.items():
        if sid_str in all_segments_map:
            all_segments_map[sid_str]["mes_efforts"] = seg["my_efforts"]
            all_segments_map[sid_str]["ecart"]       = \
                all_segments_map[sid_str]["ll_efforts"] - seg["my_efforts"]
        else:
            # Nouveau segment — appel API
            try:
                info = strava_get(f"/segments/{sid_str}", token)
                ll   = info.get("local_legend")

                distance_km = round(info.get("distance", 0) / 1000, 2)
                denivele_m  = round(info.get("total_elevation_gain", 0), 1)
                ll_name     = ll.get("title", "—")                if ll else "—"
                ll_efforts  = int(ll.get("effort_count", 0) or 0) if ll else 0
                my_efforts  = seg["my_efforts"]
                ecart       = ll_efforts - my_efforts

                start   = info.get("start_latlng", [])
                end_ll  = info.get("end_latlng", [])
                encoded = info.get("map", {}).get("polyline") or \
                          info.get("map", {}).get("summary_polyline")
                trace   = pl.decode(encoded) if encoded else []

                traces_cache[sid_str] = {
                    "lat":     start[0] if start else None,
                    "lng":     start[1] if start else None,
                    "lat_end": end_ll[0] if end_ll else None,
                    "lng_end": end_ll[1] if end_ll else None,
                    "trace":   trace,
                }

                seg_data = {
                    "segment_id":   int(sid_str),
                    "segment":      seg["name"],
                    "distance_km":  distance_km,
                    "denivele_m":   denivele_m,
                    "local_legend": ll_name,
                    "ll_efforts":   ll_efforts,
                    "mes_efforts":  my_efforts,
                    "ecart":        ecart,
                }
                all_segments[sid_str]     = seg_data
                all_segments_map[sid_str] = seg_data

            except Exception:
                pass

    # Calcul indicateur
    for sid_str, s in all_segments.items():
        efforts_a_faire = (s["ecart"] + 1) if s["ecart"] >= 0 else 0
        difficulte      = s["distance_km"] + (s["denivele_m"] * 0.01)
        s["indicateur"] = round(efforts_a_faire * difficulte, 3)

    save_json(segs_cache_path, all_segments)
    save_json(traces_cache_path, traces_cache)

    # 4. Applique traces GPS + filtre Mont-Royal
    results = []
    for sid_str, s in all_segments.items():
        cached = traces_cache.get(sid_str, {})
        lat    = cached.get("lat")
        lng    = cached.get("lng")
        if not lat or not lng:
            continue
        if dist_mont_royal(lat, lng) > RAYON_KM:
            continue
        results.append({
            **s,
            "lat":     lat,
            "lng":     lng,
            "lat_end": cached.get("lat_end"),
            "lng_end": cached.get("lng_end"),
            "trace":   cached.get("trace", []),
        })

    if not results:
        return jsonify({"segments": [], "athlete": session["athlete"]})

    # 5. Normalisation
    ind_norms = indicateur_normalise([r["indicateur"] for r in results])
    for idx, (r, norm) in enumerate(zip(results, ind_norms)):
        r["indic_norm"] = norm
        r["color"]      = couleur_gradient(norm)
        r["idx"]        = idx

    results.sort(key=lambda x: x["indic_norm"])
    for idx, r in enumerate(results):
        r["idx"] = idx

    return jsonify({"segments": results, "athlete": session["athlete"]})


# ── API ROUTE ORS ─────────────────────────────────────────────
@app.route("/api/route", methods=["POST"])
def api_route():
    if "athlete" not in session:
        return jsonify({"error": "Non connecte"}), 401
    data = request.json
    res  = requests.post(
        "https://api.openrouteservice.org/v2/directions/foot-hiking",
        json=data,
        headers={
            "Content-Type":  "application/json",
            "Authorization": ORS_KEY,
        },
    )
    return jsonify(res.json()), res.status_code


if __name__ == "__main__":
    print("\n  Strava Local Legend Dashboard")
    print(f"  Client ID : {STRAVA_CLIENT_ID}")
    print(f"  ORS Key   : {ORS_KEY[:8]}..." if ORS_KEY else "  ORS Key   : NON CONFIGURE")
    print("\n  Ouvre http://localhost:5000")
    app.run(debug=True, port=5000)
