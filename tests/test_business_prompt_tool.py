from __future__ import annotations

from gateway_core.tools.business_prompt_tool import BusinessPromptContextTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext


def _content_for(question: str) -> dict:
    output = BusinessPromptContextTool().run(
        AgentToolInput(arguments={"question": question}),
        ToolExecutionContext(tenant_id="sch_zx_mlh"),
    )
    return output.to_dict()["evidence"][0]["content"]


def _content_for_args(args: dict) -> dict:
    output = BusinessPromptContextTool().run(
        AgentToolInput(arguments=args),
        ToolExecutionContext(tenant_id="sch_zx_mlh"),
    )
    return output.to_dict()["evidence"][0]["content"]


def test_business_prompt_context_uses_teacher_profile_boundary_for_teacher_portrait() -> None:
    content = _content_for("学校的整体教师画像")

    assert content["domain"] == "teacher_profile"
    assert any("教师总数" in item and "教师userid" in item for item in content["evidence_boundaries"])
    assert any("请假" in item and "不宜简单加总" in item for item in content["evidence_boundaries"])
    assert any("评教" in item and "学期" in item for item in content["evidence_boundaries"])
    assert any("个人敏感" in item for item in content["evidence_boundaries"])


def test_business_prompt_context_keeps_teacher_development_for_title_or_honor_questions() -> None:
    content = _content_for("教师职称荣誉申报情况怎么样？")

    assert content["domain"] == "teacher_development"
    assert any("积分" in item or "成果" in item for item in content["evidence_boundaries"])


def test_business_prompt_context_returns_principal_role_prompt_pack() -> None:
    content = _content_for_args({"question": "学校最近有什么异常", "user_role": "校长"})

    role_context = content["role_context"]
    assert content["domain"] == "school_operations"
    assert role_context["role_name"] == "校长/书记"
    assert "全校统筹管理" in role_context["functional_areas"]
    assert "同比环比" in role_context["data_response_logic"]
    assert "职称存量" in role_context["professional_focus"]
    assert "结论+优化管理建议" in role_context["data_response_logic"]
    assert "正式文件" in role_context["disclaimer"]


def test_business_prompt_context_maps_psychology_question_to_psychology_role() -> None:
    content = _content_for("最近心理预警和个案有什么风险？")

    role_context = content["role_context"]
    assert content["domain"] == "psychological_health"
    assert role_context["role_name"] == "心理老师"
    assert "心理危机干预" in role_context["functional_areas"]
    assert "高危学生单独标记" in role_context["data_response_logic"]
    assert "危机上报层级" in role_context["professional_focus"]


def test_business_prompt_context_selects_parent_role_when_requested() -> None:
    content = _content_for_args({"question": "孩子最近在校情况怎么样？", "user_role": "学生家长"})

    role_context = content["role_context"]
    assert role_context["role_name"] == "学生家长"
    assert "子女考勤就餐查询" in role_context["functional_areas"]
    assert "政策通俗化解读" in role_context["data_response_logic"]
