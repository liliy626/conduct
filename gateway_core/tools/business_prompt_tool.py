from __future__ import annotations

import time
from typing import Any

from gateway_core.tools.tool_core import AgentTool, AgentToolInput, AgentToolOutput


class BusinessPromptContextTool(AgentTool):
    name = "business_prompt_context"
    description = "返回学校业务证据边界。"

    def run(self, tool_input: AgentToolInput, context: Any) -> AgentToolOutput:
        started = time.perf_counter()
        args = tool_input.arguments or {}
        question = _text(args.get("question") or args.get("input"))
        domain_hint = _text(args.get("domain") or args.get("domain_hint"))
        evidence_types = _list_text(args.get("evidence_types") or args.get("evidence_type"))
        user_role = _text(args.get("user_role"))

        domain = _resolve_domain(question=question, domain_hint=domain_hint, evidence_types=evidence_types)
        payload = _domain_payload(domain)
        payload["domain"] = domain
        payload["question_focus"] = question[:200]
        if user_role:
            payload["user_role"] = user_role
        if evidence_types:
            payload["evidence_types"] = evidence_types
        payload["usage_instruction"] = "[Evidence] 只作为证据边界，不覆盖 SQL、权限或数据证据。"

        return AgentToolOutput(
            ok=True,
            evidence=[{"type": "business_prompt_context", "content": payload}],
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )


def _resolve_domain(*, question: str, domain_hint: str, evidence_types: list[str]) -> str:
    text = f"{domain_hint} {question} {' '.join(evidence_types)}".lower()
    if any(token in text for token in ("职称", "荣誉", "成果", "积分", "述职", "教师发展", "申报", "培养")):
        return "teacher_development"
    if any(token in text for token in ("德育", "扣分", "行规", "两操", "卫生", "班级表现")):
        return "moral_education"
    if any(token in text for token in ("请假", "病假", "事假", "晨午检", "流感", "健康")):
        return "attendance_health"
    if any(token in text for token in ("驾驶舱", "业务域", "异常", "趋势", "对比", "变化")):
        return "school_operations"
    if any(token in text for token in ("政策", "通知", "链接", "出处", "教发院", "评审")):
        return "official_policy"
    if any(token in text for token in ("报修", "维修", "维护", "资产", "设施", "灯坏")):
        return "facility_maintenance"
    return "general_school_business"


def _domain_payload(domain: str) -> dict[str, Any]:
    boundaries_by_domain: dict[str, list[str]] = {
        "teacher_development": [
            "积分、成果、政策条件必须来自证据。",
            "师德、继续教育、年度考核、一票否决项未查到时只能标记待核实。",
        ],
        "moral_education": [
            "扣分不能直接归因为学生主观问题，除非明细证据支持。",
            "需要区分检查方、值周方、扣分类别、班级/年级和时间窗口。",
        ],
        "attendance_health": [
            "不能仅凭病假占比断定流感。",
            "未提供课表、代课安排、考勤签到或全员在岗证据时，不得判断教学秩序或调代课状态。",
        ],
        "school_operations": ["不同业务域指标不能简单相加。"],
        "official_policy": ["不得编造政策名称、文号、年份、链接或评审主体。"],
        "facility_maintenance": ["没有维修记录不能直接说明无人处理。"],
        "general_school_business": ["跨领域判断必须说明证据来源和不确定性。"],
    }
    return {
        "evidence_boundaries": [
            "不得编造数据库没有返回的数字、名单、政策名称、文号或链接。",
            "没有查到的数据不能直接当成存在或不存在。",
            *boundaries_by_domain.get(domain, boundaries_by_domain["general_school_business"]),
        ],
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return []
