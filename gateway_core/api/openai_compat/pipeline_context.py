from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gateway_core.api.openai_compat.monitor_payloads import build_request_monitor_base


@dataclass(frozen=True)
class PipelineContext:
    request: Any
    header_probe: dict[str, Any]
    completion_id: str
    model_id: str
    stream: bool
    stream_downgraded: bool
    x_school_scope: str | None
    token: str
    current_viewer_role: str | None
    current_user_id: str | None
    clip_monitor_text_fn: Callable[[Any], str]
    hash_token_for_monitor_fn: Callable[[str], str]
    now_ts_fn: Callable[[], int]

    def monitor_base(
        self,
        *,
        school_scope: str | None,
        question: str,
        last_question: str,
        intent_route: str,
        predicted_domain: str | None = None,
        general_knowledge_subtype: str | None = None,
        route_name: str | None = None,
        context_source: str | None = None,
        context_present: bool | None = None,
        cache_hit: bool | None = None,
        stream_downgraded: bool | None = None,
    ) -> dict[str, Any]:
        return build_request_monitor_base(
            request=self.request,
            header_probe=self.header_probe,
            completion_id=self.completion_id,
            model_id=self.model_id,
            stream=self.stream,
            school_scope=school_scope,
            x_school_scope=self.x_school_scope,
            token=self.token,
            current_viewer_role=self.current_viewer_role,
            current_user_id=self.current_user_id,
            question=question,
            last_question=last_question,
            intent_route=intent_route,
            clip_monitor_text_fn=self.clip_monitor_text_fn,
            hash_token_for_monitor_fn=self.hash_token_for_monitor_fn,
            now_ts_fn=self.now_ts_fn,
            predicted_domain=predicted_domain,
            general_knowledge_subtype=general_knowledge_subtype,
            route_name=route_name,
            context_source=context_source,
            context_present=context_present,
            cache_hit=cache_hit,
            stream_downgraded=self.stream_downgraded if stream_downgraded is None else stream_downgraded,
        )
