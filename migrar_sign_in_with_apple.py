#!/usr/bin/env python3
"""Migración Sign in with Apple en Firebase (simple o por shards).

Ejecutar desde sign_in_with_apple_migration/:

Modo simple (recorre Auth filtrando apple.com):
  python3 migrar_sign_in_with_apple.py

Modo paralelo en VM (millones de cuentas en el proyecto):
  python3 generar_manifest_apple.py
  python3 migrar_sign_in_with_apple.py --shard-id 0 --num-shards 4 &
  ...
  python3 fusionar_progreso_apple.py

Constantes en apple_migration_settings.py. Dependencias: requirements.txt
"""

from __future__ import annotations

import argparse
import sys
import time

from apple_migration_core import (
    AppleTeam,
    FirebaseAuthAdmin,
    apple_client_id,
    iter_apple_users_from_firebase,
    iter_manifest_rows,
    load_progress,
    manifest_path,
    migrate_one_user,
    progress_file,
    require_constants,
    resolve_path,
    shard_for_uid,
)
from apple_migration_settings import (
    APPLY,
    BUNDLE_ID,
    FIREBASE_SERVICE_ACCOUNT_PATH,
    KEY_ID,
    MANIFEST_BASENAME,
    ONLY_FIREBASE_UID,
    PAUSE_SECONDS,
    P8_PATH,
    TEAM_ID,
)


def process_rows(rows, team, firebase_writer, progress_path, apply: bool) -> dict:
    progress = load_progress(progress_path)
    counts = {"seen": 0, "done": 0, "skipped": 0, "failed": 0, "dry_run": 0}

    for row in rows:
        counts["seen"] += 1
        uid = row["firebase_uid"]
        print(f"[user] {uid} sub_viejo={row['old_sub']}")
        try:
            outcome = migrate_one_user(
                uid,
                row["old_sub"],
                row.get("old_provider_email") or "",
                row.get("account_email") or "",
                team,
                firebase_writer,
                progress,
                progress_path,
                apply,
            )
            if outcome == "skip":
                counts["skipped"] += 1
                print(f"[skip] {uid}")
            elif outcome == "dry-run":
                counts["dry_run"] += 1
                print(f"[dry-run] {uid}")
            elif outcome == "done":
                counts["done"] += 1
                print(f"[done] {uid}")
            elif outcome == "conflict":
                counts["failed"] += 1
                print(f"[conflict] {uid}", file=sys.stderr)
            else:
                counts["failed"] += 1
        except Exception as error:
            counts["failed"] += 1
            from apple_migration_core import record

            record(progress, progress_path, uid, status="error", error=str(error))
            print(f"[error] {uid} {error}", file=sys.stderr)
        time.sleep(PAUSE_SECONDS)

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrar proveedor apple.com en Firebase")
    parser.add_argument("--manifest", default="", help="JSONL de generar_manifest_apple.py")
    parser.add_argument("--shard-id", type=int, default=None, help="Índice de este worker (0 .. num-shards-1)")
    parser.add_argument("--num-shards", type=int, default=None, help="Cantidad de workers en paralelo")
    parser.add_argument(
        "--apply",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Sobreescribe APPLY de apple_migration_settings.py",
    )
    args = parser.parse_args()

    if (args.shard_id is None) != (args.num_shards is None):
        print("Usa --shard-id y --num-shards juntos.", file=sys.stderr)
        return 1
    if args.shard_id is not None and (args.shard_id < 0 or args.shard_id >= args.num_shards):
        print("--shard-id fuera de rango.", file=sys.stderr)
        return 1

    require_constants()
    apply = APPLY if args.apply is None else args.apply

    import firebase_admin
    from firebase_admin import credentials

    service_account = str(resolve_path(FIREBASE_SERVICE_ACCOUNT_PATH))
    firebase_admin.initialize_app(credentials.Certificate(service_account))
    firebase_writer = FirebaseAuthAdmin(service_account)
    team = AppleTeam(TEAM_ID, KEY_ID, P8_PATH)

    if args.shard_id is not None:
        progress_path = progress_file(args.shard_id)
    else:
        progress_path = progress_file()

    print(f"Proyecto Firebase: {firebase_writer.project_id}")
    print(f"Bundle ID: {BUNDLE_ID}")
    print(f"Apple client_id: {apple_client_id()}")
    print(f"APPLY: {apply}")
    if args.shard_id is not None:
        print(f"Shard: {args.shard_id} / {args.num_shards}")
    print(f"Progreso: {progress_path}")

    if args.shard_id is not None or args.manifest:
        manifest = manifest_path(args.manifest or MANIFEST_BASENAME)
        if not manifest.is_file():
            print(f"No existe manifiesto: {manifest}", file=sys.stderr)
            return 1

        def row_stream():
            for row in iter_manifest_rows(manifest):
                uid = row["firebase_uid"]
                if ONLY_FIREBASE_UID and uid != ONLY_FIREBASE_UID:
                    continue
                if args.shard_id is not None and shard_for_uid(uid, args.num_shards) != args.shard_id:
                    continue
                yield row

        counts = process_rows(row_stream(), team, firebase_writer, progress_path, apply)
    else:
        def firebase_stream():
            for user, provider in iter_apple_users_from_firebase():
                from apple_migration_core import manifest_row

                yield manifest_row(user, provider)

        counts = process_rows(firebase_stream(), team, firebase_writer, progress_path, apply)

    if ONLY_FIREBASE_UID and counts["seen"] == 0:
        print(f"No hay usuario apple.com con uid {ONLY_FIREBASE_UID}", file=sys.stderr)
        return 1

    print(
        f"Vistos: {counts['seen']}  listos: {counts['done']}  dry-run: {counts['dry_run']}  "
        f"omitidos: {counts['skipped']}  errores: {counts['failed']}"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())