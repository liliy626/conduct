from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from gateway_core.api.openai_compat.citation_formatter import citation_sources_from_payload
from gateway_core.prompts.prompt_domains import (
    CROSS_DOMAIN_ANALYSIS_LAYER,
    DOMAIN_AGENT_LABEL,
    DOMAIN_CONTEXT_LAYER,
    expert_playbook_for_domain,
    final_answer_guide_for_domain,
    resolve_domain_id,
)
from gateway_core.prompts.prompt_layer import PROMPT_REGISTRY, PromptBuilder, PromptRender, PromptTemplate
from gateway_core.prompts.rules.answer_context import (
    ANSWER_AUDIENCE_REMINDER_PROMPT_ID,
    ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,
    ANSWER_CITATION_FORMAT_PROMPT_ID,
    ANSWER_CITATION_FORMAT_PROMPT_VERSION,
    ANSWER_CONTEXT_BASE_PROMPT_ID,
    ANSWER_CONTEXT_BASE_PROMPT_VERSION,
    ANSWER_QUESTION_TYPE_PROMPT_ID,
    ANSWER_QUESTION_TYPE_PROMPT_VERSION,
    ANSWER_REQUEST_PROTOCOL_PROMPT_ID,
    ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION,
    ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID,
    ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION,
    ANSWER_TITLE_EVALUATION_PROMPT_ID,
    ANSWER_TITLE_EVALUATION_PROMPT_VERSION,
)
from gateway_core.prompts.rules.answer_evidence import (
    ANSWER_COMPLEX_EVIDENCE_PROMPT_ID,
    ANSWER_EVIDENCE_BOARD_PROMPT_ID,
    ANSWER_EVIDENCE_PROMPT_VERSION,
    ANSWER_POLICY_EVIDENCE_PROMPT_ID,
)

ANSWER_CONTEXT_PROMPT_ID = "answers.context.system_data"
ANSWER_CONTEXT_PROMPT_VERSION = "2026-06-03.1"
ANSWER_CONTEXT_AGENT_LABEL_PROMPT_ID = "answers.context.agent_label"
ANSWER_CONTEXT_DOMAIN_LAYER_PROMPT_ID = "answers.context.domain_layer"
ANSWER_CONTEXT_EXPERT_PLAYBOOK_PROMPT_ID = "answers.context.expert_playbook"
ANSWER_CONTEXT_CROSS_DOMAIN_PROMPT_ID = "answers.context.cross_domain"
ANSWER_CONTEXT_FINAL_GUIDE_PROMPT_ID = "answers.context.final_guide"
ANSWER_CONTEXT_QUESTION_PROMPT_ID = "answers.context.current_question"
ANSWER_CONTEXT_NOTE_PROMPT_ID = "answers.context.note"
ANSWER_CONTEXT_CITATION_SOURCES_PROMPT_ID = "answers.context.citation_sources"
ANSWER_CONTEXT_PAYLOAD_PROMPT_ID = "answers.context.payload_json"


def audience_mode(viewer_role: str) -> str:
    role = re.sub(r"\s+", "", str(viewer_role or "").strip().lower())
    if not role:
        return ""
    if "校长" in role or "principal" in role or "headmaster" in role:
        return "principal"
    if any(token in role for token in ["班主任", "年级组长", "年级主任", "homeroom", "gradelead", "grade_lead", "gradeleader"]):
        return "grade_homeroom"
    return ""


def render_context_prompt(
    *,
    payload: Any,
    question: str = "",
    viewer_role: str = "",
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_id: str = "",
    context_note: str = "",
) -> PromptRender:
    return _build_context_prompt_render(
        payload=payload,
        question=question,
        viewer_role=viewer_role,
        dataset_id=dataset_id,
        feature_name=feature_name,
        route_name=route_name,
        context_source=context_source,
        domain_id=domain_id,
        context_note=context_note,
    )


def build_context_prompt(
    *,
    payload: Any,
    question: str = "",
    viewer_role: str = "",
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_id: str = "",
    context_note: str = "",
) -> str:
    return render_context_prompt(
        payload=payload,
        question=question,
        viewer_role=viewer_role,
        dataset_id=dataset_id,
        feature_name=feature_name,
        route_name=route_name,
        context_source=context_source,
        domain_id=domain_id,
        context_note=context_note,
    ).text


def _build_context_prompt_render(
    *,
    payload: Any,
    question: str = "",
    viewer_role: str = "",
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_id: str = "",
    context_note: str = "",
) -> PromptRender:
    resolved_domain = resolve_domain_id(
        dataset_id=dataset_id,
        feature_name=feature_name,
        route_name=route_name,
        context_source=context_source,
        domain_hint=domain_id,
    )
    agent_line = DOMAIN_AGENT_LABEL.get(resolved_domain, DOMAIN_AGENT_LABEL["generic_school_data"])
    domain_line = DOMAIN_CONTEXT_LAYER.get(resolved_domain, DOMAIN_CONTEXT_LAYER["generic_school_data"])

    builder = (
        PromptBuilder(PROMPT_REGISTRY, separator="\n")
        .literal(agent_line, prompt_id=ANSWER_CONTEXT_AGENT_LABEL_PROMPT_ID, version="inline")
        .add(ANSWER_REQUEST_PROTOCOL_PROMPT_ID, ANSWER_REQUEST_PROTOCOL_PROMPT_VERSION)
        .add(ANSWER_CONTEXT_BASE_PROMPT_ID, ANSWER_CONTEXT_BASE_PROMPT_VERSION)
        .literal(domain_line, prompt_id=ANSWER_CONTEXT_DOMAIN_LAYER_PROMPT_ID, version="inline")
    )
    expert_playbook = expert_playbook_for_domain(resolved_domain)
    if expert_playbook:
        builder.literal(expert_playbook, prompt_id=ANSWER_CONTEXT_EXPERT_PLAYBOOK_PROMPT_ID, version="inline")
        builder.literal(CROSS_DOMAIN_ANALYSIS_LAYER, prompt_id=ANSWER_CONTEXT_CROSS_DOMAIN_PROMPT_ID, version="inline")
    final_answer_guide = final_answer_guide_for_domain(resolved_domain)
    if final_answer_guide:
        builder.literal(final_answer_guide, prompt_id=ANSWER_CONTEXT_FINAL_GUIDE_PROMPT_ID, version="inline")
    builder.add(ANSWER_QUESTION_TYPE_PROMPT_ID, ANSWER_QUESTION_TYPE_PROMPT_VERSION)
    builder.add_if(
        _payload_requires_policy_evidence(payload),
        ANSWER_POLICY_EVIDENCE_PROMPT_ID,
        ANSWER_EVIDENCE_PROMPT_VERSION,
    )
    citation_sources = citation_sources_from_payload(payload)
    builder.add_if(bool(citation_sources), ANSWER_CITATION_FORMAT_PROMPT_ID, ANSWER_CITATION_FORMAT_PROMPT_VERSION)
    builder.add_if(
        _payload_has_evidence_board(payload),
        ANSWER_EVIDENCE_BOARD_PROMPT_ID,
        ANSWER_EVIDENCE_PROMPT_VERSION,
    )
    is_complex_evidence = isinstance(payload, dict) and payload.get("query_mode") in {
        "planned_multi_query",
        "planned_multi_source",
        "ddl_react_agent",
        "ddl_evidence",
        "react_agent",
        "gateway_agent",
    }
    builder.add_if(is_complex_evidence, ANSWER_COMPLEX_EVIDENCE_PROMPT_ID, ANSWER_EVIDENCE_PROMPT_VERSION)
    if any(
        token in str(question or "")
        for token in ["成果", "申报", "学术", "论文", "发表", "亮点", "述职", "评优", "业绩", "职称"]
    ):
        builder.add(ANSWER_TEACHER_DEVELOPMENT_PROMPT_ID, ANSWER_TEACHER_DEVELOPMENT_PROMPT_VERSION)
    if "职称" in str(question or ""):
        builder.add(ANSWER_TITLE_EVALUATION_PROMPT_ID, ANSWER_TITLE_EVALUATION_PROMPT_VERSION)
    audience = audience_mode(viewer_role)
    builder.add_if(
        audience in {"principal", "grade_homeroom"},
        ANSWER_AUDIENCE_REMINDER_PROMPT_ID,
        ANSWER_AUDIENCE_REMINDER_PROMPT_VERSION,
        audience=audience,
    )

    q = str(question or "").strip()
    if q:
        builder.literal(f"当前问题：{q}", prompt_id=ANSWER_CONTEXT_QUESTION_PROMPT_ID, version="inline")
    note = str(context_note or "").strip()
    if note:
        builder.literal(note, prompt_id=ANSWER_CONTEXT_NOTE_PROMPT_ID, version="inline")
    if citation_sources:
        builder.literal(
            f"引用来源索引(JSON):\n{json.dumps(citation_sources, ensure_ascii=False, default=_json_safe)}",
            prompt_id=ANSWER_CONTEXT_CITATION_SOURCES_PROMPT_ID,
            version="inline",
        )
    builder.literal(
        f"检索结果(JSON):\n{json.dumps(payload, ensure_ascii=False, default=_json_safe)}",
        prompt_id=ANSWER_CONTEXT_PAYLOAD_PROMPT_ID,
        version="inline",
    )
    return builder.build(prompt_id=ANSWER_CONTEXT_PROMPT_ID, version=ANSWER_CONTEXT_PROMPT_VERSION)


def _render_context_prompt(
    *,
    payload: Any,
    question: str = "",
    viewer_role: str = "",
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_id: str = "",
    context_note: str = "",
) -> str:
    return _build_context_prompt_render(
        payload=payload,
        question=question,
        viewer_role=viewer_role,
        dataset_id=dataset_id,
        feature_name=feature_name,
        route_name=route_name,
        context_source=context_source,
        domain_id=domain_id,
        context_note=context_note,
    ).text


def _payload_requires_policy_evidence(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if bool(payload.get("policy_evidence_required")):
        return True
    evidence = payload.get("policy_evidence")
    return isinstance(evidence, list) and bool(evidence)


def _payload_has_evidence_board(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("evidence_board"), dict)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        try:
            return float(value)
        except Exception:
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


PROMPT_REGISTRY.register(
    PromptTemplate(
        prompt_id=ANSWER_CONTEXT_PROMPT_ID,
        version=ANSWER_CONTEXT_PROMPT_VERSION,
        renderer=_render_context_prompt,
        description="最终回答前的 system/data context prompt",
        tags=("answer", "context", "system_data"),
    )
)
