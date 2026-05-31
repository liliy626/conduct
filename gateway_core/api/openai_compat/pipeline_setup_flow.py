from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException

from gateway_core.api.openai_compat.pipeline_context import PipelineContext


@dataclass(frozen=True)
class PipelineSetupResult:
    token: str
    x_school_scope: str | None
    spec: Any
    client: Any
    lc_messages: list[Any]
    raw_last_question: str
    last_question: str
    previous_question: str
    effective_question: str
    completion_id: str
    stream_downgraded: bool
    current_user_id: str | None
    current_viewer_role: str | None
    request_started: float
    pipeline_ctx: PipelineContext


def prepare_pipeline_setup(
    *,
    request: Any,
    req: Any,
    authorization: str | None,
    x_school_scope: str | None,
    x_user_id: str | None,
    x_user_role: str | None,
    x_user_permissions: str | None,
    prepare_chat_session_context_fn: Callable[..., tuple[str, Any]],
    current_user_id_fn: Callable[[], str | None],
    current_viewer_role_fn: Callable[[], str | None],
    gateway: Any,
    to_lc_messages_fn: Callable[..., list[Any]],
    last_user_text_raw_fn: Callable[..., str],
    last_user_text_fn: Callable[..., str],
    previous_user_text_fn: Callable[..., str],
    stringify_content_fn: Callable[[Any], str],
    is_meta_instruction_prompt_fn: Callable[[str], bool],
    resolve_effective_stream_mode_fn: Callable[..., tuple[bool, bool]],
    set_route_name_fn: Callable[[str], Any],
    set_trace_usage_fn: Callable[[dict[str, int]], Any],
    zero_usage_fn: Callable[[], dict[str, int]],
    collect_request_header_probe_fn: Callable[[], dict[str, Any]],
    clip_monitor_text_fn: Callable[[Any], str],
    hash_token_for_monitor_fn: Callable[[str], str],
) -> PipelineSetupResult:
    resolved_x_school_scope = (
        x_school_scope
        or request.headers.get("x-openwebui-school-scope")
        or request.headers.get("x-school-scope")
    )
    resolved_x_user_id = (
        x_user_id
        or request.headers.get("x-openwebui-user-id")
        or request.headers.get("x-user-id")
    )
    resolved_x_user_role = (
        x_user_role
        or request.headers.get("x-openwebui-user-role")
        or request.headers.get("x-user-role")
    )
    resolved_x_user_permissions = (
        x_user_permissions
        or request.headers.get("x-openwebui-user-permissions")
        or request.headers.get("x-user-permissions")
    )

    token, session = prepare_chat_session_context_fn(
        authorization=authorization,
        x_school_scope=resolved_x_school_scope,
        x_user_id=resolved_x_user_id,
        x_user_role=resolved_x_user_role,
        x_user_permissions=resolved_x_user_permissions,
    )
    x_school_scope = session.school_scope
    token = session.token
    current_user_id = current_user_id_fn() or None
    current_viewer_role = current_viewer_role_fn() or None
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages is required")

    spec = gateway.resolve_model(req.model)
    client = gateway.get_client(spec, req.temperature, req.max_tokens)
    lc_messages = to_lc_messages_fn(req.messages)
    raw_last_question = last_user_text_raw_fn(req.messages, stringify_content=stringify_content_fn)
    last_question = last_user_text_fn(
        req.messages,
        stringify_content=stringify_content_fn,
        is_meta_instruction_prompt=is_meta_instruction_prompt_fn,
    )
    previous_question = previous_user_text_fn(
        req.messages,
        stringify_content=stringify_content_fn,
        is_meta_instruction_prompt=is_meta_instruction_prompt_fn,
    )
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    set_route_name_fn("")
    set_trace_usage_fn(zero_usage_fn())
    request_started = time.perf_counter()
    req.stream, stream_downgraded = resolve_effective_stream_mode_fn(
        requested_stream=req.stream,
        raw_last_question=raw_last_question,
        last_question=last_question,
        accept_header=str(request.headers.get("accept", "") or "").lower(),
        user_agent=str(request.headers.get("user-agent", "") or "").lower(),
        is_meta_instruction_prompt=is_meta_instruction_prompt_fn,
    )
    header_probe = collect_request_header_probe_fn()
    pipeline_ctx = PipelineContext(
        request=request,
        header_probe=header_probe,
        completion_id=completion_id,
        model_id=spec.model_id,
        stream=bool(req.stream),
        stream_downgraded=stream_downgraded,
        x_school_scope=x_school_scope or None,
        token=token,
        current_viewer_role=current_viewer_role,
        current_user_id=current_user_id,
        clip_monitor_text_fn=clip_monitor_text_fn,
        hash_token_for_monitor_fn=hash_token_for_monitor_fn,
        now_ts_fn=lambda: int(time.time()),
    )
    return PipelineSetupResult(
        token=token,
        x_school_scope=x_school_scope,
        spec=spec,
        client=client,
        lc_messages=lc_messages,
        raw_last_question=raw_last_question,
        last_question=last_question,
        previous_question=previous_question,
        effective_question=last_question,
        completion_id=completion_id,
        stream_downgraded=stream_downgraded,
        current_user_id=current_user_id,
        current_viewer_role=current_viewer_role,
        request_started=request_started,
        pipeline_ctx=pipeline_ctx,
    )
