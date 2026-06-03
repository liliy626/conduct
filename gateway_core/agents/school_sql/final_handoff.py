from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from gateway_core.agents.school_sql.agent_model import agent_model_for_tool_loop
from gateway_core.infra.utils import chunk_text as _chunk_text
from gateway_core.infra.utils import env_value as _env_value
from gateway_core.infra.utils import extract_chunk_usage as _extract_chunk_usage
from gateway_core.infra.utils import loads_json_object as _loads_json_object
from gateway_core.infra.utils import model_name as _model_name
from gateway_core.infra.utils import truncate as _truncate_text
from gateway_core.runtime.runtime_trace_context import _add_trace_usage
from gateway_core.school.trace import set_step_output, trace_preview, trace_step


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
        for key in (
            "pure_business_data_markdown",
            "final_answer",
            "answer",
            "answer_markdown",
            "summary_markdown",
            "summary",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip() and not _looks_like_internal_payload(value):
                return value.strip()
    return ""


def append_business_disclaimer(answer: str, *, business_prompt_context: str = "") -> str:
    clean = str(answer or "").strip()
    disclaimer = _business_prompt_disclaimer(business_prompt_context)
    if not clean or not disclaimer or disclaimer in clean:
        return clean
    return clean.rstrip() + "\n\n" + disclaimer


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
        handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
        with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
            set_step_output(
                context_step,
                {
                    "input": {
                        "question": question,
                        "source_views": source_views,
                        "handoff_chars": len(handoff_text),
                    },
                    "decision": {
                        "model_name": "",
                        "scripted_handoff": True,
                        "format_policy": "scripted_business_markdown",
                    },
                    "output": {
                        "answer_chars": len(scripted_answer),
                        "handoff_json": trace_preview(handoff_text),
                    },
                    "error": None,
                },
            )
        yield scripted_answer
        return

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    answer_parts: list[str] = []
    started = datetime.now().timestamp()
    prompt = fast_final_answer_prompt(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    system_prompt = fast_final_answer_system_prompt()
    handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(handoff_text),
                    "business_prompt_chars": len(str(business_prompt_context or "")),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "business_prompt_context": trace_preview(business_prompt_context),
                    "system_prompt": trace_preview(system_prompt),
                    "final_prompt": trace_preview(prompt),
                },
                "error": None,
            },
        )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(handoff_text),
            "source_views": source_views,
            "handoff_json": trace_preview(handoff_text),
            "business_prompt_context": trace_preview(business_prompt_context),
            "final_prompt": trace_preview(prompt),
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        async for chunk in model.astream(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]
        ):
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
        set_step_output(
            step,
            {
                "model_name": _model_name(final_model),
                "first_token_ms": first_token_ms,
                "stream_chunk_count": chunk_count,
                "usage": usage,
                "final_prompt": trace_preview(prompt),
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)
    disclaimer = _business_prompt_disclaimer(business_prompt_context)
    if disclaimer and disclaimer not in "".join(answer_parts):
        yield "\n\n" + disclaimer


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
        description=(
            "当你已完成必要数据查询、联网/政策检索和业务证据核验，准备交接证据时调用。"
            "把紧凑 JSON 字符串放入 handoff_json。"
            "不要把完整 Markdown 长答案放入 handoff_json；最终自然语言表达由 final answer 模型完成。"
            "目录盘点类问题优先交接 business_domains/items/source_views/caveats 等结构化字段，"
            "每项只保留名称、数量、代表性表或关键证据短语。"
        ),
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
        "question": question,
        "data_evidence": evidence_by_task,
        "evidence_board": evidence_board,
        "external_evidence": [],
        "pure_business_data_markdown": "",
        "source_views": source_views,
        "caveats": [caveat] if caveat else [],
    }
    return payload


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
        handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
        with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
            set_step_output(
                context_step,
                {
                    "input": {
                        "question": question,
                        "source_views": source_views,
                        "handoff_chars": len(handoff_text),
                    },
                    "decision": {
                        "model_name": "",
                        "scripted_handoff": True,
                        "format_policy": "scripted_business_markdown",
                    },
                    "output": {
                        "answer_chars": len(scripted_answer),
                        "handoff_json": trace_preview(handoff_text),
                    },
                    "error": None,
                },
            )
        return scripted_answer

    model = agent_model_for_tool_loop(final_model)
    first_token_ms: int | None = None
    chunk_count = 0
    started = datetime.now().timestamp()
    answer_parts: list[str] = []
    prompt = fast_final_answer_prompt(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    system_prompt = fast_final_answer_system_prompt()
    handoff_text = json.dumps(handoff_payload, ensure_ascii=False, default=str)
    with trace_step(trace, "final_answer_context", {"question": question}) as context_step:
        set_step_output(
            context_step,
            {
                "input": {
                    "question": question,
                    "source_views": source_views,
                    "handoff_chars": len(handoff_text),
                    "business_prompt_chars": len(str(business_prompt_context or "")),
                },
                "decision": {
                    "model_name": _model_name(final_model),
                    "format_policy": "free",
                },
                "output": {
                    "handoff_json": trace_preview(handoff_text),
                    "business_prompt_context": trace_preview(business_prompt_context),
                    "system_prompt": trace_preview(system_prompt),
                    "final_prompt": trace_preview(prompt),
                },
                "error": None,
            },
        )
    with trace_step(
        trace,
        "agent_native.final_fast.llm",
        {
            "model_name": _model_name(final_model),
            "question": question,
            "handoff_chars": len(handoff_text),
            "source_views": source_views,
            "handoff_json": trace_preview(handoff_text),
            "business_prompt_context": trace_preview(business_prompt_context),
            "final_prompt": trace_preview(prompt),
        },
    ) as step:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for chunk in model.stream(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=prompt),
            ]
        ):
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
        set_step_output(
            step,
            {
                "model_name": _model_name(final_model),
                "first_token_ms": first_token_ms,
                "stream_chunk_count": chunk_count,
                "usage": usage,
                "final_prompt": trace_preview(prompt),
            },
        )
        if usage["total_tokens"] > 0:
            _add_trace_usage(usage)
    return append_business_disclaimer("".join(answer_parts), business_prompt_context=business_prompt_context)


def fast_final_answer_system_prompt() -> str:
    return (
        "你根据客观证据包回答学校数据问题。"
        "所有数字、名单、判断、政策与来源必须来自证据，不得新增未提供的事实。"
        "禁止向用户泄露 SQL、数据表名、工具名、Handoff JSON、内部节点名或“我先查询/我需要查看”等过程性话术。"
        "在不改变事实的前提下，可以自由组织最终呈现方式；用自然、克制、面向学校管理者的语言，避免模板腔和生硬口号。"
    )


def fast_final_answer_prompt(
    *,
    question: str,
    handoff_payload: dict[str, Any],
    source_views: list[str],
    business_prompt_context: str = "",
) -> str:
    style_guide = fast_final_answer_style_guide(
        question=question,
        handoff_payload=handoff_payload,
        source_views=source_views,
        business_prompt_context=business_prompt_context,
    )
    return "\n".join(
        [
            "客观证据包：",
            style_guide,
            "基于证据回答用户问题。呈现方式自由；结合业务提示词里的证据边界，用清楚、柔和、可读的学校业务语言表达；不要泄露内部结构，不要复述工具过程，不要说“根据校医院反馈”等未提供来源。",
        ]
    )


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
    for key in (
        "question",
        "status",
        "answer_focus",
        "summary",
        "final_answer",
        "answer",
        "answer_markdown",
        "pure_business_data_markdown",
        "business_domains",
        "items",
        "metrics",
        "key_findings",
        "caveats",
        "source_views",
        "external_evidence",
    ):
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
        for key in ("dataset_label", "intent", "total_row_count", "query_may_have_more", "total_count_error"):
            value = task.get(key)
            if value not in (None, "", [], {}):
                item[key] = value
        for key in ("truth_data_markdown", "notable_findings", "top_items", "row_sample"):
            value = summary.get(key)
            if value not in (None, "", [], {}):
                item[key] = _compact_json_value(value, max_chars=1800)
        sql_lineage = task.get("sql_lineage")
        if isinstance(sql_lineage, dict):
            item["sql_lineage"] = {
                key: sql_lineage.get(key)
                for key in ("tables_used", "row_count", "time_range")
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
    for key in ("business_clues", "caveats", "source_views", "tasks"):
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
        for key in ("summary", "final_answer", "answer", "answer_markdown", "handoff_json"):
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
    marker_index = _formal_final_answer_marker_index(raw)
    if marker_index < 0:
        marker_index = _final_answer_marker_index(raw)
    if marker_index > 0 and _looks_like_tool_planning_text(raw[:marker_index]):
        return raw[marker_index:].lstrip(" \n\r\t：:")
    return raw


def _formal_final_answer_marker_index(text: str) -> int:
    candidates: list[int] = []
    for pattern in [
        r"以下是.{0,180}(?:完整汇报|情况|名单|分析|报告|结果)",
        r"下面(?:是|给你|为你).{0,180}(?:汇报|情况|名单|分析|报告|结果)",
        r"正式(?:回答|汇报|结论)[:：]?",
    ]:
        match = re.search(pattern, str(text or ""))
        if match:
            candidates.append(match.start())
    return min(candidates) if candidates else -1


def _final_answer_marker_index(text: str) -> int:
    candidates: list[int] = []
    for pattern in [
        r"以下是.{0,180}(?:完整汇报|情况|名单|分析|报告|结果)",
        r"下面(?:是|给你|为你).{0,180}(?:汇报|情况|名单|分析|报告|结果)",
        r"正式(?:回答|汇报|结论)[:：]?",
        r"\n\s*#{1,4}\s+",
        r"\n\s*---\s*\n",
        r"根据(?:学校|本次|查询|数据)",
        r"(?:本月|本学期|本年度|本周).{0,16}(?:情况|概况|分析|如下)",
        r"(?:统计周期|数据来源)[:：]",
    ]:
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
