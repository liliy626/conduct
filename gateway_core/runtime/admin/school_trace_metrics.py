from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


_EMPTY_METRICS = {
    "total": 0,
    "ok_count": 0,
    "error_count": 0,
    "success_rate": 0.0,
    "avg_duration_ms": 0.0,
    "sql_failure_count": 0,
    "empty_result_count": 0,
    "policy_task_count": 0,
    "tool_call_count": 0,
    "react_repeat_hint_count": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "avg_total_tokens": 0.0,
    "llm_call_count": 0,
}


def build_school_trace_metrics(traces: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_school_traces: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_traces: list[dict[str, Any]] = []
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        all_traces.append(trace)
        school_id = str(trace.get("school_id") or "unknown").strip() or "unknown"
        by_school_traces[school_id].append(trace)

    by_school = {
        school_id: _aggregate_metrics(items)
        for school_id, items in sorted(by_school_traces.items(), key=lambda item: item[0])
    }
    schools = [
        {"school_id": school_id, **metrics}
        for school_id, metrics in sorted(
            by_school.items(),
            key=lambda item: (-int(item[1].get("total") or 0), item[0]),
        )
    ]
    return {
        "overall": _aggregate_metrics(all_traces),
        "by_school": by_school,
        "schools": schools,
    }


def _aggregate_metrics(traces: list[dict[str, Any]]) -> dict[str, Any]:
    if not traces:
        return dict(_EMPTY_METRICS)

    total = len(traces)
    error_count = sum(1 for trace in traces if _trace_has_error(trace))
    duration_total = sum(_trace_duration_ms(trace) for trace in traces)
    return {
        "total": total,
        "ok_count": total - error_count,
        "error_count": error_count,
        "success_rate": _round_ratio((total - error_count) / total),
        "avg_duration_ms": _round_ratio(duration_total / total),
        "sql_failure_count": sum(_trace_sql_failure_count(trace) for trace in traces),
        "empty_result_count": sum(1 for trace in traces if _trace_has_empty_result(trace)),
        "policy_task_count": sum(_trace_policy_task_count(trace) for trace in traces),
        "tool_call_count": sum(_trace_tool_call_count(trace) for trace in traces),
        "react_repeat_hint_count": sum(_trace_react_repeat_hint_count(trace) for trace in traces),
        **_trace_token_metrics(traces),
    }


def build_api_usage_metrics(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate question monitor token usage by gateway API key hash.

    Raw API keys are intentionally never included in monitor logs or this output.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_hash = str(row.get("token_hash") or "unknown").strip() or "unknown"
        bucket = buckets.setdefault(
            token_hash,
            {
                "token_hash": token_hash,
                "request_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "models": {},
                "routes": {},
            },
        )
        bucket["request_count"] += 1
        usage = _usage_from_payload(row.get("usage"))
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            bucket[key] += int(usage.get(key) or 0)
        model_id = str(row.get("model_id") or "unknown").strip() or "unknown"
        route_name = str(row.get("route_name") or row.get("intent_route") or "unknown").strip() or "unknown"
        _add_nested_usage(bucket["models"], model_id, usage)
        _add_nested_usage(bucket["routes"], route_name, usage)
    return sorted(
        buckets.values(),
        key=lambda item: (-int(item.get("total_tokens") or 0), -int(item.get("request_count") or 0), str(item.get("token_hash") or "")),
    )


def build_token_usage_payload(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build standalone token usage metrics for the admin UI."""
    deduped_rows = _dedupe_monitor_rows(rows)
    return {
        "overall": _usage_summary(deduped_rows),
        "by_api": _aggregate_monitor_rows(deduped_rows, key_name="token_hash", default="unknown"),
        "by_user": _aggregate_monitor_rows(deduped_rows, key_name="user_id", default="anonymous"),
        "by_model": _aggregate_monitor_rows(deduped_rows, key_name="model_id", default="unknown"),
        "by_route": _aggregate_monitor_rows(deduped_rows, key_name="route_name", fallback_key="intent_route", default="unknown"),
        "recent": _recent_usage_rows(deduped_rows, limit=200),
    }


def extract_trace_token_usage(trace: dict[str, Any]) -> dict[str, Any]:
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    by_model: dict[str, dict[str, int]] = {}
    llm_call_count = 0
    for step in _trace_steps(trace):
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        step_usage = _usage_from_payload(output.get("usage"))
        if step_usage["total_tokens"] <= 0:
            continue
        llm_call_count += 1
        for key in usage:
            usage[key] += int(step_usage.get(key) or 0)
        step_input = step.get("input") if isinstance(step.get("input"), dict) else {}
        model_name = str(output.get("model_name") or step_input.get("model_name") or "")
        model_name = model_name.strip() or "unknown"
        _add_nested_usage(by_model, model_name, step_usage)
    return {**usage, "llm_call_count": llm_call_count, "by_model": by_model}


def _trace_steps(trace: dict[str, Any]) -> list[dict[str, Any]]:
    steps = trace.get("steps")
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _trace_token_metrics(traces: list[dict[str, Any]]) -> dict[str, Any]:
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    llm_call_count = 0
    for trace in traces:
        usage = extract_trace_token_usage(trace)
        for key in total_usage:
            total_usage[key] += int(usage.get(key) or 0)
        llm_call_count += int(usage.get("llm_call_count") or 0)
    avg_total = (total_usage["total_tokens"] / len(traces)) if traces else 0.0
    return {
        **total_usage,
        "avg_total_tokens": _round_ratio(avg_total),
        "llm_call_count": llm_call_count,
    }


def _usage_from_payload(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(_number(raw.get("prompt_tokens", raw.get("input_tokens", 0))))
    completion_tokens = int(_number(raw.get("completion_tokens", raw.get("output_tokens", 0))))
    total_tokens = int(_number(raw.get("total_tokens")))
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": max(0, total_tokens),
    }


def _add_nested_usage(target: dict[str, Any], name: str, usage: dict[str, int]) -> None:
    item = target.setdefault(
        name,
        {"request_count": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    item["request_count"] += 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        item[key] += int(usage.get(key) or 0)


def _dedupe_monitor_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_completion: dict[str, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        completion_id = str(row.get("completion_id") or "").strip()
        if not completion_id:
            anonymous.append(row)
            continue
        previous = by_completion.get(completion_id)
        if previous is None or _monitor_row_rank(row) >= _monitor_row_rank(previous):
            by_completion[completion_id] = row
    out = list(by_completion.values()) + anonymous
    return sorted(out, key=lambda item: float(_number(item.get("ts"))), reverse=True)


def _monitor_row_rank(row: dict[str, Any]) -> tuple[int, float]:
    usage = _usage_from_payload(row.get("usage"))
    has_usage = 1 if usage["total_tokens"] > 0 else 0
    is_final = 1 if str(row.get("response_mode") or "") == "stream_final" else 0
    return (has_usage, is_final, _number(row.get("ts")))


def _usage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in rows:
        row_usage = _usage_from_payload(row.get("usage"))
        for key in usage:
            usage[key] += int(row_usage.get(key) or 0)
    request_count = len(rows)
    return {
        "request_count": request_count,
        **usage,
        "avg_total_tokens": _round_ratio(usage["total_tokens"] / request_count) if request_count else 0.0,
    }


def _aggregate_monitor_rows(
    rows: list[dict[str, Any]],
    *,
    key_name: str,
    default: str,
    fallback_key: str = "",
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw_key = row.get(key_name)
        if (raw_key is None or str(raw_key).strip() == "") and fallback_key:
            raw_key = row.get(fallback_key)
        key = str(raw_key or default).strip() or default
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "request_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        )
        bucket["request_count"] += 1
        usage = _usage_from_payload(row.get("usage"))
        for usage_key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            bucket[usage_key] += int(usage.get(usage_key) or 0)
    for bucket in buckets.values():
        count = int(bucket.get("request_count") or 0)
        bucket["avg_total_tokens"] = _round_ratio((int(bucket.get("total_tokens") or 0) / count) if count else 0.0)
    return sorted(
        buckets.values(),
        key=lambda item: (-int(item.get("total_tokens") or 0), -int(item.get("request_count") or 0), str(item.get("key") or "")),
    )


def _recent_usage_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows[: max(1, limit)]:
        usage = _usage_from_payload(row.get("usage"))
        out.append(
            {
                "ts": row.get("ts"),
                "completion_id": row.get("completion_id"),
                "question": row.get("question"),
                "token_hash": row.get("token_hash"),
                "user_id": row.get("user_id") or "anonymous",
                "model_id": row.get("model_id") or "unknown",
                "route_name": row.get("route_name") or row.get("intent_route") or "unknown",
                "status": row.get("status"),
                "response_mode": row.get("response_mode"),
                "usage": usage,
                "e2e_ms": row.get("e2e_ms"),
                "first_token_ms": row.get("first_token_ms"),
            }
        )
    return out


def _trace_has_error(trace: dict[str, Any]) -> bool:
    for step in _trace_steps(trace):
        if str(step.get("status") or "").lower() == "error":
            return True
        if str(step.get("error") or "").strip():
            return True
        output = step.get("output")
        if isinstance(output, dict):
            if str(output.get("error") or "").strip():
                return True
            if output.get("ok") is False:
                return True
    return bool(trace.get("has_error"))


def _trace_duration_ms(trace: dict[str, Any]) -> float:
    total = 0.0
    for step in _trace_steps(trace):
        total += _number(step.get("duration_ms"))
    return total


def _trace_sql_failure_count(trace: dict[str, Any]) -> int:
    count = 0
    for step in _trace_steps(trace):
        name = str(step.get("name") or "").lower()
        if not _is_sql_related_step(name):
            continue
        if _step_has_error(step):
            count += 1
    return count


def _trace_has_empty_result(trace: dict[str, Any]) -> bool:
    counts: list[float] = []
    for step in _trace_steps(trace):
        if not _is_result_step(str(step.get("name") or "").lower()):
            continue
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        for key in ("row_count", "chunk_count", "record_count"):
            if key in output:
                counts.append(_number(output.get(key)))
                break
    return bool(counts) and all(value <= 0 for value in counts)


def _trace_policy_task_count(trace: dict[str, Any]) -> int:
    step_count = 0
    summary_count = 0
    evidence_count = 0
    for step in _trace_steps(trace):
        name = str(step.get("name") or "").lower()
        if "policy" in name and ("search" in name or name.startswith("tool.")):
            step_count += 1
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        payload_summary = output.get("payload_summary")
        if isinstance(payload_summary, dict):
            summary_count = max(summary_count, int(_number(payload_summary.get("policy_task_count"))))
        evidence_by_task = output.get("evidence_by_task")
        if isinstance(evidence_by_task, dict):
            evidence_count = max(evidence_count, _count_official_policy_tasks(evidence_by_task))
    return max(step_count, summary_count, evidence_count)


def _trace_tool_call_count(trace: dict[str, Any]) -> int:
    count = 0
    for step in _trace_steps(trace):
        name = str(step.get("name") or "").lower()
        if ".tool." in name or name.startswith("tool."):
            count += 1
    return count


def _trace_react_repeat_hint_count(trace: dict[str, Any]) -> int:
    suggestion_count = 0
    summary_count = 0
    board_count = 0
    for step in _trace_steps(trace):
        output = step.get("output")
        if not isinstance(output, dict):
            continue
        name = str(step.get("name") or "").lower()
        if name == "evidence_board.suggest_related_queries":
            suggestion_count += int(_number(output.get("suggestion_count")))
        payload_summary = output.get("payload_summary")
        if isinstance(payload_summary, dict):
            for key in ("related_query_count", "unexplored_clue_count", "react_repeat_hint_count"):
                summary_count += int(_number(payload_summary.get(key)))
        evidence_board = output.get("evidence_board")
        if isinstance(evidence_board, dict):
            board_count = max(
                board_count,
                _count_collection_items(
                    evidence_board,
                    ("related_queries", "suggested_related_queries", "unexplored_clues", "related_queries_executed"),
                ),
            )
    return suggestion_count + max(summary_count, board_count)


def _step_has_error(step: dict[str, Any]) -> bool:
    if str(step.get("status") or "").lower() == "error":
        return True
    if str(step.get("error") or "").strip():
        return True
    output = step.get("output")
    if isinstance(output, dict):
        return bool(str(output.get("error") or "").strip() or output.get("ok") is False)
    return False


def _is_sql_related_step(name: str) -> bool:
    return (
        "sql" in name
        or "db.execute" in name
        or name.endswith(".execute_tenant_query")
    )


def _is_result_step(name: str) -> bool:
    if "context.build" in name:
        return False
    return (
        "sql_db_query" in name
        or "official_policy_search" in name
        or "web_search" in name
        or "chart" in name
        or "image" in name
        or "slide" in name
    )


def _count_official_policy_tasks(evidence_by_task: dict[str, Any]) -> int:
    return sum(
        1
        for item in evidence_by_task.values()
        if isinstance(item, dict) and str(item.get("source") or "") == "official_policy"
    )


def _count_collection_items(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    count = 0
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            count += len(value)
        elif isinstance(value, dict):
            count += len(value)
    return count


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _round_ratio(value: float) -> float:
    return round(float(value or 0.0), 3)
