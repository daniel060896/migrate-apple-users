"""Constantes compartidas: manifest, workers en paralelo y migración simple."""

# Bundle ID de la app transferida (App ID en Apple Developer).
BUNDLE_ID = "com.sushicorp.kobe"

# Si Firebase usa Services ID para Apple, ponlo aquí. Vacío = BUNDLE_ID.
SERVICES_CLIENT_ID = ""

TEAM_ID = "FQRKJL6L22"
KEY_ID = "C5262AG392"
P8_PATH = "AuthKey_C5262AG392.p8"

FIREBASE_SERVICE_ACCOUNT_PATH = "juan-valdez-development-firebase-adminsdk-8kff5-a38ff714f8a.json"

# Migración simple (sin --manifest): vacío = todos; con uid = solo ese usuario.
ONLY_FIREBASE_UID = ""

# False = solo mapeo Apple; True = escribe Firebase.
APPLY = True

PAUSE_SECONDS = 0.4

# Reintentos por usuario (Apple 503, timeouts, invalid_request transitorio).
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2.0, 5.0, 10.0)

# Nombre base del manifiesto y del progreso (se arma con BUNDLE_ID en core).
MANIFEST_BASENAME = ""  # vacío -> apple_manifest_<bundle>.jsonl