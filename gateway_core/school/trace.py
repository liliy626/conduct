from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import time
import uuid
from typing import Any, Iterator

from gateway_core.infra.utils import env_value as _env_value


@dataclass
class SchoolTraceStep:
    name: str
    status: str = "ok"
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0


@dataclass
class SchoolTrace:
    trace_id: str
    question: str
    school_id: str = ""
    steps: list[SchoolTraceStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


TenantTraceStep = SchoolTraceStep
TenantTrace = SchoolTrace


_TRACE_STORE: OrderedDict[str, SchoolTrace] = OrderedDict()


def tenant_trace_enabled() -> bool:
    return _truthy_env("SCHOOL_TRACE_ENABLED", "TENANT_TRACE_ENABLED", "1")


def include_sql() -> bool:
    return _truthy_env("SCHOOL_TRACE_INCLUDE_SQL", "TENANT_TRACE_INCLUDE_SQL", "0")


def include_rows() -> bool:
    return _truthy_env("SCHOOL_TRACE_INCLUDE_ROWS", "TENANT_TRACE_INCLUDE_ROWS", "0")


def debug_trace_enabled() -> bool:
    return _truthy_env("GATEWAY_DEBUG_TRACE", "SCHOOL_DEBUG_TRACE", "0") or _truthy_env(
        "SCHOOL_TRACE_DEBUG", "TENANT_TRACE_DEBUG", "0"
    )


def trace_preview(value: Any, *, max_chars: int | None = None) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    limit = max_chars if max_chars is not None else trace_preview_chars()
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}...<truncated {len(text) - limit} chars>"


def trace_preview_chars() -> int:
    default = "12000" if debug_trace_enabled() else "2000"
    raw = _env_value("GATEWAY_TRACE_PREVIEW_CHARS", "SCHOOL_TRACE_PREVIEW_CHARS", default)
    try:
        return max(200, min(int(raw), 50000))
    except Exception:
        return int(default)


def max_rows() -> int:
    raw = _env_value("SCHOOL_TRACE_MAX_ROWS", "TENANT_TRACE_MAX_ROWS", "20")
    try:
        return max(0, int(raw))
    except Exception:
        return 20


def new_school_trace(question: str) -> SchoolTrace | None:
    if not tenant_trace_enabled():
        return None
    return SchoolTrace(trace_id=uuid.uuid4().hex, question=str(question or ""))


new_tenant_trace = new_school_trace


@contextmanager
def trace_step(trace: SchoolTrace | None, name: str, input_payload: dict[str, Any] | None = None) -> Iterator[SchoolTraceStep | None]:
    if trace is None:
        yield None
        return
    step = SchoolTraceStep(name=name, input=_sanitize(input_payload or {}))
    start = time.perf_counter()
    step.started_at = time.time()
    try:
        yield step
    except Exception as exc:
        step.status = "error"
        step.error = str(exc)
        raise
    finally:
        step.ended_at = time.time()
        step.duration_ms = int((time.perf_counter() - start) * 1000)
        step.input = _sanitize(step.input)
        step.output = _sanitize(step.output)
        step.error = _mask_text(step.error)
        trace.steps.append(step)


def set_step_output(step: SchoolTraceStep | None, output: dict[str, Any]) -> None:
    if step is not None:
        step.output = output


def finish_trace(trace: SchoolTrace | None) -> None:
    if trace is None:
        return
    _purge_expired()
    limit = _store_limit()
    _TRACE_STORE[trace.trace_id] = trace
    _TRACE_STORE.move_to_end(trace.trace_id)
    while len(_TRACE_STORE) > limit:
        _TRACE_STORE.popitem(last=False)
    _append_trace_log(trace)
    from gateway_core.observability.trace_exporters import export_school_trace_to_observability

    export_school_trace_to_observability(trace)


def get_trace(trace_id: str, school_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any] | None:
    _purge_expired()
    clean_trace_id = str(trace_id or "").strip()
    clean_school_id = str(school_id or tenant_id or "").strip()
    trace = _TRACE_STORE.get(clean_trace_id)
    if trace is not None and clean_school_id and trace.school_id != clean_school_id:
        return None
    if trace:
        return _trace_to_dict(trace)
    persisted = _read_persisted_trace(clean_trace_id)
    if persisted is not None and clean_school_id and _trace_school_id(persisted) != clean_school_id:
        return None
    return persisted


def recent_traces(limit: int = 50, school_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    _purge_expired()
    clean_limit = max(1, min(int(limit or 50), 200))
    clean_school_id = str(school_id or tenant_id or "").strip()
    summaries_by_id: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for trace in _TRACE_STORE.values():
        if clean_school_id and trace.school_id != clean_school_id:
            continue
        summaries_by_id[trace.trace_id] = _trace_summary(trace)
    for item in _read_recent_persisted_traces(limit=clean_limit * 3, school_id=clean_school_id):
        trace_id = str(item.get("trace_id") or "")
        if trace_id and trace_id not in summaries_by_id:
            summaries_by_id[trace_id] = _trace_summary_from_dict(item)
    items = sorted(summaries_by_id.values(), key=lambda item: float(item.get("created_at") or 0), reverse=True)[:clean_limit]
    return {"items": items, "count": len(items)}


def _trace_to_dict(trace: SchoolTrace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "question": trace.question,
        "school_id": trace.school_id,
        "created_at": trace.created_at,
        "steps": [
            {
                "name": step.name,
                "status": step.status,
                "input": step.input,
                "output": step.output,
                "error": step.error,
                "duration_ms": step.duration_ms,
                "started_at": step.started_at,
                "ended_at": step.ended_at,
                "start_offset_ms": _offset_ms(trace.created_at, step.started_at),
                "end_offset_ms": _offset_ms(trace.created_at, step.ended_at),
            }
            for step in trace.steps
        ],
    }


def build_trace_timing(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    created_at = _float(trace.get("created_at"))
    timeline: list[dict[str, Any]] = []
    by_category: dict[str, dict[str, Any]] = {}
    cumulative_offset_ms = 0
    max_end_offset_ms = 0
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        duration_ms = _int(step.get("duration_ms"))
        start_offset_ms = _step_offset_ms(step, "start_offset_ms", "started_at", created_at, cumulative_offset_ms)
        end_offset_ms = _step_offset_ms(step, "end_offset_ms", "ended_at", created_at, start_offset_ms + duration_ms)
        if end_offset_ms < start_offset_ms:
            end_offset_ms = start_offset_ms + duration_ms
        cumulative_offset_ms = max(cumulative_offset_ms, end_offset_ms)
        max_end_offset_ms = max(max_end_offset_ms, end_offset_ms)
        category = _step_category(str(step.get("name") or ""))
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        row_count = _first_not_none(output.get("row_count"), output.get("doc_count"), output.get("table_count"))
        source_count = _source_count(output)
        item = {
            "index": idx,
            "name": str(step.get("name") or ""),
            "category": category,
            "status": str(step.get("status") or "ok"),
            "start_offset_ms": start_offset_ms,
            "end_offset_ms": end_offset_ms,
            "duration_ms": duration_ms,
            "first_token_ms": output.get("first_token_ms"),
            "stream_chunk_count": output.get("stream_chunk_count"),
            "row_count": row_count,
            "source_count": source_count,
            "error": str(step.get("error") or output.get("error") or ""),
        }
        timeline.append(item)
        summary = by_category.setdefault(category, {"count": 0, "duration_ms": 0, "max_duration_ms": 0})
        summary["count"] += 1
        summary["duration_ms"] += duration_ms
        summary["max_duration_ms"] = max(summary["max_duration_ms"], duration_ms)
    return {
        "total_observed_ms": max_end_offset_ms or sum(item["duration_ms"] for item in timeline),
        "step_count": len(timeline),
        "by_category": by_category,
        "timeline": timeline,
    }


def _offset_ms(created_at: float, value: float) -> int:
    if not created_at or not value:
        return 0
    return max(0, int(round((float(value) - float(created_at)) * 1000)))


def _step_offset_ms(step: dict[str, Any], offset_key: str, absolute_key: str, created_at: float, fallback: int) -> int:
    if step.get(offset_key) is not None:
        return _int(step.get(offset_key))
    absolute = _float(step.get(absolute_key))
    if created_at and absolute:
        return _offset_ms(created_at, absolute)
    return max(0, int(fallback or 0))


def _step_category(name: str) -> str:
    text = str(name or "").lower()
    if text.endswith(".llm") or ".llm" in text:
        return "llm"
    if "web_search" in text:
        return "web_search"
    if "sql_db_query" in text:
        return "sql"
    if "ddl_search" in text or "inspect_table_schema" in text or "sample_table_rows" in text:
        return "ddl"
    if ".tool." in text or "tool." in text:
        return "tool"
    if "context" in text:
        return "context"
    return "other"


def _source_count(output: dict[str, Any]) -> int:
    sources = output.get("sources")
    if isinstance(sources, list):
        return len(sources)
    artifacts = output.get("artifacts")
    if isinstance(artifacts, list):
        count = 0
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            content = artifact.get("content") if isinstance(artifact.get("content"), dict) else {}
            results = content.get("results")
            if isinstance(results, list):
                count += len(results)
        return count
    return 0


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _int(value: Any) -> int:
    try:
        return int(round(float(value or 0)))
    except Exception:
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _trace_summary(trace: SchoolTrace) -> dict[str, Any]:
    last_step = trace.steps[-1].name if trace.steps else ""
    has_error = any(step.status == "error" or step.error for step in trace.steps)
    return {
        "trace_id": trace.trace_id,
        "question": trace.question,
        "school_id": trace.school_id,
        "created_at": trace.created_at,
        "step_count": len(trace.steps),
        "last_step": last_step,
        "has_error": has_error,
    }


def _trace_summary_from_dict(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    last_step = ""
    if steps and isinstance(steps[-1], dict):
        last_step = str(steps[-1].get("name") or "")
    has_error = any(
        isinstance(step, dict) and (str(step.get("status") or "") == "error" or bool(step.get("error")))
        for step in steps
    )
    return {
        "trace_id": str(trace.get("trace_id") or ""),
        "question": str(trace.get("question") or ""),
        "school_id": _trace_school_id(trace),
        "created_at": float(trace.get("created_at") or 0),
        "step_count": len(steps),
        "last_step": last_step,
        "has_error": has_error,
    }


def _trace_school_id(trace: dict[str, Any]) -> str:
    return str(trace.get("school_id") or trace.get("tenant_id") or "").strip()


def _append_trace_log(trace: SchoolTrace) -> None:
    if not _truthy_env("SCHOOL_TRACE_PERSIST_ENABLED", "TENANT_TRACE_PERSIST_ENABLED", "1"):
        return
    path = _trace_log_path(trace.created_at)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_trace_to_dict(trace), ensure_ascii=False, default=str))
            handle.write("\n")
    except Exception:
        return


def _read_persisted_trace(trace_id: str) -> dict[str, Any] | None:
    if not trace_id:
        return None
    for path in _trace_log_files_newest_first():
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    payload = _parse_jsonl_line(line)
                    if payload and str(payload.get("trace_id") or "") == trace_id:
                        return payload
        except Exception:
            continue
    return None


def _read_recent_persisted_traces(*, limit: int, school_id: str = "") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    clean_school_id = str(school_id or "").strip()
    for path in _trace_log_files_newest_first():
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    payload = _parse_jsonl_line(line)
                    if not payload:
                        continue
                    if clean_school_id and _trace_school_id(payload) != clean_school_id:
                        continue
                    rows.append(payload)
        except Exception:
            continue
        rows.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        out.extend(rows)
        if len(out) >= limit:
            break
    out.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    return out[: max(1, int(limit or 1))]


def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
    clean = str(line or "").strip()
    if not clean:
        return None
    try:
        payload = json.loads(clean)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _trace_log_files_newest_first() -> list[Path]:
    root = _trace_log_dir()
    try:
        return sorted([*root.glob("school_trace_*.jsonl"), *root.glob("tenant_trace_*.jsonl")], reverse=True)
    except Exception:
        return []


def _trace_log_path(created_at: float) -> Path:
    day = time.strftime("%Y-%m-%d", time.localtime(float(created_at or time.time())))
    return _trace_log_dir() / f"school_trace_{day}.jsonl"


def _trace_log_dir() -> Path:
    raw = _env_value("SCHOOL_TRACE_LOG_DIR", "TENANT_TRACE_LOG_DIR", "logs/school_trace")
    return Path(raw)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize_nested(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        return _mask_text(value)
    return value


def _sanitize_nested(key: str, value: Any) -> Any:
    if key in {"sql", "compiled_sql", "raw_sql"} and not include_sql():
        return "<hidden>"
    if key in {"input_preview", "output_preview"} and not include_sql() and _contains_sql(value):
        return "<hidden>"
    if key in {
        "columns",
        "fields",
        "field_labels",
        "source_views",
        "referenced_views",
        "dataset_id",
        "dataset_label",
        "source_view",
        "source_field",
        "field_id",
        "label",
        "table_name",
        "schema_name",
    }:
        return value
    if key in {"rows", "raw_rows"}:
        if not include_rows():
            return "<hidden>"
        if isinstance(value, list):
            return [_sanitize(item) for item in value[: max_rows()]]
    if key in {"params", "masked_params"}:
        return [_sanitize(item) for item in value] if isinstance(value, list) else _sanitize(value)
    return _sanitize(value)


def _contains_sql(value: Any) -> bool:
    text = str(value or "")
    return bool(re.search(r"(?is)\b(select|with)\b.+\bfrom\b", text))


def _mask_text(text: str) -> str:
    clean = str(text or "")
    if not clean:
        return clean
    if len(clean) >= 11 and clean.isdigit():
        return f"{clean[:3]}****{clean[-4:]}"
    if 2 <= len(clean) <= 4 and all("\u4e00" <= char <= "\u9fff" for char in clean):
        return f"{clean[:1]}***{clean[-1:]}"
    return clean


def _purge_expired() -> None:
    ttl = _ttl_seconds()
    if ttl <= 0:
        return
    cutoff = time.time() - ttl
    expired = [trace_id for trace_id, trace in _TRACE_STORE.items() if trace.created_at < cutoff]
    for trace_id in expired:
        _TRACE_STORE.pop(trace_id, None)


def _ttl_seconds() -> int:
    raw = _env_value("SCHOOL_TRACE_TTL_SECONDS", "TENANT_TRACE_TTL_SECONDS", "3600")
    try:
        return max(0, int(raw))
    except Exception:
        return 3600


def _store_limit() -> int:
    raw = _env_value("SCHOOL_TRACE_STORE_LIMIT", "TENANT_TRACE_STORE_LIMIT", "200")
    try:
        return max(10, min(int(raw), 1000))
    except Exception:
        return 200


def _truthy_env(name: str, legacy: str = "", default: str = "0") -> bool:
    return _env_value(name, legacy, default).lower() in {"1", "true", "yes", "on"}
