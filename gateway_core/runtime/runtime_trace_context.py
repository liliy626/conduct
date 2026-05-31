from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Optional

ROUTE_NAME_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("route_name", default="")
TRACE_USAGE_CTX: contextvars.ContextVar[Dict[str, int]] = contextvars.ContextVar("trace_usage", default={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
CONTEXT_STAGE_TRACE_CTX: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("context_stage_trace", default={"stages": []})

def _set_route_name(name: str) -> None:
    """把当前请求最终命中的路由名记到上下文里。

    后面写日志、做监控、给 dashboard 展示时，都会读这个值。
    """
    ROUTE_NAME_CTX.set((name or "").strip())


def _current_route_name() -> str:
    """读取当前请求已经记录下来的路由名。"""
    return (ROUTE_NAME_CTX.get() or "").strip()


def _reset_context_stage_trace() -> None:
    """清空本次请求的“上下文构建过程”打点记录。"""
    CONTEXT_STAGE_TRACE_CTX.set({"stages": []})


def _ensure_context_stage_trace() -> Dict[str, Any]:
    """确保上下文打点容器一定是可写的字典结构。"""
    trace = CONTEXT_STAGE_TRACE_CTX.get()
    if not isinstance(trace, dict):
        trace = {"stages": []}
        CONTEXT_STAGE_TRACE_CTX.set(trace)
    if not isinstance(trace.get("stages"), list):
        trace["stages"] = []
    return trace


def _record_context_stage(
    stage: str,
    *,
    elapsed_ms: float,
    status: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """记录某个上下文阶段的耗时和结果。

    例如 data_availability、orchestrator、fallback 是否命中，
    方便排查“到底卡在哪一层”。
    """
    trace = _ensure_context_stage_trace()
    item: Dict[str, Any] = {
        "stage": str(stage or "").strip(),
        "elapsed_ms": round(max(float(elapsed_ms or 0.0), 0.0), 1),
        "status": str(status or "").strip() or "ok",
    }
    if extra:
        for key, value in extra.items():
            if value is not None:
                item[key] = value
    trace["stages"].append(item)


def _current_context_stage_trace() -> Dict[str, Any]:
    """取出当前请求的阶段打点，并顺手汇总总耗时。"""
    trace = CONTEXT_STAGE_TRACE_CTX.get()
    if not isinstance(trace, dict):
        return {}
    stages = trace.get("stages")
    if not isinstance(stages, list):
        return {}
    out_stages: List[Dict[str, Any]] = []
    for stage in stages:
        if isinstance(stage, dict):
            out_stages.append(dict(stage))
    total_ms = round(sum(float(item.get("elapsed_ms") or 0.0) for item in out_stages), 1)
    return {"stages": out_stages, "total_measured_ms": total_ms}


def _zero_usage() -> Dict[str, int]:
    """返回一个全 0 的 token 用量结构。"""
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _normalize_usage(raw: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """把上游模型返回的 usage 统一整理成稳定格式。

    这个函数的作用是“兜底”，避免某些字段缺失或类型不对时把主流程搞崩。
    """
    if not isinstance(raw, dict):
        return _zero_usage()
    try:
        prompt_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    except Exception:
        prompt_tokens = 0
    try:
        completion_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    except Exception:
        completion_tokens = 0
    try:
        total_tokens = int(raw.get("total_tokens", 0) or 0)
    except Exception:
        total_tokens = prompt_tokens + completion_tokens
    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": max(0, total_tokens),
    }


def _set_trace_usage(raw: Optional[Dict[str, Any]]) -> None:
    """把本次请求的 token 用量写入上下文。"""
    TRACE_USAGE_CTX.set(_normalize_usage(raw))


def _current_trace_usage() -> Dict[str, int]:
    """读取当前请求已经累计的 token 用量。"""
    return _normalize_usage(TRACE_USAGE_CTX.get())


def _add_trace_usage(raw: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """把一段新的 token 用量累加到当前请求上。"""
    current = _current_trace_usage()
    extra = _normalize_usage(raw)
    merged = {
        "prompt_tokens": current["prompt_tokens"] + extra["prompt_tokens"],
        "completion_tokens": current["completion_tokens"] + extra["completion_tokens"],
        "total_tokens": current["total_tokens"] + extra["total_tokens"],
    }
    _set_trace_usage(merged)
    return merged
