#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SeedKey:
    api_key: str
    key_type: str
    school_id: str = ""
    schema_name: str = ""
    display_name: str = ""
    rate_limit_rpm: int = 200


DEFAULT_KEYS = [
    SeedKey("key_a", "school", "sch_zx_mlh", "mlh", "美兰湖中学"),
    SeedKey("key_a_raw", "school", "sch_zx_mlh", "mlh", "美兰湖中学 Raw"),
    SeedKey("key_a_ddl", "school", "sch_zx_mlh", "mlh", "美兰湖中学 DDL"),
    SeedKey("key_b", "school", "sch_zx_sdfz", "sdfz", "上大附中"),
    SeedKey("key_b_raw", "school", "sch_zx_sdfz", "sdfz", "上大附中 Raw"),
    SeedKey("key_b_ddl", "school", "sch_zx_sdfz", "sdfz", "上大附中 DDL"),
    SeedKey("key_c", "school", "sch_zx_jfdf", "zx_jfdf", "zx_jfdf"),
    SeedKey("key_policy", "policy", "", "official_policy", "政策助手"),
    SeedKey("key_admin", "admin", "", "", "Gateway Admin"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed platform.gateway_api_keys for local gateway testing.")
    parser.add_argument("--dsn", default="")
    parser.add_argument("--rpm", type=int, default=200)
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

    if not init_gateway_api_key_table(dsn=dsn, psycopg_module=psycopg):
        print("failed to initialize gateway api key table", file=sys.stderr)
        return 1

    schema = api_key_table_schema()
    table = api_key_table_name()
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for item in DEFAULT_KEYS:
                cur.execute(
                    f"""
                    INSERT INTO "{schema}"."{table}"
                        (api_key, key_prefix, key_type, school_id, schema_name,
                         display_name, enabled, rate_limit_rpm, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, now())
                    ON CONFLICT (api_key) DO UPDATE SET
                        key_prefix = EXCLUDED.key_prefix,
                        key_type = EXCLUDED.key_type,
                        school_id = EXCLUDED.school_id,
                        schema_name = EXCLUDED.schema_name,
                        display_name = EXCLUDED.display_name,
                        enabled = TRUE,
                        rate_limit_rpm = EXCLUDED.rate_limit_rpm,
                        updated_at = now()
                    """,
                    [
                        item.api_key,
                        item.api_key[:8],
                        item.key_type,
                        item.school_id,
                        item.schema_name,
                        item.display_name,
                        max(1, int(args.rpm or item.rate_limit_rpm)),
                    ],
                )
        conn.commit()

    print(f"ok: upserted {len(DEFAULT_KEYS)} rows into {schema}.{table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
