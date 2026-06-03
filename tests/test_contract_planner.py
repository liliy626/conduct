from __future__ import annotations

from gateway_core.agents.contracts.planner import ContractPlanner


class _Message:
    def __init__(self, content: str):
        self.content = content


class _StructuredFailure:
    def invoke(self, _messages):
        raise RuntimeError("BadRequestError: structured output schema is not supported")


class _StructuredAndPlainFailureModel:
    def with_structured_output(self, _schema):
        return _StructuredFailure()

    def invoke(self, _messages):
        raise RuntimeError("plain planner call failed")


class _StructuredFailsPlainSucceedsModel:
    def with_structured_output(self, _schema):
        return _StructuredFailure()

    def invoke(self, _messages):
        return _Message(
            '{"required_outputs":["plot_artifact","unknown_output"],'
            '"allowed_tools":["plot","not_available"],'
            '"answer_mode":"plot",'
            '"reason":""}'
        )


class _PlainCaptureModel:
    def __init__(self):
        self.messages = []

    def invoke(self, messages):
        self.messages = messages
        return _Message(
            '{"required_outputs":[],"allowed_tools":[],"answer_mode":"data",'
            '"answer_focus":"按表名总结学校业务领域","reason":"ok"}'
        )


class _PlainInvalidAnswerModeModel:
    def invoke(self, _messages):
        return _Message(
            '{"required_outputs":[],"allowed_tools":[],"answer_mode":"single",'
            '"answer_focus":"按表名总结学校业务领域","reason":"ok"}'
        )


class _PlainChatRouteModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"chat","required_outputs":[],"allowed_tools":[],"answer_mode":"text",'
            '"answer_focus":"普通寒暄直接回应","reason":"无需学校数据或工具"}'
        )


class _PlainInvalidRouteModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"maybe","required_outputs":[],"allowed_tools":[],"answer_mode":"data",'
            '"answer_focus":"基于学校运行数据找值得关注事项","reason":"ok"}'
        )


class _PlainEmptyPlanModel:
    def invoke(self, _messages):
        return _Message('{"required_outputs":[],"allowed_tools":[],"answer_mode":"data","answer_focus":"","reason":"ok"}')


class _PlainOverbroadWorkScheduleModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"data","required_outputs":[],"allowed_tools":["time","official_policy_search"],'
            '"answer_mode":"multi",'
            '"answer_focus":"汇总本周全校重点工作，包括全员导师活动、AI五育平台活动、公文通知、值班重点事项等",'
            '"reason":"重点工作可能涉及政策依据"}'
        )


class _PlainDictAnswerFocusModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"data","required_outputs":[],"allowed_tools":[],"answer_mode":"data",'
            '"answer_focus":{"P0":"本周学校有哪些重点工作安排？","P1":"公文通知","P2":null},'
            '"reason":"ok"}'
        )


class _PlainTeacherTeamDictAnswerFocusModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"data","required_outputs":[],"allowed_tools":["official_policy_search"],'
            '"answer_mode":"data",'
            '"answer_focus":{"P0":"用户问题：学校师资团队怎么样？",'
            '"P1":"从职称梯队、教师规模、学科结构、获奖与带教情况给出优化建议。"},'
            '"reason":"校长角色关注职称梯队和正式文件"}'
        )


class _PlainFocusArrayRequiredArtifactsModel:
    def invoke(self, _messages):
        return _Message(
            '{"route":"data","required_artifacts":["chart_artifact"],"allowed_tools":["chart"],'
            '"answer_mode":"multi",'
            '"answer_focus":[{"priority":"P0","target_content":"回答用户原问题：把本周请假数据做成交互图",'
            '"trigger_condition":"User Request"},'
            '{"priority":"P1","target_content":"补充请假原因分布",'
            '"trigger_condition":"P0 查询结果包含请假原因字段时"}]}'
        )


def test_contract_planner_uses_plain_json_fallback_after_structured_bad_request():
    plan = ContractPlanner(_StructuredFailsPlainSucceedsModel()).plan_turn(
        question="把本周请假数据画成图",
        available_tools=["plot", "time"],
    )

    assert plan.required_outputs == ["plot_artifact"]
    assert plan.allowed_tools == ["plot"]
    assert plan.answer_mode == "plot"
    assert "plain_json_fallback_after_structured_error" in plan.reason
    assert "BadRequestError" in plan.reason


def test_contract_planner_records_structured_and_plain_failure_details():
    plan = ContractPlanner(_StructuredAndPlainFailureModel()).plan_turn(
        question="今天教师请假人数是多少？",
        available_tools=["time"],
    )

    assert plan.required_outputs == []
    assert plan.allowed_tools == []
    assert plan.answer_mode == "data"
    assert "contract planner unavailable: RuntimeError: plain planner call failed" in plan.reason
    assert "structured_error=RuntimeError: BadRequestError" in plan.reason


def test_contract_planner_prompt_includes_catalog_business_history_and_memory_contexts():
    model = _PlainCaptureModel()

    plan = ContractPlanner(model).plan_turn(
        question="学校有哪些业务领域",
        conversation_context="上一轮问的是学校最近异常",
        metadata_catalog_context="强相关候选：zx_mlh.学生请假_请假申请",
        ddl_vector_context="schema=zx_mlh; vector_table=ddl_vector_documents; top_k=8",
        business_prompt_context="跨领域判断必须说明证据来源",
        sql_experience_context='{"manual_hint_count":1}',
        available_tools=["time"],
    )

    prompt = model.messages[-1].content
    assert "元数据目录快照" in prompt
    assert "强相关候选：zx_mlh.学生请假_请假申请" in prompt
    assert "DDL 向量检索配置" in prompt
    assert "vector_table=ddl_vector_documents" in prompt
    assert "业务提示词" in prompt
    assert "跨领域判断必须说明证据来源" in prompt
    assert "历史 SQL 经验检索" in prompt
    assert '{"manual_hint_count":1}' in prompt
    assert "记忆/会话上下文" in prompt
    assert "上一轮问的是学校最近异常" in prompt
    assert "route=data" in prompt
    assert "business_prompt_context" not in prompt
    assert "P0 原问题：学校有哪些业务领域" in plan.answer_focus
    assert "P1 可选扩展：按表名总结学校业务领域" in plan.answer_focus
    assert plan.route == "data"


def test_contract_planner_normalizes_unknown_answer_mode_but_keeps_answer_focus():
    plan = ContractPlanner(_PlainInvalidAnswerModeModel()).plan_turn(
        question="学校有哪些业务领域",
        available_tools=["time"],
    )

    assert plan.answer_mode == "data"
    assert "P0 原问题：学校有哪些业务领域" in plan.answer_focus
    assert "P1 可选扩展：按表名总结学校业务领域" in plan.answer_focus


def test_contract_planner_accepts_chat_route_from_llm():
    plan = ContractPlanner(_PlainChatRouteModel()).plan_turn(
        question="你好，帮我润色一句话",
        available_tools=["time"],
    )

    assert plan.route == "chat"
    assert plan.answer_mode == "text"
    assert plan.answer_focus == "普通寒暄直接回应"


def test_contract_planner_normalizes_unknown_route_to_data():
    plan = ContractPlanner(_PlainInvalidRouteModel()).plan_turn(
        question="有什么是我能关心的",
        available_tools=["time"],
    )

    assert plan.route == "data"
    assert "P0 原问题：有什么是我能关心的" in plan.answer_focus
    assert "P1 可选扩展：基于学校运行数据找值得关注事项" in plan.answer_focus


def test_contract_planner_keeps_work_schedule_focus_generic_without_hardcoded_expansion():
    plan = ContractPlanner(_PlainOverbroadWorkScheduleModel()).plan_turn(
        question="本周学校有哪些重点工作安排？",
        available_tools=["time", "official_policy_search", "web_search"],
    )

    assert plan.route == "data"
    assert plan.required_outputs == []
    assert plan.allowed_tools == ["time"]
    assert "P0 原问题" in plan.answer_focus
    assert "本周学校有哪些重点工作安排？" in plan.answer_focus
    assert "P0 首轮策略" in plan.answer_focus
    assert "P1 可选扩展" in plan.answer_focus
    assert "触发条件" in plan.answer_focus
    assert "全员导师" in plan.answer_focus
    assert "AI五育" in plan.answer_focus
    assert plan.answer_focus.index("本周学校有哪些重点工作安排？") < plan.answer_focus.index("全员导师")
    assert "日程录入" not in plan.answer_focus
    assert "周计划" not in plan.answer_focus
    assert "official_policy_search" not in plan.allowed_tools


def test_contract_planner_accepts_dict_answer_focus_from_llm():
    plan = ContractPlanner(_PlainDictAnswerFocusModel()).plan_turn(
        question="本周学校有哪些重点工作安排？",
        available_tools=["time"],
    )

    assert plan.route == "data"
    assert "P0 原问题" in plan.answer_focus
    assert "本周学校有哪些重点工作安排？" in plan.answer_focus
    assert "contract planner unavailable" not in plan.reason


def test_contract_planner_prioritizes_teacher_team_question_and_ignores_role_policy_expansion():
    plan = ContractPlanner(_PlainTeacherTeamDictAnswerFocusModel()).plan_turn(
        question="学校师资团队怎么样？",
        business_prompt_context=(
            '{"evidence":[{"content":{"role_context":{"role_name":"校长/书记",'
            '"professional_focus":"统筹教师职称存量、空岗名额、符合晋升人员清单；熟悉正式文件。"}}}]}'
        ),
        available_tools=["time", "official_policy_search", "web_search"],
    )

    assert plan.route == "data"
    assert plan.required_outputs == []
    assert plan.allowed_tools == []
    assert "contract planner unavailable" not in plan.reason
    assert "P0 原问题：学校师资团队怎么样？" in plan.answer_focus
    assert "P0 首轮策略" in plan.answer_focus
    assert "P1 可选扩展" in plan.answer_focus
    assert "触发条件" in plan.answer_focus
    assert "角色提示词" in plan.answer_focus
    assert "official_policy_search" not in plan.allowed_tools


def test_contract_planner_accepts_focus_array_and_required_artifacts_schema():
    plan = ContractPlanner(_PlainFocusArrayRequiredArtifactsModel()).plan_turn(
        question="把本周请假数据做成交互图",
        available_tools=["chart", "time"],
    )

    assert plan.route == "data"
    assert plan.required_outputs == ["chart_artifact"]
    assert plan.allowed_tools == ["chart"]
    assert plan.answer_mode == "multi"
    assert "P0 原问题" in plan.answer_focus
    assert "把本周请假数据做成交互图" in plan.answer_focus
    assert "P1 可选扩展：补充请假原因分布" in plan.answer_focus
    assert "P1 触发条件：P0 查询结果包含请假原因字段时" in plan.answer_focus


def test_contract_planner_uses_business_prompt_to_allow_policy_evidence_tools():
    plan = ContractPlanner(_PlainEmptyPlanModel()).plan_turn(
        question="今年教师高级职称申报依据和要求是什么？",
        business_prompt_context=(
            '{"evidence":[{"content":{"role_context":{"role_name":"校办&人事干事",'
            '"professional_focus":"依托上海市教师专业发展信息管理平台规则作答，熟悉正式文件和职称校内初审。"}}}]}'
        ),
        available_tools=["time", "official_policy_search", "web_search"],
    )

    assert "official_policy_search" in plan.allowed_tools
    assert "policy_evidence" in plan.required_outputs
    assert "业务提示词" in plan.reason


def test_contract_planner_uses_question_to_require_web_evidence_for_latest_links():
    plan = ContractPlanner(_PlainEmptyPlanModel()).plan_turn(
        question="请查官网最新链接，确认上海综评填报要求",
        business_prompt_context=(
            '{"evidence":[{"content":{"role_context":{"role_name":"教务处&教务员",'
            '"professional_focus":"遵循上海综评制度、新课标要求。"}}}]}'
        ),
        available_tools=["time", "official_policy_search", "web_search"],
    )

    assert "official_policy_search" in plan.allowed_tools
    assert "web_search" in plan.allowed_tools
    assert "policy_evidence" in plan.required_outputs
    assert "web_evidence" in plan.required_outputs
