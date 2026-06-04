from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from gateway_core.agents.contracts.output_contracts import (
    DIRECT_ANSWER_KEYS,
    EVIDENCE_BOARD_KEYS,
    EVIDENCE_LINEAGE_KEYS,
    EVIDENCE_SUMMARY_KEYS,
    EVIDENCE_TASK_KEYS,
    HANDOFF_COMPACT_KEYS,
    OUTPUT_CONTRACT_VERSION,
)
from gateway_core.agents.contracts.inter_agent_state import build_inter_agent_state
from gateway_core.agents.contracts.workflow_contracts import (
    SCHOOL_DATA_ANSWER_WORKFLOW,
)
from gateway_core.agents.contracts.workflow_trace import record_inter_agent_state_build
from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.agents.school_sql.final_answer_trace import (
    final_answer_messages as _final_answer_messages,
    final_llm_trace_input as _final_llm_trace_input,
    handoff_text as _handoff_text,
    record_final_answer_context as _record_final_answer_context,
    record_final_llm_output as _record_final_llm_output,
    record_scripted_final_answer_context as _record_scripted_final_answer_context,
)
from gateway_core.infra.utils import chunk_text as _chunk_text
from gateway_core.infra.utils import env_value as _env_value
from gateway_core.infra.utils import extract_chunk_usage as _extract_chunk_usage
from gateway_core.infra.utils import loads_json_object as _loads_json_object
from gateway_core.infra.utils import truncate as _truncate_text
from gateway_core.prompts.agents.final_answer import build_final_answer_prompt, build_final_answer_system_prompt
from gateway_core.prompts.output_contracts.final_handoff import FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION
from gateway_core.school.trace import set_step_output, trace_preview, trace_step


NESTED_HANDOFF_JSON_KEYS = ("summary", "final_answer", "answer", "answer_markdown", "handoff_json")
FORMAL_FINAL_ANSWER_PATTERNS = (
    r"以下是.{0,180}(?:完整汇报|情况|名单|分析|报告|结果)",
    r"下面(?:是|给你|为你).{0,180}(?:汇报|情况|名单|分析|报告|结果)",
    r"正式(?:回答|汇报|结论)[:：]?",
)
GENERAL_FINAL_ANSWER_PATTERNS = (
    r"\n\s*#{1,4}\s+",
    r"\n\s*---\s*\n",
    r"根据(?:学校|本次|查询|数据)",
    r"(?:本月|本学期|本年度|本周).{0,16}(?:情况|概况|分析|如下)",
    r"(?:统计周期|数据来源)[:：]",
)


def final_handoff_enabled(final_model: Any) -> bool:
    if final_model is None:
        return False
    return _env_value("SCHOOL_AGENT_FINAL_MODEL_HANDOFF_ENABLED", "TENANT_AGENT_FINAL_MODEL_HANDOFF_ENABLED", "0").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def extract_final_handoff_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    marker = "FINAL_ANSWER_HANDOFF_JSON:"
    marker_index = raw.find(marker)
    if marker_index < 0:
        return {}
    payload_text = _strip_json_fence(raw[marker_index + len(marker) :])
    parsed = _loads_json_object(payload_text)
    if parsed:
        return parsed
    match = re.search(r"\{.*\}", payload_text, flags=re.DOTALL)
    return _loads_json_object(match.group(0)) if match else {}


def extract_final_handoff_block(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    marker = "FINAL_ANSWER_HANDOFF_BLOCKED:"
    marker_index = raw.find(marker)
    if marker_index < 0:
        return {}
    payload_text = _strip_json_fence(raw[marker_index + len(marker) :])
    parsed = _loads_json_object(payload_text)
    if parsed:
        return parsed
    match = re.search(r"\{.*\}", payload_text, flags=re.DOTALL)
    return _loads_json_object(match.group(0)) if match else {}


def handoff_payload_with_tool_evidence(handoff_payload: dict[str, Any], *, tools: Any) -> dict[str, Any]:
    payload = dict(handoff_payload or {})
    evidence_by_task = getattr(tools, "evidence_by_task", {}) or {}
    if isinstance(evidence_by_task, dict) and evidence_by_task:
        payload.setdefault("data_evidence", evidence_by_task)
    payload.setdefault(
        "inter_agent_state",
        _inter_agent_state_payload(
            question=str(payload.get("question") or ""),
            tools=tools,
            handoff_payload=payload,
        ),
    )
    return payload


def scripted_handoff_answer(handoff_payload: dict[str, Any], *, business_prompt_context: str = "") -> str:
    if _env_value("SCHOOL_AGENT_SCRIPTED_HANDOFF_FINAL_ENABLED", default="1").lower() not in {"1", "true", "yes", "on"}:
        return ""
    if not isinstance(handoff_payload, dict):
        return ""
    markdown = business_answer_markdown(handoff_payload)
    if not markdown:
        return ""
    answer = _sanitize_final_answer(markdown)
    caveats: list[str] = []
    for payload in _business_handoff_payloads(handoff_payload):
        caveats = _business_caveats(payload.get("caveats"))
        if caveats:
            break
    if caveats and not _answer_already_has_caveats(answer):
        answer = answer.rstrip() + "\n\n### 注意事项\n" + "\n".join(f"- {item}" for item in caveats)
    return append_business_disclaimer(answer.strip(), business_prompt_context=business_prompt_context)


def business_answer_markdown(handoff_payload: dict[str, Any]) -> str:
    for payload in _business_handoff_payloads(handoff_payload):
        for key in DIRECT_ANSWER_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip() and not _looks_like_internal_payload(value):
                return value.strip()
    return ""


def append_business_disclaimer(answer: str, *, business_prompt_context: str = "") -> str:
    clean = str(answer or "").strip()
    if not clean:
        return clean
    return clean + _business_disclaimer_suffix(clean, business_prompt_context=business_prompt_context)


def _final_answer_materials(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str,
) -> dict[str, str]:
    prompt = fast_final_answer_prompt(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    return {
        "prompt": prompt,
        "system_prompt": fast_final_answer_system_prompt(),
        "handoff_text": _handoff_text(handoff_payload),
    }


def _business_disclaimer_suffix(answer: str, *, business_prompt_context: str = "") -> str:
    disclaimer = _business_prompt_disclaimer(business_prompt_context)
    if not disclaimer or disclaimer in str(answer or ""):
        return ""
    return "\n\n" + disclaimer


async def stream_fast_final_answer(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> AsyncIterator[str]:
    scripted_answer = scripted_handoff_answer(handoff_payload, business_prompt_context=business_prompt_context)
    if scripted_answer:
        _record_scripted_final_answer_context(
            trace=trace,
            question=question,
            source_views=source_views,
            handoff_payload=handoff_payload,
            scripted_answer=scripted_answer,
        )
        yield scripted_answer
        return

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    answer_parts: list[str] = []
    started = datetime.now().timestamp()
    materials = _final_answer_materials(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    prompt = materials["prompt"]
    system_prompt = materials["system_prompt"]
    handoff_text = materials["handoff_text"]
    _record_final_answer_context(
        trace=trace,
        final_model=final_model,
        question=question,
        source_views=source_views,
        handoff_text=handoff_text,
        business_prompt_context=business_prompt_context,
        system_prompt=system_prompt,
        prompt=prompt,
    )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        _final_llm_trace_input(
            final_model=final_model,
            question=question,
            handoff_text=handoff_text,
            source_views=source_views,
            business_prompt_context=business_prompt_context,
            prompt=prompt,
        ),
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        async for chunk in model.astream(_final_answer_messages(system_prompt=system_prompt, prompt=prompt)):
            chunk_usage = _extract_chunk_usage(chunk)
            if chunk_usage["total_tokens"] > 0:
                usage = chunk_usage
            text = _chunk_text(chunk)
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = max(0, int((datetime.now().timestamp() - started) * 1000))
            chunk_count += 1
            answer_parts.append(text)
            yield text
        _record_final_llm_output(
            step,
            final_model=final_model,
            first_token_ms=first_token_ms,
            chunk_count=chunk_count,
            usage=usage,
            prompt=prompt,
        )
    suffix = _business_disclaimer_suffix("".join(answer_parts), business_prompt_context=business_prompt_context)
    if suffix:
        yield suffix


def final_answer_handoff_tool(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    source_views_fn: Callable[[], list[str]],
    tool_contract: Any = None,
) -> StructuredTool:
    def _run(handoff_json: str) -> str:
        if tool_contract is not None:
            try:
                block_payload = tool_contract.handoff_block_payload()
            except Exception:
                block_payload = None
            if block_payload:
                return "FINAL_ANSWER_HANDOFF_BLOCKED: " + json.dumps(block_payload, ensure_ascii=False, default=str)
        handoff_payload = _loads_json_object(str(handoff_json or "")) or {"summary": str(handoff_json or "").strip()}
        handoff_payload.setdefault("contract_version", OUTPUT_CONTRACT_VERSION)
        handoff_payload.setdefault("question", question)
        handoff_payload.setdefault("source_views", source_views_fn())
        if tool_contract is not None:
            try:
                handoff_payload.setdefault("tool_contract", tool_contract.trace_payload())
            except Exception:
                pass
        return "FINAL_ANSWER_HANDOFF_JSON: " + json.dumps(handoff_payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name="final_answer_handoff",
        description=FINAL_ANSWER_HANDOFF_TOOL_DESCRIPTION,
        func=_run,
    )


def fallback_final_handoff_payload(*, question: str, tools: Any, caveat: str = "") -> dict[str, Any]:
    evidence_board: dict[str, Any] = {}
    try:
        evidence_board = tools.evidence_board_payload()
    except Exception:
        evidence_board = {}
    source_views = list(getattr(tools, "source_views", []) or [])
    evidence_by_task = getattr(tools, "evidence_by_task", {}) or {}
    payload = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "question": question,
        "data_evidence": evidence_by_task,
        "evidence_board": evidence_board,
        "external_evidence": [],
        "pure_business_data_markdown": "",
        "source_views": source_views,
        "caveats": [caveat] if caveat else [],
    }
    payload["inter_agent_state"] = _inter_agent_state_payload(
        question=question,
        tools=tools,
        handoff_payload=payload,
    )
    return payload


def _inter_agent_state_payload(*, question: str, tools: Any, handoff_payload: dict[str, Any]) -> dict[str, Any]:
    evidence_by_task = getattr(tools, "evidence_by_task", {}) or {}
    if not isinstance(evidence_by_task, dict):
        evidence_by_task = {}
    evidence_board = handoff_payload.get("evidence_board")
    if not isinstance(evidence_board, dict):
        try:
            evidence_board = tools.evidence_board_payload()
        except Exception:
            evidence_board = {}
    tool_contract = handoff_payload.get("tool_contract")
    if not isinstance(tool_contract, dict):
        tool_contract = {}
    state_payload = build_inter_agent_state(
        question=str(question or handoff_payload.get("question") or ""),
        data_evidence=evidence_by_task,
        evidence_board=evidence_board if isinstance(evidence_board, dict) else {},
        source_views=list(handoff_payload.get("source_views") or getattr(tools, "source_views", []) or []),
        tool_contract=tool_contract,
        completed_outputs=["data_evidence"] if evidence_by_task else [],
        external_evidence=handoff_payload.get("external_evidence") if isinstance(handoff_payload.get("external_evidence"), list) else [],
        artifacts=getattr(getattr(tools, "tool_contract", None), "artifacts", []) if hasattr(tools, "tool_contract") else [],
        caveats=handoff_payload.get("caveats") if isinstance(handoff_payload.get("caveats"), list) else [],
    ).model_dump()
    record_inter_agent_state_build(
        trace=getattr(tools, "trace", None),
        workflow=SCHOOL_DATA_ANSWER_WORKFLOW,
        question=str(question or handoff_payload.get("question") or ""),
        state_payload=state_payload,
    )
    return state_payload


def run_fast_final_answer_sync(
    *,
    trace: Any,
    final_model: Any,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    scripted_answer = scripted_handoff_answer(handoff_payload, business_prompt_context=business_prompt_context)
    if scripted_answer:
        _record_scripted_final_answer_context(
            trace=trace,
            question=question,
            source_views=source_views,
            handoff_payload=handoff_payload,
            scripted_answer=scripted_answer,
        )
        return scripted_answer

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    answer_parts: list[str] = []
    materials = _final_answer_materials(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    prompt = materials["prompt"]
    system_prompt = materials["system_prompt"]
    handoff_text = materials["handoff_text"]
    _record_final_answer_context(
        trace=trace,
        final_model=final_model,
        question=question,
        source_views=source_views,
        handoff_text=handoff_text,
        business_prompt_context=business_prompt_context,
        system_prompt=system_prompt,
        prompt=prompt,
    )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        _final_llm_trace_input(
            final_model=final_model,
            question=question,
            handoff_text=handoff_text,
            source_views=source_views,
            business_prompt_context=business_prompt_context,
            prompt=prompt,
        ),
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for chunk in model.stream(_final_answer_messages(system_prompt=system_prompt, prompt=prompt)):
            chunk_usage = _extract_chunk_usage(chunk)
            if chunk_usage["total_tokens"] > 0:
                usage = chunk_usage
            text = _chunk_text(chunk)
            if not text:
                continue
            if first_token_ms is None:
                first_token_ms = max(0, int((datetime.now().timestamp() - started) * 1000))
            chunk_count += 1
            answer_parts.append(text)
        _record_final_llm_output(
            step,
            final_model=final_model,
            first_token_ms=first_token_ms,
            chunk_count=chunk_count,
            usage=usage,
            prompt=prompt,
        )
    answer = "".join(answer_parts)
    return append_business_disclaimer(answer, business_prompt_context=business_prompt_context)


def fast_final_answer_system_prompt() -> str:
    return build_final_answer_system_prompt()


def fast_final_answer_prompt(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    evidence_packet = fast_final_answer_style_guide(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    return build_final_answer_prompt(evidence_packet)


def fast_final_answer_style_guide(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    q = str(question or "")
    business_prompt_text = str(business_prompt_context or "").strip()
    evidence_matrix: dict[str, Any] = {
        "user_original_question": q,
        "source_views": list(source_views or []),
        "handoff_evidence": compact_final_handoff_payload(handoff_payload),
    }
    if business_prompt_text:
        evidence_matrix["业务提示词/证据边界"] = business_prompt_text
        evidence_matrix["business_prompt_context"] = business_prompt_text
    return (
        "========================================================\n"
        f"{json.dumps(evidence_matrix, ensure_ascii=False, default=str, indent=2)}\n"
        "========================================================"
    )


def compact_final_handoff_payload(handoff_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(handoff_payload, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in HANDOFF_COMPACT_KEYS:
        value = handoff_payload.get(key)
        if value not in (None, "", [], {}):
            compact[key] = _compact_json_value(value, max_chars=3000)
    digest = evidence_digest(handoff_payload.get("data_evidence"))
    if digest:
        compact["evidence_digest"] = digest
    board_digest = evidence_board_digest(handoff_payload.get("evidence_board"))
    if board_digest:
        compact["evidence_board_digest"] = board_digest
    return compact


def evidence_digest(data_evidence: Any) -> list[dict[str, Any]]:
    if not isinstance(data_evidence, dict):
        return []
    out: list[dict[str, Any]] = []
    for task_id, task in data_evidence.items():
        if not isinstance(task, dict):
            continue
        summary = task.get("evidence_summary") if isinstance(task.get("evidence_summary"), dict) else {}
        item: dict[str, Any] = {
            "task_id": str(task_id),
            "row_count": task.get("row_count", summary.get("row_count")),
            "referenced_views": task.get("referenced_views", summary.get("referenced_views")),
        }
        for key in EVIDENCE_TASK_KEYS:
            value = task.get(key)
            if value not in (None, "", [], {}):
                item[key] = value
        for key in EVIDENCE_SUMMARY_KEYS:
            value = summary.get(key)
            if value not in (None, "", [], {}):
                item[key] = _compact_json_value(value, max_chars=1800)
        sql_lineage = task.get("sql_lineage")
        if isinstance(sql_lineage, dict):
            item["sql_lineage"] = {
                key: sql_lineage.get(key)
                for key in EVIDENCE_LINEAGE_KEYS
                if sql_lineage.get(key) not in (None, "", [], {})
            }
        out.append({key: value for key, value in item.items() if value not in (None, "", [], {})})
        if len(out) >= _final_answer_evidence_digest_limit():
            break
    return out


def evidence_board_digest(evidence_board: Any) -> dict[str, Any]:
    if not isinstance(evidence_board, dict):
        return {}
    out: dict[str, Any] = {}
    for key in EVIDENCE_BOARD_KEYS:
        value = evidence_board.get(key)
        if value not in (None, "", [], {}):
            out[key] = _compact_json_value(value, max_chars=2000)
    return out


def _business_handoff_payloads(handoff_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(handoff_payload, dict):
        return []
    out: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = [handoff_payload]
    seen: set[int] = set()
    while queue and len(out) < 8:
        payload = queue.pop(0)
        identity = id(payload)
        if identity in seen:
            continue
        seen.add(identity)
        out.append(payload)
        for key in NESTED_HANDOFF_JSON_KEYS:
            parsed = _loads_json_object(payload.get(key))
            if parsed:
                queue.append(parsed)
    return out


def _business_prompt_disclaimer(business_prompt_context: str) -> str:
    payload = _loads_json_object(str(business_prompt_context or ""))
    found = _first_key_text(payload, "disclaimer")
    return found.strip()


def _first_key_text(value: Any, key_name: str) -> str:
    if isinstance(value, dict):
        value_at_key = value.get(key_name)
        if isinstance(value_at_key, str) and value_at_key.strip():
            return value_at_key
        for nested in value.values():
            found = _first_key_text(nested, key_name)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _first_key_text(nested, key_name)
            if found:
                return found
    return ""


def _business_caveats(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\n;；]+", value) if item.strip()]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_items = []
    out: list[str] = []
    for item in raw_items:
        clean = _strip_internal_caveat_text(item)
        if clean and clean not in out:
            out.append(clean)
    return out[:6]


def _strip_internal_caveat_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    internal_tokens = [
        "sql",
        "ddl_",
        "tool_",
        "handoff",
        "json",
        "trace",
        "langgraph",
        "source_views",
        "evidence_board",
        "tool_contract",
    ]
    lowered = clean.lower()
    if any(token in lowered for token in internal_tokens):
        return ""
    return clean


def _looks_like_internal_payload(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return True
    if clean.startswith("{") and clean.endswith("}"):
        return True
    lowered = clean.lower()
    return any(token in lowered for token in ["final_answer_handoff_json", "handoff json", "tool_contract"])


def _answer_already_has_caveats(answer: str) -> bool:
    text = str(answer or "")
    return any(token in text for token in ["注意事项", " caveat", "Caveat", "局限", "说明"])


def _compact_json_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, max_chars, reserve=20)
    if isinstance(value, list):
        return [_compact_json_value(item, max_chars=max_chars) for item in value[:20]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, nested) in enumerate(value.items()):
            if index >= 30:
                break
            if str(key) in {"raw_rows", "rows", "data_evidence", "evidence_board"}:
                continue
            out[str(key)] = _compact_json_value(nested, max_chars=max_chars)
        return out
    return value


def _final_answer_evidence_digest_limit() -> int:
    try:
        return max(3, min(int(_env_value("SCHOOL_FINAL_ANSWER_EVIDENCE_DIGEST_LIMIT", default="24") or "24"), 80))
    except Exception:
        return 24


def _sanitize_final_answer(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    marker_index = _final_answer_marker_index(raw, formal_only=True)
    if marker_index < 0:
        marker_index = _final_answer_marker_index(raw)
    if marker_index > 0 and _looks_like_tool_planning_text(raw[:marker_index]):
        return raw[marker_index:].lstrip(" \n\r\t：:")
    return raw


def _final_answer_marker_index(text: str, *, formal_only: bool = False) -> int:
    candidates: list[int] = []
    patterns = FORMAL_FINAL_ANSWER_PATTERNS if formal_only else (*FORMAL_FINAL_ANSWER_PATTERNS, *GENERAL_FINAL_ANSWER_PATTERNS)
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else -1


def _looks_like_tool_planning_text(text: str) -> bool:
    clean = re.sub(r"\s+", "", str(text or ""))
    return any(
        marker in clean
        for marker in [
            "我先",
            "先查",
            "查一下",
            "查看",
            "确认字段",
            "找到核心表",
            "现在查询",
            "现在我来查询",
            "现在我来整理",
            "数据非常完整",
            "数据已经",
            "已经获取",
            "已经拿到",
            "让我",
        ]
    )


def _strip_json_fence(text: str) -> str:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


_final_handoff_enabled = final_handoff_enabled
_extract_final_handoff_payload = extract_final_handoff_payload
_extract_final_handoff_block = extract_final_handoff_block
_handoff_payload_with_tool_evidence = handoff_payload_with_tool_evidence
_scripted_handoff_answer = scripted_handoff_answer
_business_answer_markdown = business_answer_markdown
_append_business_disclaimer = append_business_disclaimer
_stream_fast_final_answer = stream_fast_final_answer
_final_answer_handoff_tool = final_answer_handoff_tool
_fallback_final_handoff_payload = fallback_final_handoff_payload
_run_fast_final_answer_sync = run_fast_final_answer_sync
_fast_final_answer_system_prompt = fast_final_answer_system_prompt
_fast_final_answer_prompt = fast_final_answer_prompt
_fast_final_answer_style_guide = fast_final_answer_style_guide
_compact_final_handoff_payload = compact_final_handoff_payload
_evidence_digest = evidence_digest
_evidence_board_digest = evidence_board_digest
