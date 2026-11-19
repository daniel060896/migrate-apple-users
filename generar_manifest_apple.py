#!/usr/bin/env python3
"""Inventario único: todos los usuarios Firebase con proveedor apple.com.

Escribe una línea JSON por usuario (JSONL). Los workers leen este archivo y no
vuelven a recorrer los millones de cuentas del proyecto.

Desde sign_in_with_apple_migration/:

  python3 generar_manifest_apple.py

Dependencias: requirements.txt (firebase-admin)
"""

from __future__ import annotations

import argparse
import sys
import time

from apple_migration_core import (
    iter_apple_users_from_firebase,
    manifest_path,
    manifest_row,
    require_constants,
    resolve_path,
    write_manifest_row,
)
from apple_migration_settings import FIREBASE_SERVICE_ACCOUNT_PATH, MANIFEST_BASENAME, ONLY_FIREBASE_UID


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera manifiesto JSONL de usuarios apple.com")
    parser.add_argument(
        "--output",
        default="",
        help="Ruta del .jsonl (por defecto apple_manifest_<bundle>.jsonl)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="No borra el archivo; agrega usuarios nuevos (no deduplica).",
    )
    args = parser.parse_args()

    require_constants()
    out = manifest_path(args.output or MANIFEST_BASENAME)
    if out.exists() and not args.resume:
        print(f"Ya existe {out}. Usa --resume o bórralo.", file=sys.stderr)
        return 1
    if not args.resume:
        out.write_text("", encoding="utf-8")

    import firebase_admin
    from firebase_admin import credentials

    service_account = str(resolve_path(FIREBASE_SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(credentials.Certificate(service_account))

    started = time.time()
    count = 0
    print(f"Escribiendo manifiesto: {out}")
    print("Listando Auth (solo se guardan usuarios con apple.com)...")

    for user, provider in iter_apple_users_from_firebase():
        if ONLY_FIREBASE_UID and user.uid != ONLY_FIREBASE_UID:
            continue
        write_manifest_row(out, manifest_row(user, provider))
        count += 1
        if count % 500 == 0:
            elapsed = int(time.time() - started)
            print(f"  ... {count} usuarios apple.com ({elapsed}s)")

    print(f"Listo: {count} filas en {out} ({int(time.time() - started)}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())