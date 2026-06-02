import pytest

from gateway_core.agents.universal_hub.models import SkillEvent
from gateway_core.api.openai_compat.adapter import _openai_chunks_for_event
from gateway_core.agents.school_sql.agent_stream import _fast_final_answer_system_prompt


def test_supervisor_content_routes_to_reasoning_content_chunk():
    """用例 1：Supervisor 决策节点的内部 content 输出必须被反向劫持为思考流 chunk"""
    event = SkillEvent(
        event_type="content",
        data={"text": "任务拆解完成，正在调度学校SQL技能专家工具"},
        metadata={"langgraph_node": "supervisor"},
    )

    chunks = _openai_chunks_for_event(
        event,
        model_id="gpt-4o",
        completion_id="chatcmpl-123",
        stream_tool_events=False,
    )

    assert len(chunks) == 1
    assert "reasoning_content" in chunks[0]
    assert "任务拆解完成" in chunks[0]


def test_final_answer_summarizer_passes_to_content_chunk():
    """用例 2：最终 Responder 节点的 content 输出才配通过，作为最终回答喷射"""
    event = SkillEvent(
        event_type="content",
        data={"text": "根据本周假勤数据大盘来看，全校请假主要集中在数学组。"},
        metadata={"langgraph_node": "answer_summarizer"},
    )

    chunks = _openai_chunks_for_event(
        event,
        model_id="gpt-4o",
        completion_id="chatcmpl-123",
        stream_tool_events=False,
    )

    assert len(chunks) == 1
    assert '"content":' in chunks[0]
    assert "reasoning_content" not in chunks[0]
    assert "数学组" in chunks[0]


def test_unowned_content_routes_to_reasoning_content_chunk():
    event = SkillEvent(
        event_type="content",
        data={"text": "正在检查 DDL 并准备执行 SQL"},
    )

    chunks = _openai_chunks_for_event(
        event,
        model_id="gpt-4o",
        completion_id="chatcmpl-123",
        stream_tool_events=False,
    )

    assert len(chunks) == 1
    assert "reasoning_content" in chunks[0]
    assert '"content":' not in chunks[0]


def test_pipeline_marked_final_answer_passes_to_content_chunk():
    event = SkillEvent(
        event_type="content",
        data={"text": "最终答案：今天全校请假共 8 人。"},
        metadata={"langgraph_node": "final_answer"},
    )

    chunks = _openai_chunks_for_event(
        event,
        model_id="gpt-4o",
        completion_id="chatcmpl-123",
        stream_tool_events=False,
    )

    assert len(chunks) == 1
    assert '"content":' in chunks[0]
    assert "reasoning_content" not in chunks[0]
    assert "最终答案" in chunks[0]


def test_final_answer_prompt_must_be_free_from_format_locks():
    system_prompt = _fast_final_answer_system_prompt()

    assert "必须使用Markdown表格" not in system_prompt
    assert "必须使用 Markdown 表格" not in system_prompt
    assert "结论先行" not in system_prompt
    assert "固定结构" not in system_prompt
    assert "输出结构固定" not in system_prompt

    assert "自由" in system_prompt
    assert "客观证据包" in system_prompt
