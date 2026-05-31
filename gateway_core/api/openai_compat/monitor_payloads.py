from __future__ import annotations

from typing import Any, Callable


ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def response_mode_for_text(*, stream: bool, hint: str = "") -> str:
    if str(hint or "").strip().lower() == "intro":
        return "stream_intro" if stream else "non_stream_intro"
    return "stream" if stream else "non_stream"


def build_request_monitor_base(
    *,
    request: Any,
    header_probe: dict[str, Any],
    completion_id: str,
    model_id: str,
    stream: bool,
    school_scope: str | None,
    x_school_scope: str | None,
    token: str,
    current_viewer_role: str | None,
    current_user_id: str | None,
    question: str,
    last_question: str,
    intent_route: str,
    clip_monitor_text_fn: Callable[[Any], str],
    hash_token_for_monitor_fn: Callable[[str], str],
    now_ts_fn: Callable[[], int],
    predicted_domain: str | None = None,
    general_knowledge_subtype: str | None = None,
    route_name: str | None = None,
    context_source: str | None = None,
    context_present: bool | None = None,
    cache_hit: bool | None = None,
    stream_downgraded: bool | None = None,
) -> dict[str, Any]:
    payload = {
        "ts": now_ts_fn(),
        "completion_id": completion_id,
        "model_id": model_id,
        "stream": bool(stream),
        "school_scope": school_scope,
        "x_school_scope": x_school_scope,
        "client_ip": (request.client.host if getattr(request, "client", None) else None),
        "user_agent": clip_monitor_text_fn(getattr(request, "headers", {}).get("user-agent", "")),
        **header_probe,
        "token_hash": hash_token_for_monitor_fn(token),
        "viewer_role": current_viewer_role,
        "user_id": current_user_id,
        "question": clip_monitor_text_fn(question),
        "last_question": clip_monitor_text_fn(last_question),
        "intent_route": intent_route,
    }
    optional_fields = {
        "predicted_domain": predicted_domain,
        "general_knowledge_subtype": general_knowledge_subtype,
        "route_name": route_name,
        "context_source": context_source,
        "context_present": context_present,
        "cache_hit": cache_hit,
        "stream_downgraded": stream_downgraded,
    }
    for key, value in optional_fields.items():
        if value is not None:
            payload[key] = value
    return payload


def build_text_response_monitor_pair(
    *,
    base: dict[str, Any],
    intent_route: str | None = None,
    route_name: str | None = None,
    context_source: Any = None,
    context_present: bool = False,
    status: str = "ok",
    final_status: str = "ok",
    response_mode: str,
    usage: dict[str, Any] | None,
    answer: str,
    monitor_answer_preview_fn: Callable[[Any], str],
    first_extra: dict[str, Any] | None = None,
    final_extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    shared = {
        **base,
        "event": "chat_completion",
        "intent_route": intent_route if intent_route is not None else base.get("intent_route"),
        "route_name": route_name,
        "cache_hit": False,
        "context_source": context_source,
        "context_present": context_present,
    }
    first = {
        **shared,
        "status": status,
        "response_mode": response_mode,
        "usage": usage,
        **(first_extra or {}),
    }
    final = {
        **shared,
        "status": final_status,
        "response_mode": "stream_final",
        "usage": None,
        "answer_preview": monitor_answer_preview_fn(answer),
        **(final_extra or {}),
    }
    return first, final
