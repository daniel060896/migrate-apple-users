# Migración Sign in with Apple (Firebase)

Paso 3 tras el transfer de la app: actualizar el `sub` del proveedor `apple.com` en el **mismo** `uid` de Firebase.

Guía completa: [MIGRACION_USUARIOS_SIGN_IN_WITH_APPLE.md](./MIGRACION_USUARIOS_SIGN_IN_WITH_APPLE.md)

## VM (Ubuntu)

```bash
cd sign_in_with_apple_migration
sudo apt update && sudo apt install -y python3-pip
pip3 install --user -r requirements.txt
# si hace falta: pip3 install --user --break-system-packages -r requirements.txt
```

Configura `apple_migration_settings.py` (bundle, Team ID, rutas a `.p8` y JSON de Firebase; los paths son relativos a esta carpeta).

```bash
python3 migrar_sign_in_with_apple.py          # prueba con ONLY_FIREBASE_UID
python3 generar_manifest_apple.py             # lote grande: manifiesto primero
python3 migrar_sign_in_with_apple.py --shard-id 0 --num-shards 4
python3 fusionar_progreso_apple.py
```

No subas a git: `.p8`, service account, `apple_migration_*.json`, `apple_manifest_*.jsonl` (ver `.gitignore`).

## EC2 (checklist)

1. Copia esta carpeta a la instancia (`scp` / `rsync`). Incluye **a mano** (no van en git): `AuthKey_*.p8`, `*firebase-adminsdk*.json`, y si retomas Kobe el `apple_migration_<bundle>.json` con los `done`.
2. `chmod 600 AuthKey_*.p8 *firebase-adminsdk*.json`
3. Python 3.9+ (`python3 --version`). Salida a internet hacia `appleid.apple.com` y Google APIs.
4. **Kobe (~150 Apple, ya migraste parte):** modo **simple** sin shards y el JSON de progreso copiado; omite los `done` y termina el resto.
5. **Proyecto enorme en Auth:** `generar_manifest_apple.py` una vez, luego 4–8 shards y `fusionar_progreso_apple.py`. Cada shard tiene su propio JSON; el progreso del modo simple no se reparte solo entre shards.
6. Prueba login TestFlight con un `done` antes de publicar el release nuevo.