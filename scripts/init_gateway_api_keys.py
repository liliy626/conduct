#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway_core.infra.api_keys import api_key_table_name, api_key_table_schema, init_gateway_api_key_table
from gateway_core.infra.postgres_dsn import gateway_api_key_postgres_dsn


def main() -> int:
    parser = argparse.ArgumentParser(description="Create platform.gateway_api_keys.")
    parser.add_argument("--dsn", default="")
    args = parser.parse_args()
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env", override=False)
    dsn = args.dsn or gateway_api_key_postgres_dsn()
    if not dsn:
        print("missing DSN: pass --dsn or set POSTGRES_DSN", file=sys.stderr)
        return 2
    try:
        import psycopg
    except Exception as exc:
        print(f"missing psycopg: {exc}", file=sys.stderr)
        return 2
    ok = init_gateway_api_key_table(dsn=dsn, psycopg_module=psycopg)
    if not ok:
        print("failed to create gateway api key table", file=sys.stderr)
        return 1
    print(f"ok: {api_key_table_schema()}.{api_key_table_name()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
