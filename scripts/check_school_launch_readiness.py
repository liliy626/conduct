#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway_core.infra.api_keys import api_key_table_name, api_key_table_schema
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.schema_context.query_experience import experience_table


def main() -> int:
    parser = argparse.ArgumentParser(description="Check 20-school gateway launch readiness.")
    parser.add_argument("--schemas", required=True, help="Comma-separated schema names")
    parser.add_argument("--dsn", default="")
    parser.add_argument("--out-dir", default="logs/launch_readiness")
    parser.add_argument("--vector-dim", type=int, default=1024)
    args = parser.parse_args()
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env", override=False)
    dsn = args.dsn or postgres_dsn()
    if not dsn:
        print("missing DSN", file=sys.stderr)
        return 2
    try:
        import psycopg
    except Exception as exc:
        print(f"missing psycopg: {exc}", file=sys.stderr)
        return 2
    schemas = [item.strip() for item in args.schemas.replace(";", ",").split(",") if item.strip()]
    report = {
        "created_at": time.time(),
        "expected_vector_dim": args.vector_dim,
        "schemas": [],
        "summary": {"total": len(schemas), "ready": 0, "failed": 0},
    }
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        for schema in schemas:
            item = _check_schema(conn, schema=schema, expected_dim=args.vector_dim)
            report["schemas"].append(item)
            if item["ready"]:
                report["summary"]["ready"] += 1
            else:
                report["summary"]["failed"] += 1
        report["api_keys"] = _check_api_keys(conn)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"launch_readiness_{stamp}.json"
    html_path = out_dir / f"launch_readiness_{stamp}.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    html_path.write_text(_render_html(report), encoding="utf-8")
    print(json_path)
    print(html_path)
    return 0 if report["summary"]["failed"] == 0 and report["api_keys"]["ok"] else 1


def _check_schema(conn, *, schema: str, expected_dim: int) -> dict:
    checks = []
    checks.append(_check_exists(conn, "schema_exists", "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s", [schema]))
    checks.append(_check_exists(conn, "ddl_vector_documents_exists", "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name='ddl_vector_documents'", [schema]))
    if checks[-1]["ok"]:
        checks.append(_check_vector_dim(conn, "ddl_vector_documents_dim", schema, "ddl_vector_documents", expected_dim))
    else:
        checks.append({"name": "ddl_vector_documents_dim", "ok": False, "error": "ddl_vector_documents_missing"})
    hist_table = experience_table()
    checks.append(_check_exists(conn, "sql_history_exists", "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", [schema, hist_table]))
    if checks[-1]["ok"]:
        checks.append(_check_vector_dim(conn, "sql_history_dim", schema, hist_table, expected_dim))
    else:
        checks.append({"name": "sql_history_dim", "ok": False, "error": "sql_history_missing"})
    checks.append(
        _check_exists(
            conn,
            "sql_history_fingerprint_index",
            "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND position('sql_fingerprint' in indexdef) > 0",
            [schema, hist_table],
        )
    )
    return {"schema": schema, "ready": all(check["ok"] for check in checks), "checks": checks}


def _check_api_keys(conn) -> dict:
    schema = api_key_table_schema()
    table = api_key_table_name()
    exists = _check_exists(conn, "gateway_api_keys_exists", "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", [schema, table])
    if not exists["ok"]:
        return {"ok": False, "checks": [exists], "school_key_count": 0, "admin_key_count": 0, "policy_key_count": 0}
    with conn.cursor() as cur:
        cur.execute(f'SELECT key_type, COUNT(*) FROM "{schema}"."{table}" WHERE enabled = TRUE GROUP BY key_type')
        counts = {str(row[0] or ""): int(row[1] or 0) for row in cur.fetchall()}
    return {
        "ok": True,
        "checks": [exists],
        "school_key_count": counts.get("school", 0),
        "admin_key_count": counts.get("admin", 0),
        "policy_key_count": counts.get("policy", 0),
    }


def _check_exists(conn, name: str, sql: str, params: list) -> dict:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            ok = cur.fetchone() is not None
        return {"name": name, "ok": ok}
    except Exception as exc:
        _rollback_quietly(conn)
        return {"name": name, "ok": False, "error": str(exc)}


def _check_vector_dim(conn, name: str, schema: str, table: str, expected_dim: int) -> dict:
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT vector_dims(embedding) FROM "{schema}"."{table}" WHERE embedding IS NOT NULL LIMIT 1')
            row = cur.fetchone()
            dim = int(row[0]) if row else expected_dim
        return {"name": name, "ok": dim == expected_dim, "dim": dim}
    except Exception as exc:
        _rollback_quietly(conn)
        return {"name": name, "ok": False, "error": str(exc)}


def _rollback_quietly(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        return


def _render_html(report: dict) -> str:
    rows = []
    for item in report.get("schemas") or []:
        checks = "".join(
            f"<li class={'ok' if c.get('ok') else 'bad'}>{c.get('name')}: {c.get('dim', '')} {c.get('error', '')}</li>"
            for c in item.get("checks") or []
        )
        rows.append(f"<section><h2>{item['schema']} - {'OK' if item['ready'] else 'FAIL'}</h2><ul>{checks}</ul></section>")
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>body{{font-family:Arial,sans-serif;margin:24px}}.ok{{color:#047857}}.bad{{color:#b91c1c}}section{{border:1px solid #ddd;padding:12px;margin:12px 0}}</style></head><body><h1>Gateway Launch Readiness</h1><pre>{json.dumps(report.get('summary'), ensure_ascii=False, indent=2)}</pre><pre>{json.dumps(report.get('api_keys'), ensure_ascii=False, indent=2)}</pre>{''.join(rows)}</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
