from __future__ import annotations

import time
from typing import Any

from gateway_core.tools.tool_core import AgentTool, AgentToolInput, AgentToolOutput


class BusinessPromptContextTool(AgentTool):
    name = "business_prompt_context"
    description = (
        "返回站在学校业务角度的提示词上下文，而不是回答模板。"
        "用于补充业务角色、判断视角、证据边界和表达注意事项。"
    )

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
        payload["usage_instruction"] = (
            "把本工具返回内容作为业务视角提示词板块使用。"
            "它不是回答模板，不要求固定格式；最终回答应基于已查证据自由组织。"
        )

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
    common = {
        "global_business_lens": [
            "先区分已查到的事实、基于事实的业务判断、仍需补证或人工核实的事项。",
            "不要把没有查到的数据直接当成不存在；应表达为当前数据未显示或本轮未核实。",
            "建议要尽量落到学校真实角色可执行，例如年级组、班主任、德育处、人事、教务或总务。",
        ],
        "global_risk_notes": [
            "不得编造数据库没有返回的数字、名单、政策名称、文号或链接。",
            "涉及学生、教师、家长个人信息时，只在问题确有必要且工具已返回证据时使用。",
        ],
    }
    domains: dict[str, dict[str, Any]] = {
        "teacher_development": {
            "business_role": "教师发展与职称/荣誉申报顾问",
            "business_lens": [
                "不要只看积分高低，要解释积分来源、成果级别、等第、主办单位和教师本人角色的业务意义。",
                "发展建议要结合短板、同类对比、政策门槛和下一阶段可补材料。",
                "涉及职称、高级教师、荣誉申报时，要同时使用学校数据和 official_policy 证据。",
            ],
            "risk_notes": [
                "积分不等于职称或荣誉一定通过。",
                "师德、继续教育、年度考核、一票否决等没有证据时必须列为待核实。",
            ],
            "answer_posture": "专业、克制、像学校教师发展负责人给出的分析建议。",
        },
        "moral_education": {
            "business_role": "德育处数据分析助手",
            "business_lens": [
                "先看总体态势，再看年级、班级、扣分类别和时间集中度。",
                "关注扣分是否集中在少数班级、少数问题或某个时间段。",
                "建议应面向德育处、年级组、班主任的可执行动作。",
            ],
            "risk_notes": [
                "不要把扣分直接归因为学生主观问题，除非明细证据支持。",
                "原因分析不足时，应提示继续结合行规明细、执勤或班级管理记录。",
            ],
            "answer_posture": "管理视角、事实优先、建议具体。",
        },
        "attendance_health": {
            "business_role": "校务请假与健康态势分析助手",
            "business_lens": [
                "请假问题要关注总量、对象分布、原因分布、时间趋势和异常集中。",
                "涉及病假高发或流感判断时，应区分校内请假事实、晨午检事实和外部公开/政策信息。",
            ],
            "risk_notes": [
                "不能仅凭病假占比断定流感，需要晨午检、同班同年级集中情况或外部信息补证。",
            ],
            "answer_posture": "谨慎、关注风险、强调后续核查。",
        },
        "school_operations": {
            "business_role": "校级驾驶舱与运营分析助手",
            "business_lens": [
                "先给跨业务域概览，再指出异常、变化趋势和需要管理层关注的事项。",
                "对比题要说明比较口径、时间范围和变化方向。",
            ],
            "risk_notes": ["不同业务域指标不能简单相加，应说明各自口径。"],
            "answer_posture": "面向管理层，结论清楚，重点突出。",
        },
        "official_policy": {
            "business_role": "政策证据解读助手",
            "business_lens": [
                "回答必须贴合 official_policy 返回的出处、链接、发布时间和适用范围。",
                "政策、通知和公开网页证据有强时效性；多条结果并存时，优先采用发布时间更近、来源更权威、主题更贴合的证据。",
                "如果结果时间跨度较大，要说明最新可见证据和较早证据的参考边界；没有发布时间的结果不能被当作最新依据。",
                "如果政策证据不足，应说明当前未检索到充分依据。",
            ],
            "risk_notes": ["不得编造政策文号、评审主体、年限要求或官方网址。"],
            "answer_posture": "依据优先，引用清楚，保守表达。",
        },
        "facility_maintenance": {
            "business_role": "总务与设施维护数据分析助手",
            "business_lens": [
                "维修/报修问题要关注类型分布、位置/对象、处理状态、重复发生和时间趋势。",
                "如果从执勤或巡检发现设施线索，应继续核对报修、维修和资产记录。",
            ],
            "risk_notes": ["没有维修记录不能直接说明无人处理，只能说明当前数据未查到对应记录。"],
            "answer_posture": "问题定位清楚，建议面向总务处可执行。",
        },
        "general_school_business": {
            "business_role": "学校业务数据分析助手",
            "business_lens": [
                "先用数据回答用户真正关心的问题，再解释关键口径和限制。",
                "如发现相关业务线索，可以说明已补查或建议下一步补查方向。",
            ],
            "risk_notes": ["跨领域判断必须说明证据来源和不确定性。"],
            "answer_posture": "清楚、专业、基于证据。",
        },
    }
    payload = dict(common)
    payload.update(domains.get(domain, domains["general_school_business"]))
    return payload


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return []
