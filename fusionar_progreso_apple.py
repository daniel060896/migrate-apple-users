#!/usr/bin/env python3
"""Une los JSON de progreso de cada worker (shard) en un solo archivo.

Desde sign_in_with_apple_migration/:

  python3 fusionar_progreso_apple.py
  python3 fusionar_progreso_apple.py --output apple_migration_com.sushicorp.kobe.json
"""

from __future__ import annotations

import argparse
import json
import sys

from apple_migration_core import load_progress, merge_progress, progress_file, progress_shard_glob
from apple_migration_settings import BUNDLE_ID


def main() -> int:
    parser = argparse.ArgumentParser(description="Fusiona progreso de shards Sign in with Apple")
    parser.add_argument("--output", default="", help="JSON final (por defecto apple_migration_<bundle>.json)")
    args = parser.parse_args()

    shards = progress_shard_glob()
    if not shards:
        print("No hay archivos apple_migration_*.shard-*.json", file=sys.stderr)
        return 1

    output = progress_file() if not args.output else __import__("pathlib").Path(args.output)
    report = merge_progress(shards, output)

    print(f"Bundle: {BUNDLE_ID}")
    print(f"Shards fusionados: {len(shards)}")
    for shard in shards:
        data = load_progress(shard)
        print(f"  {shard.name}: {len(data)} entradas")
    print(f"Salida: {report['output']}")
    print(f"Total uids: {report['merged_count']}")
    print("Estados:", json.dumps(report["stats"], ensure_ascii=False))
    if report["collisions"]:
        print(f"Uids con entrada distinta en varios shards: {len(report['collisions'])}")
        for uid in report["collisions"][:20]:
            print(f"  {uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())