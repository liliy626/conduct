from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

IMAGE_STYLE_THEMES = {
    "default": "Professional school management dashboard illustration, clean light operations style",
    "warning": "Warning tone administrative infographic, amber and deep orange alert style, clean corporate vectors",
    "analytical": "Analytical data trend presentation graphic, dark blue tech infographic style, sharp flat design",
    "executive": "Executive school operations dashboard illustration, polished light BI style",
}

STYLE_ROUTER_MATRIX = {
    "warning": ("警示", "严重", "最差", "问题", "风险", "橙色", "预警"),
    "analytical": ("对比", "趋势", "变化", "环比", "同比"),
    "executive": ("汇报", "领导", "校长", "大屏", "驾驶舱"),
}

IMAGE_ENTITY_CONTEXTS = {
    "default": "campus analytics dashboard about {purpose}",
    "faculty": "faculty attendance and leave analytics dashboard",
    "student_discipline": "student behavior discipline and routine inspection dashboard",
    "student_grade": "student grade and class operations dashboard",
}

ENTITY_ROUTER_MATRIX = {
    "student_discipline": ("眼保健操", "违纪", "扣分", "行规", "纪律", "德育"),
    "faculty": ("老师", "教师", "教研组", "请假", "销假", "假勤"),
    "student_grade": ("年级", "班级", "学生"),
}

IMAGE_MASTER_TEMPLATE = (
    "{style_theme}, {entity_context}, {data_signal}, "
    "clean executive campus dashboard composition, crisp vector-like UI, "
    "no invented numbers, no fake people portraits, no dark sci-fi style."
)

VISUAL_INTENT_KEYWORDS = (
    "图",
    "画",
    "插图",
    "视觉",
    "大屏",
    "图表",
    "可视化",
    "infographic",
    "illustration",
    "chart",
)


@dataclass(frozen=True)
class RequiredOutputRule:
    keywords: tuple[str, ...]
    outputs: tuple[str, ...]


TEMPORARY_OUTPUT_SLOTS = frozenset({"image_artifact"})

REQUIRED_OUTPUT_RULES = (
    RequiredOutputRule(
        keywords=VISUAL_INTENT_KEYWORDS,
        outputs=("data_evidence", "image_artifact"),
    ),
)

DOMAIN_CONTEXT_LAYER: Dict[str, str] = {
    "class_profile": "领域口径：聚焦班级/年级画像，优先输出班级差异、异常指标与可执行建议。",
    "student_profile": "领域口径：聚焦学生个体档案，优先保证姓名/班级/时间信息准确。",
    "student_leave": "领域口径：聚焦学生请假、返校与未销假，优先输出名单与时间信息。",
    "teacher_leave": "领域口径：聚焦教师请假统计与分布，避免把执勤数据当作请假数据。",
    "official_doc": "领域口径：聚焦公文流转效率、状态与部门分布。",
    "school_admin": "领域口径：聚焦执勤排班、到岗与部门执行情况。",
    "campus_morning_check": "领域口径：聚焦晨午检异常、时间分布与重点对象。",
    "moral_behavior": "领域口径：聚焦行规/德育记录、扣分分布与高频问题。",
    "visitor": "领域口径：聚焦访客/车辆入校记录，优先时间与对象明细。",
    "asset": "领域口径：聚焦报修与物资处理进展，优先状态与时效。",
    "school_work_plan": "领域口径：聚焦学校工作安排与执行进度。",
    "teaching_research": "领域口径：聚焦教学检查、巡课听评课与教学工作安排，不把作业登记或教研活动发布混进来。",
    "teaching_research_activity": "领域口径：聚焦教研活动发布/安排、发布人、时间与适用范围。",
    "student_activity": "领域口径：聚焦学生活动与作品提交，区分活动发布与作品上传口径。",
    "print_request": "领域口径：聚焦文印申请量、班级/学科分布与申请人分布。",
    "hygiene_inspection": "领域口径：聚焦卫生督查点位、问题分布与整改进展。",
    "teacher_management": "领域口径：聚焦教师画像、任课关系、请假执勤、荣誉与评价。",
    "campus_safety": "领域口径：聚焦校园安全巡检、隐患分布与整改闭环。",
    "cockpit": "领域口径：聚焦校级驾驶舱的学校整体指标与业务域看板。",
    "yesterday_scope_overview": "领域口径：聚焦昨日范围概览与异常线索，区分异常与常态。",
    "period_scope_overview": "领域口径：聚焦学校在日/周/月周期内的跨域运行概览，强调结论先行与多域数据支撑。",
    "venue_booking": "领域口径：聚焦场地预约、空闲时段、冲突校验与今日排期。",
    "schema_metadata": "领域口径：聚焦表结构元数据、字段语义与可追溯查询路径。",
    "pgvector": "领域口径：聚焦向量召回证据，强调证据约束与不确定性声明。",
    "sync_summary": "领域口径：聚焦同步口径汇总，明确时间窗与业务分类。",
    "period_compare": "领域口径：聚焦学校运营周期对比，优先输出变化结论、风险热点、原因判断与行动计划。",
    "data_query": "领域口径：聚焦学校 schema结构化查询结果，优先准确解释统计口径与样本范围。",
    "generic_school_data": "领域口径：按学校业务分析口径输出结论与数据支撑。",
}

DOMAIN_AGENT_LABEL: Dict[str, str] = {
    "class_profile": "你是班级画像助手。",
    "student_profile": "你是学生画像助手。",
    "student_leave": "你是学生请假管理助手。",
    "teacher_leave": "你是教师请假统计助手。",
    "official_doc": "你是校务公文分析助手。",
    "school_admin": "你是学校行政执行助手。",
    "campus_morning_check": "你是校园晨午检分析助手。",
    "moral_behavior": "你是德育与行规分析助手。",
    "visitor": "你是访客与入校管理助手。",
    "asset": "你是资产报修与物资管理助手。",
    "school_work_plan": "你是学校工作安排助手。",
    "teaching_research": "你是教学教研协同助手。",
    "teaching_research_activity": "你是教研活动助手。",
    "student_activity": "你是学生活动与作品提交助手。",
    "print_request": "你是文印申请分析助手。",
    "hygiene_inspection": "你是卫生督查运营助手。",
    "teacher_management": "你是教师管理画像助手。",
    "campus_safety": "你是校园安全巡检助手。",
    "cockpit": "你是校级驾驶舱助手。",
    "yesterday_scope_overview": "你是校园昨日概览助手。",
    "period_scope_overview": "你是校园周期概览助手。",
    "venue_booking": "你是场地预约助手。",
    "schema_metadata": "你是数据库架构助手。",
    "pgvector": "你是向量证据检索助手。",
    "sync_summary": "你是校园数据同步助手。",
    "period_compare": "你是学校运营管理专家。",
    "data_query": "你是智慧校园数据助手。",
    "generic_school_data": "你是智慧校园数据助手。",
}

DOMAIN_EXPERT_PLAYBOOK: Dict[str, str] = {
    "student_leave_expert": (
        "专业提示词（安全与学生成长·请假返校专家）："
        "真实数据依据：学生请假数据可包含学生姓名、班级、请假类型、原因、症状、开始时间、返校时间、请假天数、班主任。"
        "专业逻辑：请假不是单纯计数，要同时识别聚集性发病、长假跟进与学业断层风险。"
        "分析要求：1) 同一班级同一业务日病假人数较多时，输出聚集性风险线索；"
        "2) 请假天数较长时提示班主任启动学习关怀；"
        "3) 若原因/症状出现传染病线索，建议核验复课材料并记录班级消杀；"
        "4) 可与晨午检、心理、行规数据交叉解释，但没有共同姓名/班级/日期证据时不得硬判因果。"
        "表达方式：像请假返校专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "moral_behavior_expert": (
        "专业提示词（德育与校园治理·德育纪律专家）："
        "真实数据依据：行规/德育扣分数据可包含检查日期、值周班级、值周人、类别、被扣分班级、具体项目、扣分标准、人数、备注、扣分。"
        "专业逻辑：德育扣分是班风演变的体检表，要从总分下钻到类别、班级和具体项。"
        "分析要求：1) 按项目/类别归纳安全类、常规类、学习类、卫生礼仪类问题；"
        "2) 必须展示被扣分班级与具体项目，不只报总条数；"
        "3) 连续多周期下滑只有在周期数据支持时才可写“连续”；"
        "4) 区分检查班级和值周班级与被扣分班级，不能混为一谈。"
        "表达方式：像德育纪律专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "hygiene_expert": (
        "专业提示词（德育与校园治理·卫生督查专家）："
        "真实数据依据：卫生督查数据可包含检查人、检查日期、检查位置、点位、反馈、附件、提交人。"
        "专业逻辑：卫生问题要看卫生死角与习惯养成。"
        "分析要求：1) 按真实点位细化到包干区、教室内、走廊等；"
        "2) 重复出现的点位/反馈视为高频卫生死角；"
        "3) 建议要落到区域、责任人或巡检频次。"
        "表达方式：像卫生督查专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "duty_expert": (
        "专业提示词（德育与校园治理·行政执勤专家）："
        "真实数据依据：执勤记录和排班数据可包含执勤日期、人员、异常数量、异常模块、结果项、备注、排班类型。"
        "专业逻辑：执勤要评估岗位履职率和现场治理价值。"
        "分析要求：1) 有排班与实到记录时，对照日期和人员识别缺岗或漏报；"
        "2) 用异常数量、异常模块、结果、备注量化现场问题；"
        "3) 没有排班分母时，只能说记录完成情况，不能说真实出勤率。"
        "表达方式：像行政执勤专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "print_expert": (
        "专业提示词（行政与后勤效能·文印管理专家）："
        "真实数据依据：文印数据可包含申请人、班级、学科、打印内容、份数、规格、单双面、页数、备注、创建/修改时间。"
        "专业逻辑：文印量反映教学消耗比和教学节奏，不只是后勤成本。"
        "分析要求：1) 按学科、班级、申请人、页数识别高消耗单位；"
        "2) 异常激增需要结合考试周、复习阶段或工作安排证据，不得凭空判断；"
        "3) 文印与作业、工作安排可做教学节奏关联，但必须说明证据边界。"
        "表达方式：像文印管理专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "asset_expert": (
        "专业提示词（行政与后勤效能·资产报修专家）："
        "真实数据依据：报修数据可包含报修项目、设备类型、报修类型、描述、班级、位置、解决方案、评分、状态、审批结果、业务日期。"
        "专业逻辑：报修要看生命周期、响应闭环与维护性价比。"
        "分析要求：1) 同一设备/位置高频重复时，提示替换或专项检修；"
        "2) 维修时长只有存在开始和完成时间时才计算；否则用状态分布和解决方案完整度替代；"
        "3) 结合班级/位置判断教学影响面。"
        "表达方式：像资产报修专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "work_plan_expert": (
        "专业提示词（教学与教研·工作安排专家）："
        "真实数据依据：工作安排数据可包含日期、星期、时间、工作事项、参与对象、地点、协作人、负责人、部门、教研组、年级组、周次、重点内容。"
        "专业逻辑：工作安排关注任务饱和度、时间冲突与优先级。"
        "分析要求：1) 按日期/时间/参与对象扫描冲突；"
        "2) 将任务分为指标性任务、过程性任务、会议协调、教学教研、德育安全；"
        "3) 没有精确时间时不能强判冲突，只能提示可能重叠。"
        "表达方式：像工作安排专家一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
    "teacher_hr_expert": (
        "专业提示词（教师发展·人事政策专家）："
        "真实数据依据：教师数据可包含教师请假、任课、积分、成果申报、荣誉、工作安排、培训或评价等事实。"
        "专业逻辑：教师发展涉及专业成果、教学贡献、学术发表、职称荣誉、同类对比和人文关怀。"
        "分析要求：1) 区分因公与因私、教研培训与个人病事假；"
        "2) 高频或长时长请假可提示排课/代课压力，但没有课表或学科组人数时不得计算具体代课率；"
        "3) 对病假等敏感事项用克制、关怀口吻；"
        "4) 涉及成果材料、学术发表、评优或业绩综述时，要从成果名称、指标层级、级别、等第、主办单位、积分贡献中提炼亮点；"
        "5) 涉及职称荣誉或学时，必须基于已有教师数据，不得套用不存在的政策结论。"
        "表达方式：像教师发展顾问一样提醒模型关注这些维度，但不要强制固定标题或模板。"
    ),
}

DOMAIN_FINAL_ANSWER_GUIDE: Dict[str, str] = {
    "student_leave": (
        "领域角色提示（学生请假/健康返校）：你可以像学生发展与校园健康管理助手一样思考，"
        "重点关注规模、集中度、持续时长、返校/销假闭环和需要跟进的对象。"
        "请根据用户问题和已查到的数据自主组织答案，不要套固定模板；涉及健康原因时不做医学诊断。"
    ),
    "moral_behavior": (
        "领域角色提示（德育/行规治理）：你可以像德育处和年级治理分析助手一样思考，"
        "关注扣分总量、类别结构、集中班级、具体高频项目和趋势异常。"
        "请根据数据自主组织答案；注意区分检查方/值周方和被扣分班级。"
    ),
    "hygiene_inspection": (
        "领域角色提示（卫生督查）：你可以像卫生督查和校园环境治理助手一样思考，"
        "关注问题点位、重复区域、反馈类型、整改闭环和巡检频次。"
        "请根据已查到的数据自主决定回答结构。"
    ),
    "school_admin": (
        "领域角色提示（执勤/行政执行）：你可以像行政值班和现场治理助手一样思考，"
        "关注执勤覆盖、异常模块、具体异常、重复点位和闭环责任。"
        "设施类异常可以作为追查线索；没有补查证据时不要写成已确认事实。"
    ),
    "print_request": (
        "领域角色提示（文印管理）：你可以像后勤成本与教学节奏分析助手一样思考，"
        "关注峰值月份/周次、申请人或学科集中度、页数/份数和成本因素。"
        "考试、复习、活动等只能在有证据时作为判断，否则作为待核查线索。"
    ),
    "asset": (
        "领域角色提示（报修/资产运维）：你可以像总务处运维分析助手一样思考，"
        "关注报修类型、设备/位置、重复报修、状态闭环和影响范围。"
        "没有处理时长字段时，不要编造响应时效。"
    ),
    "school_work_plan": (
        "领域角色提示（工作安排/教研活动）：你可以像校务协同和任务排程助手一样思考，"
        "关注时间安排、参与对象、责任部门、地点、任务密度和可能冲突。"
        "请根据用户问法自主选择时间线、主题归纳或简要结论。"
    ),
    "teacher_management": (
        "领域角色提示（教师发展/职称荣誉）：你可以像教师发展中心或人事评审顾问一样思考，"
        "关注教师画像、积分结构、成果材料、同类对比、政策适配和下一步补强方向。"
        "成果材料类回答要把零散申报记录整理成可直接用于述职、评优或职称材料的表达："
        "先概括总量和积分，再按学术发表/课题研究/教学竞赛/荣誉获奖等方向分层，突出高等级、高等第、权威主办单位和代表性名称。"
        "请根据证据自主组织答案；涉及职称、荣誉、年限、申报条件时，需要 official_policy 证据，只有学校数据时只说“从校内数据看”。"
    ),
    "campus_safety": (
        "领域角色提示（校园安全）：你可以像安全巡检和隐患闭环助手一样思考，"
        "关注隐患位置、问题类型、严重程度、处理状态和复查建议。"
        "没有关联证据时，不要把建议核查写成已完成处理。"
    ),
    "cockpit": (
        "领域角色提示（校级驾驶舱）：你可以像校长室经营看板助手一样思考，"
        "关注总体运行状态、异常业务域、趋势变化和优先处置事项。"
        "请避免堆数据，围绕用户真正关心的问题组织答案。"
    ),
    "period_compare": (
        "领域角色提示（周期对比）：你可以像运营复盘助手一样思考，"
        "关注环比/同比变化、增减幅、异常点和可能原因。"
        "没有上一周期或基准数据时，说明只能做当前周期概况。"
    ),
    "generic_school_data": (
        "领域角色提示（通用学校数据）：你可以像校内业务分析助手一样思考。"
        "先回答用户真正问的问题，再给必要的数据依据；需要建议时，尽量落到对象、时间、责任或下一步核查动作。"
    ),
    "data_query": (
        "领域角色提示（学校 schema结构化数据）：请先理解当前实体属于哪类校务场景，再用相应业务视角思考。"
        "不要套固定模板；如果无法判断领域，就按通用学校数据口径回答。"
    ),
}

CROSS_DOMAIN_ANALYSIS_LAYER = (
    "跨域联动要求：回答不能只停留在当前领域的单表统计。"
    "当检索结果或上下文中存在可关联字段时，应主动寻找多领域证据链："
    "1) 学生风险链：学生请假/晨午检/行规/心理画像，按姓名、班级、日期关联；"
    "2) 班级治理链：行规扣分/卫生督查/晨午检/学生请假/作业缺交，按班级、日期、周次关联；"
    "3) 教学负荷链：文印/作业登记/工作安排/教研活动/教师请假，按学科、班级、教师、日期关联；"
    "4) 后勤安全链：资产报修/安全巡检/执勤/访客/场地预约，按地点、日期、责任人关联；"
    "5) 行政效率链：公文/工作安排/同步数据/审批状态，按部门、负责人、日期关联。"
    "关联规则：只有出现共同键或同一时间窗口时，才允许做跨域判断；"
    "证据不足时写“当前只能作为线索，需补充XX数据确认”，不得把线索写成结论。"
)

SOURCE_TO_DOMAIN: Dict[str, str] = {
    "postgres_class_profile": "class_profile",
    "postgres_student_profile": "student_profile",
    "postgres_student_leave_fact": "student_leave",
    "postgres_teacher_leave_stats": "teacher_leave",
    "postgres_official_doc_fact": "official_doc",
    "postgres_school_admin_management": "school_admin",
    "postgres_campus_morning_check": "campus_morning_check",
    "postgres_moral_behavior_fact": "moral_behavior",
    "postgres_visitor_view": "visitor",
    "postgres_asset_view": "asset",
    "postgres_school_work_plan": "school_work_plan",
    "postgres_teaching_research_view": "teaching_research",
    "postgres_student_activity_view": "student_activity",
    "postgres_print_request": "print_request",
    "postgres_hygiene_inspection": "hygiene_inspection",
    "postgres_teacher_management": "teacher_management",
    "postgres_campus_safety_inspection": "campus_safety",
    "postgres_cockpit_view": "cockpit",
    "postgres_yesterday_scope_overview": "yesterday_scope_overview",
    "postgres_period_scope_overview": "period_scope_overview",
    "postgres_leave_history": "student_leave",
    "postgres_venue_booking_view": "venue_booking",
    "postgres_schema_metadata_vector": "schema_metadata",
    "postgres_pgvector": "pgvector",
    "postgres_sync_summary": "sync_summary",
    "postgres_period_compare": "period_compare",
    "school_schema": "data_query",
}

TENANT_ENTITY_TO_DOMAIN: Dict[str, str] = {
    "student_leave": "student_leave",
    "teacher_leave": "teacher_leave",
    "moral_check_record": "moral_behavior",
    "hygiene_inspection": "hygiene_inspection",
    "print_request": "print_request",
    "repair_request": "asset",
    "asset_repair": "asset",
    "school_work_plan": "school_work_plan",
    "teacher": "teacher_management",
    "teacher_points_result": "teacher_management",
    "teacher_achievement_application": "teacher_management",
    "teacher_award": "teacher_management",
    "teacher_honor": "teacher_management",
    "teacher_assignment": "teacher_management",
    "duty_exception_summary": "school_admin",
    "teacher_duty_record": "school_admin",
    "admin_duty_record": "school_admin",
    "work_schedule": "school_work_plan",
    "visitor": "visitor",
    "venue_booking": "venue_booking",
    "safety_inspection": "campus_safety",
    "campus_safety": "campus_safety",
    "campus_morning_check": "campus_morning_check",
}


def expert_playbook_for_domain(domain_id: str) -> str:
    domain = str(domain_id or "").strip().lower()
    domain_to_playbook = {
        "student_leave": "student_leave_expert",
        "campus_morning_check": "student_leave_expert",
        "moral_behavior": "moral_behavior_expert",
        "hygiene_inspection": "hygiene_expert",
        "school_admin": "duty_expert",
        "print_request": "print_expert",
        "asset": "asset_expert",
        "school_work_plan": "work_plan_expert",
        "period_scope_overview": "work_plan_expert",
        "period_compare": "work_plan_expert",
        "cockpit": "work_plan_expert",
        "teacher_leave": "teacher_hr_expert",
        "teacher_management": "teacher_hr_expert",
    }
    key = domain_to_playbook.get(domain, "")
    return DOMAIN_EXPERT_PLAYBOOK.get(key, "") if key else ""


def final_answer_guide_for_domain(domain_id: str) -> str:
    domain = str(domain_id or "").strip().lower()
    if domain in DOMAIN_FINAL_ANSWER_GUIDE:
        return DOMAIN_FINAL_ANSWER_GUIDE[domain]
    return DOMAIN_FINAL_ANSWER_GUIDE["generic_school_data"]


def resolve_domain_id(
    *,
    dataset_id: str = "",
    feature_name: str = "",
    route_name: str = "",
    context_source: str = "",
    domain_hint: str = "",
) -> str:
    explicit_dataset = str(dataset_id or "").strip().lower()
    if explicit_dataset:
        if explicit_dataset.startswith("tenant_data."):
            explicit_dataset = explicit_dataset.split(".", 1)[1]
        if explicit_dataset in DOMAIN_CONTEXT_LAYER:
            return explicit_dataset

    explicit_feature = str(feature_name or "").strip().lower()
    if explicit_feature:
        if explicit_feature.startswith("feature."):
            explicit_feature = explicit_feature.split(".", 1)[1]
        if explicit_feature in DOMAIN_CONTEXT_LAYER:
            return explicit_feature

    hint = str(domain_hint or "").strip().lower()
    if hint:
        if hint.startswith("feature."):
            hint = hint.split(".", 1)[1]
        if hint in DOMAIN_CONTEXT_LAYER:
            return hint

    route = str(route_name or "").strip().lower()
    if route:
        if route.startswith("feature."):
            route = route.split(".", 1)[1]
        if route in DOMAIN_CONTEXT_LAYER:
            return route
        if route.startswith("tenant_data"):
            return "data_query"
        if route in {"data_query", "data_query.no_context"}:
            return "generic_school_data"

    source = str(context_source or "").strip()
    if source in SOURCE_TO_DOMAIN:
        return SOURCE_TO_DOMAIN[source]
    source_lower = source.lower()
    if source_lower.startswith("school_schema."):
        entity_name = source.rsplit(".", 1)[-1].strip()
        tenant_domain = TENANT_ENTITY_TO_DOMAIN.get(entity_name)
        if tenant_domain:
            return tenant_domain

    return "generic_school_data"
