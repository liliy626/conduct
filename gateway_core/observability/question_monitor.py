"""
question_monitor.py — 问题监控日志

迁出自 app.py，包含：
- question_monitor 配置 / 写入 / 读取 / 导出函数
- 上下文源解析辅助（_extract_context_source）
- JSONL 写入工具 _write_jsonl
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from gateway_core.observability.log_policy import (
    maybe_cleanup_jsonl_logs,
    observability_log_dir,
)
from gateway_core.runtime.gateway_config import truthy_env as _truthy_env

LOGGER = logging.getLogger("gateway.monitor")

# 仓库根目录（.../yili）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_RETENTION_PREFIXES = (
    "question_monitor_",
    "question_route_monitor_",
    "openwebui_question_monitor",
)

def _format_exception_for_log(err: Exception) -> str:
    base = err.__class__.__name__
    if isinstance(err, OSError):
        parts = [base]
        if getattr(err, "errno", None) is not None:
            parts.append(f"errno={err.errno}")
        if getattr(err, "strerror", None):
            parts.append(f"msg={err.strerror}")
        if getattr(err, "filename", None):
            parts.append(f"path={err.filename}")
        return " ".join(parts)
    text = str(err).strip()
    return f"{base}: {text}" if text else base


def _today_text() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _question_monitor_log_dir() -> Path:
    path = observability_log_dir(PROJECT_ROOT)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────
# Question Monitor — 配置
# ─────────────────────────────────────────────

def _question_monitor_enabled() -> bool:
    return _truthy_env("QUESTION_MONITOR_ENABLED", "1")


def _question_monitor_mode() -> str:
    raw = os.getenv("QUESTION_MONITOR_MODE", "full").strip().lower()
    if raw in {"full", "question_only"}:
        return raw
    return "full"


def _question_monitor_log_path(day_text: Optional[str] = None) -> Path:
    return _question_monitor_log_dir() / f"question_monitor_{day_text or _today_text()}.jsonl"


def _question_monitor_log_paths() -> List[Path]:
    log_dir = _question_monitor_log_dir()
    daily_paths = sorted(log_dir.glob("question_monitor_*.jsonl"))
    if daily_paths:
        return daily_paths
    # 兼容历史单文件。
    legacy = log_dir / "openwebui_question_monitor.jsonl"
    return [legacy] if legacy.exists() else []


def _question_monitor_daily_merged_path() -> Path:
    return _question_monitor_log_dir() / "question_monitor_daily_merged.jsonl"


def _question_route_log_path() -> Path:
    return _question_monitor_log_dir() / f"question_route_monitor_{_today_text()}.jsonl"


def _question_monitor_max_text_len() -> int:
    raw = os.getenv("QUESTION_MONITOR_MAX_TEXT_LEN", "2000").strip()
    try:
        return max(200, int(raw))
    except Exception:
        return 2000


# ─────────────────────────────────────────────
# Question Monitor — 辅助工具
# ─────────────────────────────────────────────

def _clip_monitor_text(value: Any) -> str:
    text = str(value or "").strip()
    limit = _question_monitor_max_text_len()
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _hash_token_for_monitor(token: str) -> str:
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _extract_context_source(ctx: Optional[str]) -> Optional[str]:
    text = str(ctx or "")
    if not text:
        return None
    m = re.search(r'"source"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    if "检索结果(JSON)" in text:
        return "context_json"
    return "context_text"


# ─────────────────────────────────────────────
# Question Monitor — JSONL 写入 / 读取 / 导出
# ─────────────────────────────────────────────

def _write_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _write_question_route_event(payload: Dict[str, Any]) -> None:
    if not _question_monitor_enabled():
        return
    try:
        route_payload = {
            "ts": payload.get("ts"),
            "completion_id": payload.get("completion_id"),
            "question": payload.get("question"),
            "last_question": payload.get("last_question"),
            "intent_route": payload.get("intent_route"),
            "predicted_domain": payload.get("predicted_domain"),
            "general_knowledge_subtype": payload.get("general_knowledge_subtype"),
            "route_name": payload.get("route_name"),
            "context_source": payload.get("context_source"),
            "context_present": payload.get("context_present"),
            "model_id": payload.get("model_id"),
            "stream": payload.get("stream"),
            "school_scope": payload.get("school_scope"),
            "viewer_role": payload.get("viewer_role"),
            "user_id": payload.get("user_id"),
            "incoming_x_header_keys": payload.get("incoming_x_header_keys"),
            "incoming_user_header_values": payload.get("incoming_user_header_values"),
            "authorization_present": payload.get("authorization_present"),
            "status": payload.get("status"),
            "response_mode": payload.get("response_mode"),
            "cache_hit": payload.get("cache_hit"),
            "gk_protocol_violation": payload.get("gk_protocol_violation"),
            "answer_text_rule_hits": payload.get("answer_text_rule_hits"),
            "error_type": payload.get("error_type"),
            "error_status_code": payload.get("error_status_code"),
        }
        _write_jsonl(_question_route_log_path(), route_payload)
    except Exception as e:
        LOGGER.warning(
            "question_route_monitor_write_failed err=%s",
            _format_exception_for_log(e),
        )


def _write_question_monitor_event(payload: Dict[str, Any]) -> None:
    if not _question_monitor_enabled():
        return
    try:
        maybe_cleanup_jsonl_logs(
            log_dir=_question_monitor_log_dir(),
            prefixes=_LOG_RETENTION_PREFIXES,
            retention_days=7,
        )
        path = _question_monitor_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            if _question_monitor_mode() == "full":
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            else:
                question = _clip_monitor_text(payload.get("question", ""))
                if not question:
                    return
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(payload.get("ts") or time.time())))
                f.write(f"{ts}\t{question}\n")
        if _question_monitor_mode() == "full":
            _write_question_route_event(payload)
    except Exception as e:
        LOGGER.warning(
            "question_monitor_write_failed err=%s",
            _format_exception_for_log(e),
        )


def _read_question_monitor_recent(limit: int = 50) -> List[Dict[str, Any]]:
    rows = _read_question_monitor_all()
    return rows[-max(1, limit):]


def _read_question_monitor_all() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in _question_monitor_log_paths():
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                if _question_monitor_mode() == "full":
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
                    continue
                if "\t" in line:
                    ts, question = line.split("\t", 1)
                    out.append({"ts_text": ts.strip(), "question": question.strip()})
                else:
                    out.append({"question": line})
    return out


def _normalize_day_text(day: str) -> str:
    text = (day or "").strip()
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD")
    return text


def _question_monitor_day_text(row: Dict[str, Any]) -> str:
    try:
        ts = int(row.get("ts") or 0)
    except Exception:
        ts = 0
    if ts > 0:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    ts_text = str(row.get("ts_text", "")).strip()
    if len(ts_text) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts_text[:10]):
        return ts_text[:10]
    return "unknown"


def _export_question_monitor_daily_merged(day: str = "") -> Dict[str, Any]:
    target_day = _normalize_day_text(day)
    rows = _read_question_monitor_all()
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        day_text = _question_monitor_day_text(row)
        if target_day and day_text != target_day:
            continue
        buckets.setdefault(day_text, []).append(row)

    output_path = _question_monitor_daily_merged_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_rows = 0
    with output_path.open("w", encoding="utf-8") as f:
        for day_key in sorted(buckets.keys()):
            day_rows = buckets[day_key]
            total_rows += len(day_rows)
            f.write(
                json.dumps(
                    {
                        "date": day_key,
                        "count": len(day_rows),
                        "rows": day_rows,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    return {
        "output_path": str(output_path),
        "day": target_day or None,
        "days": len(buckets),
        "rows": total_rows,
    }
