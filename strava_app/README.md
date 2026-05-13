# Strava Local Legend Dashboard

## Deploiement Railway

### 1. Upload sur GitHub
- Cree un repo `strava-local-legend` sur github.com
- Upload tous les fichiers de ce dossier

### 2. Deploy sur Railway
- railway.app → New Project → Deploy from GitHub
- Selectionne `strava-local-legend`

### 3. Variables d'environnement sur Railway
```
STRAVA_CLIENT_ID     = ton_client_id
STRAVA_CLIENT_SECRET = ton_client_secret
ORS_KEY              = ta_cle_openrouteservice
SECRET_KEY           = une_chaine_aleatoire_longue
```

### 4. Callback Strava
- Copie l'URL Railway (ex: strava-local-legend.railway.app)
- strava.com/settings/api → Authorization Callback Domain → colle l'URL

### 5. Test local
```bash
pip install -r requirements.txt

export STRAVA_CLIENT_ID=xxx
export STRAVA_CLIENT_SECRET=xxx
export ORS_KEY=xxx
export SECRET_KEY=dev-secret

python app.py
# Ouvre http://localhost:5000
```

## Structure
```
strava_app/
  app.py              # Backend Flask
  requirements.txt    # Dependances
  Procfile            # Config Railway
  templates/
    login.html        # Page connexion
    dashboard.html    # Carte interactive
  user_cache/         # Cache par utilisateur (cree automatiquement)
```
