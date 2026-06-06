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
        role_context = _role_context(domain=domain, user_role=user_role, question=question)
        payload["domain"] = domain
        payload["role_context"] = role_context
        payload["data_query_guidance"] = _data_query_guidance(role_context)
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
    if any(token in text for token in ("心理", "危机", "个案", "测评", "预警学生", "辅导")):
        return "psychological_health"
    if any(token in text for token in ("排课", "调课", "课表", "学籍", "考务", "综评", "作业", "课后服务", "教学质量")):
        return "teaching_affairs"
    if "教师" in text and any(token in text for token in ("画像", "整体", "全貌", "队伍", "结构", "分布", "评教")):
        return "teacher_profile"
    if any(token in text for token in ("职称", "荣誉", "成果", "积分", "述职", "教师发展", "申报", "培养")):
        return "teacher_development"
    if any(token in text for token in ("德育", "扣分", "行规", "两操", "眼保健操", "违纪", "纪律最差", "卫生", "班级表现")):
        return "moral_education"
    if any(token in text for token in ("门禁", "访客", "消防", "演练", "隐患", "安防", "安全")):
        return "safety_security"
    if any(token in text for token in ("请假", "病假", "事假", "晨午检", "流感", "健康")):
        return "attendance_health"
    if any(token in text for token in ("入队", "入团", "升旗", "社团", "研学", "志愿", "团委", "少先队")):
        return "youth_league"
    if any(token in text for token in ("驾驶舱", "业务域", "异常", "趋势", "对比", "变化")):
        return "school_operations"
    if any(token in text for token in ("政策", "通知", "链接", "出处", "教发院", "评审")):
        return "official_policy"
    if any(token in text for token in ("报修", "维修", "维护", "资产", "设施", "灯坏")):
        return "facility_maintenance"
    if any(token in text for token in ("收费", "退费", "报销", "助学金", "经费", "预算", "票据", "财务")):
        return "finance"
    if any(token in text for token in ("网络", "平台账号", "账号", "智慧黑板", "机房", "线上考试", "网管")):
        return "information_technology"
    if any(token in text for token in ("学生家长", "家长", "孩子")):
        return "parent_service"
    if any(token in text for token in ("学生", "社团报名", "图书借阅", "校规")):
        return "student_service"
    return "general_school_business"


def _domain_payload(domain: str) -> dict[str, Any]:
    boundaries_by_domain: dict[str, list[str]] = {
        "teacher_development": [
            "积分、成果、政策条件必须来自证据。",
            "师德、继续教育、年度考核、一票否决项未查到时只能标记待核实。",
        ],
        "teacher_profile": [
            "教师总数优先按稳定教师 ID（如教师userid）去重；没有稳定 ID 时才按教师姓名去重，并说明口径。",
            "年级、学科、主副科、班主任等分布可能一名教师多角色/多任教年级，不宜简单相加成人数总量。",
            "新旧请假、离校报备、考勤统计可能来自不同系统阶段，记录数和去重人数需分开表达，不宜简单加总。",
            "评教数据必须说明学期/问卷口径；评教记录数不能直接等同于教师人数。",
            "获奖记录需区分记录数、获奖教师去重人数、指导学生获奖与教师个人获奖；未限定时间时说明累计口径。",
            "教师画像回答避免输出个人敏感明细；默认只给汇总、分布、趋势和必要 caveats。",
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


_ROLE_DISCLAIMER = "具体操作请以本年度校办/教务处下发的正式文件及相关平台（如上海市教师发展系统）实时数据为准。"


_ROLE_PROMPT_PACKS: dict[str, dict[str, Any]] = {
    "principal": {
        "role_name": "校长/书记",
        "aliases": ["校长", "书记", "校长书记", "学校领导"],
        "functional_areas": "全校统筹管理、教学质量管控、师资建设、安全风控、财务预算、资产管控、上级迎检、特殊学生管理、职称梯队规划",
        "data_response_logic": "优先汇总全量宏观汇总数据、同比环比，异常数据标注预警，明细指引对应数据看板；少细碎流程，输出结论+优化管理建议。",
        "professional_focus": "统筹各部门督办事项、教育局待上报材料时限管控；自动盘点全校教师职称存量、空岗名额、符合晋升人员清单；聚焦隐患闭环、经费使用、师生异动风险研判。",
    },
    "office_hr": {
        "role_name": "校办&人事干事",
        "aliases": ["校办", "人事", "人事干事", "校办人事", "办公室"],
        "functional_areas": "公文会务、档案管理、公章审批、来访接待、人事台账、年度考核、十四五继续教育学时、见习教师规范化培训、职称校内初审、教师轮岗调动",
        "data_response_logic": "按办理时序拆分材料、审批节点、截止日期；依托上海市教师专业发展信息管理平台规则作答，自动巡检教师学时、考核、教龄数据。",
        "professional_focus": "提醒文件收交倒计时、证书证照到期预警；职称材料预审，标注缺项内容；熟悉校内评优推荐惯例、公示规范。",
    },
    "teaching_affairs": {
        "role_name": "教务处&教务员",
        "aliases": ["教务", "教务处", "教务员"],
        "functional_areas": "排调课、学籍异动、考务组织、教材教辅、课后服务与社团、综评系统填报、教研公开课、大单元教学、作业管控、教学质量分析",
        "data_response_logic": "出勤、成绩、作业数据汇总后引导教务数据看板；区分填报时限与系统操作路径。",
        "professional_focus": "遵循上海综评制度、新课标要求；课题指引贴合上海教科院小切口实证导向；自动预警课务冲突、作业超标、综评漏填。",
    },
    "moral_education": {
        "role_name": "德育处/年级组长/班主任",
        "aliases": ["德育", "德育处", "年级组长", "班主任"],
        "functional_areas": "班级量化考核、违纪处置、文明班评比、家校共育、班会德育活动、欺凌防治、班主任年限核算、学生行为预警、心理健康联动",
        "data_response_logic": "班级扣分、违纪数据汇总，对高频问题给出落地举措；自带共情，带班相关咨询附加情绪缓冲、沟通话术建议。",
        "professional_focus": "精准核算主副班主任折算年限，匹配区级优秀班主任、先进班集体申报条件；异常学生联动心理岗预警。",
    },
    "youth_league": {
        "role_name": "少先队/团委书记",
        "aliases": ["少先队", "团委", "团委书记", "大队辅导员"],
        "functional_areas": "入队入团、升旗仪式、社团管理、研学社会实践、志愿者管理、团队课程、学生干部培养",
        "data_response_logic": "团员队员存量、活动开展频次汇总，报备事项标注审批流程与时间节点。",
        "professional_focus": "对标上海少工委、团市委相关申报规范，活动审批区分校内/区级报备要求。",
    },
    "psychology": {
        "role_name": "心理老师",
        "aliases": ["心理", "心理老师", "心理教师"],
        "functional_areas": "心理测评、个体团体辅导、心理危机干预、心理健康科普、重点学生追踪、心理活动策划",
        "data_response_logic": "测评数据分层统计，高危学生单独标记，干预台账指引心理专项看板。",
        "professional_focus": "遵照上海中小学心理健康工作规范，明确危机上报层级与处置流程。",
    },
    "safety": {
        "role_name": "安全干部&安保",
        "aliases": ["安全干部", "安保", "保安", "安全"],
        "functional_areas": "门禁访客、消防巡检、应急演练、隐患整改、校园安防、法治安全教育、校外风险排查",
        "data_response_logic": "访客、隐患、巡检数据汇总，逾期未整改项高亮提醒，明细跳转安全看板。",
        "professional_focus": "按上海校园安全管理条例梳理处置步骤、上报时限。",
    },
    "healthcare": {
        "role_name": "校医/卫生保健老师",
        "aliases": ["校医", "卫生保健", "保健老师", "卫生老师"],
        "functional_areas": "晨检午检、传染病防控、特殊体质学生建档、校园消杀、学生体检、常见病应急处置",
        "data_response_logic": "晨检患病数据汇总，聚集性病症自动预警，健康台账在卫健看板查询。",
        "professional_focus": "落地上海校园疾控、体检相关官方文件要求。",
    },
    "logistics": {
        "role_name": "总务处&后勤资产",
        "aliases": ["总务", "后勤", "后勤资产", "资产"],
        "functional_areas": "报修运维、物资耗材申领、固定资产盘点报废、食堂管控、水电能耗、校园绿化保洁",
        "data_response_logic": "报修工单、能耗、库存数据汇总，异常损耗预警，明细跳转后勤数据看板。",
        "professional_focus": "遵循上海学校资产管理、食堂食品安全相关细则。",
    },
    "finance": {
        "role_name": "财务会计出纳",
        "aliases": ["财务", "会计", "出纳"],
        "functional_areas": "各类收费退费、费用报销、助学金补助、部门经费管控、预算执行、票据管理",
        "data_response_logic": "收支数据汇总，超支、待报销项标注，账目明细查看财务看板。",
        "professional_focus": "严格按照上海公办中小学财务制度、收费政策作答。",
    },
    "it_admin": {
        "role_name": "信息网管老师",
        "aliases": ["信息", "网管", "信息网管", "网络管理员"],
        "functional_areas": "校园网络运维、软硬件故障报修、各平台账号运维、综评/教师发展系统技术支撑、线上考试系统保障",
        "data_response_logic": "故障报修台账汇总，批量故障提醒集中检修，系统操作指引精准到平台入口。",
        "professional_focus": "熟悉上海教育官方平台运维规范与常见故障解决方案。",
    },
    "frontline_teacher": {
        "role_name": "一线任课教师",
        "aliases": ["任课教师", "一线教师", "老师", "教师"],
        "functional_areas": "个人课务、课时统计、调代课申请、备课教研、职称材料筹备、继续教育补学时、学生日常学情反馈",
        "data_response_logic": "个人课时、学时、成果数据一键自查，缺项内容清晰罗列，指引对应平台补材料。",
        "professional_focus": "职称按硬件+业绩+育人三维体检，备课贴合新课标大单元设计。",
    },
    "student": {
        "role_name": "在校学生",
        "aliases": ["学生", "在校学生"],
        "functional_areas": "作息查询、请假办事、社团报名、图书借阅、心理求助、校规咨询",
        "data_response_logic": "个人考勤、活动报名状态简明说明，办事步骤轻量化表述。",
        "professional_focus": "以学生可理解的校园办事语言表达，避免管理端黑话。",
    },
    "parent": {
        "role_name": "学生家长",
        "aliases": ["家长", "学生家长", "父母"],
        "functional_areas": "子女考勤就餐查询、课后服务、缴费对账、家校反馈、综评家庭端填报",
        "data_response_logic": "孩子在校数据精简提炼，政策通俗化解读，规避专业术语。",
        "professional_focus": "换位处理家长焦虑，优先说明与孩子相关的事实、异常和后续沟通建议。",
    },
}


_DOMAIN_ROLE_MAP = {
    "teacher_profile": "principal",
    "teacher_development": "office_hr",
    "moral_education": "moral_education",
    "attendance_health": "healthcare",
    "school_operations": "principal",
    "official_policy": "office_hr",
    "facility_maintenance": "logistics",
    "psychological_health": "psychology",
    "teaching_affairs": "teaching_affairs",
    "safety_security": "safety",
    "youth_league": "youth_league",
    "finance": "finance",
    "information_technology": "it_admin",
    "student_service": "student",
    "parent_service": "parent",
    "general_school_business": "principal",
}


def _role_context(*, domain: str, user_role: str, question: str) -> dict[str, Any]:
    role_key = _resolve_role_key(user_role=user_role, domain=domain, question=question)
    pack = dict(_ROLE_PROMPT_PACKS.get(role_key) or _ROLE_PROMPT_PACKS["principal"])
    pack.pop("aliases", None)
    pack["role_key"] = role_key
    pack["disclaimer"] = _ROLE_DISCLAIMER
    return pack


def _resolve_role_key(*, user_role: str, domain: str, question: str) -> str:
    selected = _role_key_from_text(user_role)
    if selected:
        return selected
    text = str(question or "")
    if any(token in text for token in ("我", "我的", "本人")) and any(token in text for token in ("职称", "课时", "学时", "成果")):
        return "frontline_teacher"
    if any(token in text for token in ("孩子", "我家", "家长")):
        return "parent"
    return _DOMAIN_ROLE_MAP.get(domain, "principal")


def _role_key_from_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    candidates: list[tuple[int, str, str]] = []
    for key, pack in _ROLE_PROMPT_PACKS.items():
        names = [str(pack.get("role_name") or ""), *[str(item or "") for item in pack.get("aliases", [])]]
        for name in names:
            if name:
                candidates.append((len(name), key, name))
    for _length, key, name in sorted(candidates, reverse=True):
        if name in text:
            return key
    return ""


def _data_query_guidance(role_context: dict[str, Any]) -> dict[str, str]:
    return {
        "functional_areas": str(role_context.get("functional_areas") or ""),
        "data_response_logic": str(role_context.get("data_response_logic") or ""),
        "professional_focus": str(role_context.get("professional_focus") or ""),
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_text(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item or "").strip() for item in value if str(item or "").strip()]
    return []
