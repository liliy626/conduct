from __future__ import annotations

from gateway_core.prompts.list_query_signals import PEOPLE_LIST_QUERY_TOKENS


def build_data_format_guard(
    *,
    question: str,
    allow_record_examples: bool,
    sensitive_domain: bool,
    school_scope: str = "",
) -> str:
    format_guard = (
        "回答格式要求：默认只用中文自然语言回答，"
        "不要输出JSON对象/数组，也不要输出```json代码块。"
        "只有当用户明确要求JSON时才输出结构化JSON。"
        "回答结构默认采用“简短结论 + 数据支撑”。"
        "结论控制在1到2段，避免整篇都写成长篇汇报总结。"
        "数据支撑部分必须出现，优先用更直观的展示形式：Markdown表格、编号列表或紧凑分组小节。"
        "如果检索结果里存在记录列表或分组结果，不要只做纯文字转述，必须把关键记录或关键分布直接展示出来。"
        "必须只回答用户当前最后一条问题；除非用户明确要求回顾历史，否则不要引用前序轮次的问题或结论。"
        "若前序对话与当前检索结果冲突，一律以当前检索结果为准并直接说明。"
        "严禁编造检索结果中不存在的时间、数量、字段值或记录内容；"
        "信息缺失时可写“未提供”；只有在检索结果JSON里关键计数为0且记录列表为空时，才允许写“未检索到/暂无数据”。"
        "不得把“本次查询未返回某字段”表述为“系统没有该字段/系统不支持该明细”；如需更多细节，应明确建议补充问法。"
        "网关会自动在回答开头注入“应用名/表单名（仅在可识别时）”行，你不要重复输出同一行。"
    )
    if not allow_record_examples:
        format_guard += (
            "如果检索结果(JSON)里的记录列表没有显式允许展示样例，请只把它们作为理解上下文使用，"
            "不要逐条复述记录样例，优先输出聚合结论、关键分布或安全表格。"
        )

    q = str(question or "")
    if any(token in q for token in PEOPLE_LIST_QUERY_TOKENS):
        format_guard += (
            "名单回答要求：当用户问“哪些人/名单/姓名/都有谁”时，必须优先从JSON中的"
            "sample_rows、current_unreturned_students等记录列表逐条提取姓名作答，"
            "并尽量附带班级和请假类型；不得只回答总人数。"
            "若字段较稳定，优先使用Markdown表格呈现名单。"
            "如果记录列表为空，才可明确说明未检索到名单。"
        )

    structured_query_tokens = [
        "明细",
        "详情",
        "具体",
        "具体数据",
        "完整数据",
        "完整明细",
        "详细数据",
        "全部数据",
        "全部记录",
        "所有记录",
        "相关数据",
        "有哪些",
        "哪几条",
        "记录",
        "排行",
        "排名",
        "top",
        "top ",
        "对比",
        "同比",
        "环比",
        "趋势",
        "分布",
        "构成",
        "出勤率",
        "到岗率",
        "异常统计",
        "清单",
    ]
    if any(token in q.lower() for token in structured_query_tokens):
        structured_body = (
            "优先整理成Markdown表格；只有列不稳定或字段缺失很多时，才改用编号列表。"
            "表格或列表中至少展示时间范围、样本量，以及最关键的3到10条记录/分组结果。"
            if allow_record_examples
            else "优先展示聚合指标、时间范围、样本量和关键分布；不要把原始记录样例逐条搬到最终答案里。"
        )
        format_guard += (
            "结构化展示要求：当用户问明细、记录、排行、对比、分布、出勤率、异常统计等问题时，"
            "在给出结论后，必须补充“数据支撑”小节。"
            "若JSON里有 sample_rows、rows、result_rows、detail_rows、records、items、top_lists、distributions 等可用结果，"
            + structured_body
            + "若使用Markdown表格，必须逐行换行输出（表头、分隔行、每条数据各占一行），禁止把整张表压成单行。"
        )

    portrait_tokens = [
        "班级画像",
        "年级画像",
        "班级概况",
        "年级概况",
        "班级综合分析",
        "年级整体表现",
        "健康度画像",
        "全景画像",
    ]
    if any(token in q for token in portrait_tokens):
        format_guard += (
            "画像回答要求：必须按固定结构输出。"
            "班级画像使用“标签展示 -> 现状扫描 -> 深度洞察 -> 行动建议”；"
            "年级画像使用“年级关键指标看板 -> 班级差异矩阵 -> 风险预警 -> 年级管理策略”。"
            "每一部分都要给出来自检索结果的真实数据佐证；"
            "至少引用3个可核对指标（如人数、记录数、占比、变化值、Top分布）。"
            "若某维度无记录，必须明确写“当前维度无记录/样本不足”，不得臆测。"
        )

    student_portrait_tokens = ["学生画像", "同学画像", "学生档案", "学生情况"]
    if any(token in q for token in student_portrait_tokens):
        format_guard += (
            "学生画像回答要求：输出结构固定为“核心人格标签 -> 行为规律总结 -> 能力象限评估 -> 教育建议”。"
            "每一部分都必须给出数据佐证，优先引用请假次数、离校时间分布、活动/作品记录数等可核对指标。"
        )

    teacher_portrait_tokens = ["教师画像", "老师画像", "教师情况", "老师情况", "教师档案", "老师档案"]
    if any(token in q for token in teacher_portrait_tokens):
        format_guard += (
            "教师画像回答要求：输出结构固定为“教师画像摘要 -> 职业负荷雷达图建议 -> 学生视角还原 -> 管理策略”。"
            "每一部分都必须给出数据佐证，优先引用请假/执勤/晨午检/教研/工作安排/评价样本量等可核对指标。"
        )

    evidence_query_tokens = ["心理", "预警", "风险", "个案", "一级预警", "二级预警", "三级预警"]
    if any(token in q for token in evidence_query_tokens) and allow_record_examples:
        format_guard += (
            "证据回答要求：当问题涉及心理健康/预警/风险/个案时，"
            "在给出总体结论后，必须补充“记录样例”小节，至少列出3条来自JSON记录列表的真实样例。"
            "样例优先从 sample_rows、rows、result_rows、detail_rows、records、items 提取。"
            "每条尽量包含：姓名、班级、预警等级、触发因素（或描述）、记录时间；"
            "若字段缺失请明确写“未提供”，不得编造。"
        )
    if sensitive_domain:
        format_guard += (
            "敏感域回答要求：严禁把半结构化原始记录串、管道分隔字段或原始样例文本原样复述到最终答案里。"
            "如果需要说明个体情况，只能基于安全字段做整理后的自然语言或表格表达。"
        )
    if school_scope:
        format_guard += (
            f"数据隔离要求：本次只允许使用学校范围「{school_scope}」的数据。"
            "如果证据无法确认学校归属，必须明确说明无法安全回答。"
        )
    return format_guard
