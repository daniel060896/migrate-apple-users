# Paso 3. Escribir el identificador nuevo de Apple en Firebase

App Store Connect (Sign in with Apple, llaves `.p8`, Team ID) y Firebase Console (proveedor Apple del mismo proyecto, y la llave APNs del equipo destino) ya están hechos.

El proyecto Firebase no se muda. El `uid` de Firebase es el que usan `ECOMMERCE_USERS_V2`, `ECOMMERCE_TRANSACTIONAL`, `ECOMMERCE_FULFILLMENT` y el authorizer de `XMART_API_GATEWAY`. Este paso cambia solo el `sub` guardado en el proveedor `apple.com` de ese mismo usuario. Pedidos y fulfillment no se tocan.

Apple sigue teniendo una ventana de 60 días desde la aceptación del transfer para generar y canjear el `transfer_sub`.

Carpeta `sign_in_with_apple_migration/` (constantes en `apple_migration_settings.py`):

| Archivo | Uso |
| --- | --- |
| `generar_manifest_apple.py` | Una pasada: solo usuarios con `apple.com` → `apple_manifest_<bundle>.jsonl` |
| `migrar_sign_in_with_apple.py` | Migra (modo simple o `--shard-id` / `--num-shards`) |
| `fusionar_progreso_apple.py` | Une `apple_migration_<bundle>.shard-*.json` en un solo JSON |

Cada llamada a Apple tiene hasta **3 reintentos** (503, timeouts, `invalid_request` transitorio). Config: `MAX_RETRIES` y `RETRY_BACKOFF_SECONDS` en settings.

## Qué hace el script

Para cada usuario de Firebase cuyo `providerData` incluye `apple.com`:

1. Con la llave del equipo que recibió la app pide el `transfer_sub` a partir del `sub` viejo. Durante los 60 días posteriores al transfer, Apple acepta esa llamada firmada por el equipo destino.
2. Con la misma llave canjea ese `transfer_sub` por el `sub` nuevo. Si la persona usaba Ocultar mi correo, Apple también devuelve el relay del equipo nuevo.
3. Con `APPLY = False` solo guarda ese mapeo. No escribe Firebase.
4. Con `APPLY = True` desvincula `apple.com` y lo vuelve a vincular con el `sub` nuevo, en el mismo `uid`. Si el enlace nuevo falla, intenta dejar el `sub` viejo otra vez.
5. Si el correo de la cuenta en Firebase es el relay viejo (o está vacío, o es igual al correo del proveedor Apple) y Apple mandó uno nuevo, actualiza ese email en Firebase. La tabla `user` de `ECOMMERCE_USERS_V2` no se toca: el perfil sigue en el mismo `uid`.

El avance queda en `apple_migration_<bundle>.json`, al lado del script. No lo subas a git: tiene los identificadores de Apple. Si el script se corta, la siguiente corrida retoma a los que ya tienen `status: done` y reutiliza el mapeo de los que quedaron en `mapped`.

Cuentas de correo/contraseña y de Google no entran en el recorrido.

## Antes de la primera corrida

1. Exportar los usuarios de Authentication de ese proyecto (Firebase Console, o `firebase auth:export`) y guardar el archivo fuera del repo.
2. En la VM, copiar esta carpeta entera (scripts, `.p8`, JSON de Firebase) y instalar dependencias:

```bash
cd sign_in_with_apple_migration
pip3 install --user -r requirements.txt
# o: pip install firebase-admin PyJWT cryptography requests
```

3. En `apple_migration_settings.py`, configurar:

| Constante | Valor |
| --- | --- |
| `CLIENT_ID` | Bundle ID de la app transferida. Sin Team ID. |
| `TEAM_ID`, `KEY_ID`, `P8_PATH` | Llave Sign in with Apple del equipo que recibió la app. |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | JSON de la cuenta de servicio del proyecto Firebase de este `account`. |
| `ONLY_FIREBASE_UID` | El `uid` de un usuario interno, para la prueba. Vacío cuando ya toque el lote. |
| `APPLY` | `False` hasta revisar el mapeo de esa persona. |

Si varias apps comparten el mismo proyecto Firebase y el mismo equipo destino, el `sub` de Apple es del equipo, no de la app. Igual se corre **una vez por bundle ID** que se transfirió: el `client_id` de la llamada a Apple tiene que ser el bundle de una app transferida. Un usuario que Apple no reconozca para ese bundle queda con `status: error` en el JSON y el resto sigue.

## Corrida de un usuario

```bash
cd sign_in_with_apple_migration
python3 migrar_sign_in_with_apple.py
```

Con `APPLY = False` la salida de ese uid tiene que traer `transfer_sub` y `sub_nuevo`. El `uid` impreso es el de Firebase y no debe ser otro.

Pasa `APPLY` a `True`, deja el mismo `ONLY_FIREBASE_UID` y corre otra vez. Al terminar, en Firebase Console ese usuario sigue con el mismo uid y el proveedor Apple muestra el `sub` nuevo.

Entra con esa Apple ID en un build firmado por el equipo destino (TestFlight alcanza). El token de Firebase tiene que traer el mismo `uid`. El perfil de users y un pedido viejo tienen que seguir ahí.

Si Firebase devuelve otro `uid`, no corras el lote. Ese uid nuevo es una cuenta vacía creada porque el release nuevo entró antes de migrar. El JSON de progreso marca el caso como `conflict` cuando el correo nuevo ya está en otro usuario, y no desvincula al original.

Cuando ese usuario entre bien, vacía `ONLY_FIREBASE_UID`, deja `APPLY = True` y corre el resto. Las filas `mapped` o `done` no vuelven a pedir el `sub` a Apple.

## VM: paralelo (proyectos con millones de usuarios en Auth)

Si en Firebase hay muchas cuentas totales pero pocos con Apple, **no** lances varios workers sin manifiesto: cada uno barrería todo Auth. Orden:

```bash
cd sign_in_with_apple_migration
pip3 install --user -r requirements.txt

# 1) Inventario (una vez; puede tardar horas si hay ~1.6M usuarios, pero solo guarda apple.com)
python3 generar_manifest_apple.py

# 2) Workers (4–8 procesos; cada uno escribe su shard)
python3 migrar_sign_in_with_apple.py --shard-id 0 --num-shards 4 &
python3 migrar_sign_in_with_apple.py --shard-id 1 --num-shards 4 &
python3 migrar_sign_in_with_apple.py --shard-id 2 --num-shards 4 &
python3 migrar_sign_in_with_apple.py --shard-id 3 --num-shards 4 &
wait

# 3) Un solo JSON de progreso
python3 fusionar_progreso_apple.py
```

Progreso por worker: `apple_migration_<bundle>.shard-00.json`, `shard-01.json`, etc. Tras fusionar: `apple_migration_<bundle>.json`.

Reanudar: vuelve a correr el mismo shard; omite `status: done`. Errores: reintenta el script o un solo uid con `ONLY_FIREBASE_UID`.

Dry-run en paralelo: `APPLY = False` en settings o `--no-apply` en cada worker.

## Cada ecommerce

Repite el bloque de constantes y una corrida por app:

```text
Ecommerce:
accountId:
vendorId:
Firebase projectId:
Bundle ID (CLIENT_ID):
Usuarios apple.com:
Usuario de prueba (ONLY_FIREBASE_UID):
Dry-run: pendiente
APPLY del usuario de prueba: pendiente
Mismo uid en TestFlight: pendiente
Lote APPLY: pendiente
```

`vendorId` no es filtro del script. Firebase está partido por `account`. Sirve para saber qué app corresponde a esta corrida.

## Si el enlace queda a medias

El script, al fallar el enlace del `sub` nuevo, vuelve a vincular el `sub` viejo. Si esa restauración también falla, el error en el JSON dice que `apple.com` quedó desvinculado e incluye `old_sub` y `new_sub`. Con esos dos valores se restaura a mano el proveedor del uid original. No borres ese usuario de Firebase: el perfil y los pedidos siguen colgando de ese `uid`.
