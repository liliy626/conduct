import asyncio
from types import SimpleNamespace

from gateway_core.agents.school_sql import agent_stream


class _FakeTools:
    source_views: list[str] = []
    evidence_by_task: dict[str, object] = {}
    ddl_contexts: list[str] = []
    flushed: bool = False

    def __init__(self, **_: object) -> None:
        self.flushed = False
        pass

    def sql_experience_search(self, _question: str) -> str:
        return ""

    def as_langchain_tools(self) -> list[object]:
        return []

    def flush_sql_experience_candidates(self, *, final_status: str = "answered") -> dict[str, object]:
        self.flushed = True
        return {"final_status": final_status, "candidate_count": 0, "recorded_count": 0}


class _FakeAgent:
    async def astream_events(self, *_: object, **__: object):
        yield {
            "event": "on_chat_model_stream",
            "run_id": "main-agent",
            "data": {"chunk": SimpleNamespace(content="主 Agent 已进入。")},
        }


class _DirectAnswerAgent:
    async def astream_events(self, *_: object, **__: object):
        yield {
            "event": "on_chat_model_stream",
            "run_id": "main-agent",
            "data": {"chunk": SimpleNamespace(content="好的，老师！5月已经查到：教师因公请假73次，学生病假因病182次。")},
        }
        yield {
            "event": "on_chat_model_end",
            "run_id": "main-agent",
            "data": {"output": {"messages": []}},
        }


class _ContextLimitAgent:
    async def astream_events(self, *_: object, **__: object):
        raise RuntimeError("maximum context length exceeded")
        yield {}


def test_school_agent_does_not_short_circuit_before_schema(monkeypatch) -> None:
    schema_called = False
    guard_called = False

    async def fake_guard_router(**_: object) -> bool:
        nonlocal guard_called
        guard_called = True
        return True

    async def fake_guard_chat(**_: object):
        yield "guard chat"

    def fake_schema_index(**_: object) -> SimpleNamespace:
        nonlocal schema_called
        schema_called = True
        return SimpleNamespace(
            school_id="sch_zx_mlh",
            school_name="美兰湖中学",
            source_schema="zx_mlh",
            datasets=[],
        )

    monkeypatch.setattr(
        agent_stream,
        "_require_school_api_key_record",
        lambda: SimpleNamespace(
            school_id="sch_zx_mlh",
            schema_name="zx_mlh",
            display_name="美兰湖中学",
            key_type="school",
        ),
    )
    monkeypatch.setattr(agent_stream, "_is_guard_router_chat", fake_guard_router)
    monkeypatch.setattr(agent_stream, "_stream_guard_router_chat", fake_guard_chat)
    monkeypatch.setattr(agent_stream, "_build_agent_schema_index", fake_schema_index)
    monkeypatch.setattr(agent_stream, "DDLReactTools", _FakeTools)
    monkeypatch.setattr(agent_stream, "_plan_tool_contract", lambda **_: SimpleNamespace())
    monkeypatch.setattr(
        agent_stream,
        "build_tool_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            prompt_text=lambda: "tool contract",
            trace_payload=lambda: {},
            handoff_block_payload=lambda: None,
        ),
    )
    monkeypatch.setattr(agent_stream, "build_langchain_agent_tools", lambda **_: [])
    monkeypatch.setattr(agent_stream, "create_react_agent", lambda *_args, **_kwargs: _FakeAgent())
    monkeypatch.setattr(agent_stream, "_should_buffer_tool_planning_content", lambda _model: False)
    monkeypatch.setattr(agent_stream, "record_langgraph_event_as_trace_step", lambda *_args, **_kwargs: None)

    async def collect_events() -> list[dict[str, object]]:
        return [
            event
            async for event in agent_stream.stream_school_sql_agent_native(
                question="本月工作重点主要集中在哪些板块？",
                token="school-key",
                school_scope=None,
                dsn="",
                psycopg_module=None,
                model=SimpleNamespace(model_name="test-model"),
                final_model=None,
                embedding_fn=None,
            )
        ]

    events = asyncio.run(collect_events())

    assert schema_called
    assert not guard_called
    assert events[0]["text"] == "主 Agent 已进入。"


def test_missing_final_handoff_preserves_agent_draft_without_empty_evidence_override(monkeypatch) -> None:
    monkeypatch.setenv("SCHOOL_AGENT_FINAL_MODEL_HANDOFF_ENABLED", "1")
    monkeypatch.setattr(
        agent_stream,
        "_require_school_api_key_record",
        lambda: SimpleNamespace(
            school_id="sch_zx_mlh",
            schema_name="zx_mlh",
            display_name="美兰湖中学",
            key_type="school",
        ),
    )
    monkeypatch.setattr(
        agent_stream,
        "_build_agent_schema_index",
        lambda **_: SimpleNamespace(
            school_id="sch_zx_mlh",
            school_name="美兰湖中学",
            source_schema="zx_mlh",
            datasets=[],
        ),
    )
    monkeypatch.setattr(agent_stream, "DDLReactTools", _FakeTools)
    monkeypatch.setattr(agent_stream, "_plan_tool_contract", lambda **_: SimpleNamespace())
    monkeypatch.setattr(
        agent_stream,
        "build_tool_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            prompt_text=lambda: "tool contract",
            trace_payload=lambda: {},
            handoff_block_payload=lambda: None,
        ),
    )
    monkeypatch.setattr(agent_stream, "build_langchain_agent_tools", lambda **_: [])
    monkeypatch.setattr(agent_stream, "create_react_agent", lambda *_args, **_kwargs: _DirectAnswerAgent())
    monkeypatch.setattr(agent_stream, "_should_buffer_tool_planning_content", lambda _model: False)
    monkeypatch.setattr(agent_stream, "record_langgraph_event_as_trace_step", lambda *_args, **_kwargs: None)

    async def fail_fast_final_answer(**_: object):
        raise AssertionError("empty evidence fallback should not call final fast model")
        yield ""

    monkeypatch.setattr(agent_stream, "_stream_fast_final_answer", fail_fast_final_answer)

    async def collect_events() -> list[dict[str, object]]:
        return [
            event
            async for event in agent_stream.stream_school_sql_agent_native(
                question="那五月呢",
                token="school-key",
                school_scope=None,
                dsn="",
                psycopg_module=None,
                model=SimpleNamespace(model_name="test-model"),
                final_model=SimpleNamespace(model_name="final-model"),
                embedding_fn=None,
            )
        ]

    events = asyncio.run(collect_events())
    content = "".join(str(event.get("text") or "") for event in events if event.get("type") == "content")

    assert "教师因公请假73次" in content
    assert "学生病假因病182次" in content


def test_agent_context_limit_error_asks_user_to_narrow_scope(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_stream,
        "_require_school_api_key_record",
        lambda: SimpleNamespace(
            school_id="sch_zx_mlh",
            schema_name="zx_mlh",
            display_name="美兰湖中学",
            key_type="school",
        ),
    )
    monkeypatch.setattr(
        agent_stream,
        "_build_agent_schema_index",
        lambda **_: SimpleNamespace(
            school_id="sch_zx_mlh",
            school_name="美兰湖中学",
            source_schema="zx_mlh",
            datasets=[],
        ),
    )
    monkeypatch.setattr(agent_stream, "DDLReactTools", _FakeTools)
    monkeypatch.setattr(agent_stream, "_plan_tool_contract", lambda **_: SimpleNamespace())
    monkeypatch.setattr(
        agent_stream,
        "build_tool_contract",
        lambda *_args, **_kwargs: SimpleNamespace(
            prompt_text=lambda: "tool contract",
            trace_payload=lambda: {},
            handoff_block_payload=lambda: None,
        ),
    )
    monkeypatch.setattr(agent_stream, "build_langchain_agent_tools", lambda **_: [])
    monkeypatch.setattr(agent_stream, "create_react_agent", lambda *_args, **_kwargs: _ContextLimitAgent())

    async def collect_events() -> list[dict[str, object]]:
        return [
            event
            async for event in agent_stream.stream_school_sql_agent_native(
                question="学校整体情况怎么样？",
                token="school-key",
                school_scope=None,
                dsn="",
                psycopg_module=None,
                model=SimpleNamespace(model_name="test-model"),
                final_model=None,
                embedding_fn=None,
            )
        ]

    events = asyncio.run(collect_events())
    content = "".join(str(event.get("text") or "") for event in events if event.get("type") == "content")

    assert "超过了模型上下文限制" in content
    assert "更具体的方向" in content
    assert "德育/行规/纪律" in content


def test_reporter_style_guide_requires_raw_data_presentation() -> None:
    guide = agent_stream._fast_final_answer_style_guide(
        question="本月工作重点主要集中在哪些板块？",
        handoff_payload={
            "data_evidence": {
                "德育与学生活动": [
                    "6月1日：周一升旗仪式",
                    "6月5日：八年级14岁集体生日",
                ]
            }
        },
        source_views=["每周安排_新_日程录入"],
    )

    assert "原始数据" in guide
    assert "代表性记录" in guide
    assert "表格" in guide


def test_agent_prompt_allows_new_evidence_directions() -> None:
    prompt = agent_stream._agent_native_prompt(
        SimpleNamespace(
            school_id="sch_zx_mlh",
            school_name="美兰湖中学",
            source_schema="zx_mlh",
            datasets=[],
        ),
        final_handoff_enabled=True,
        available_tool_names=["ddl_search", "inspect_table_schema", "sql_db_query", "final_answer_handoff"],
        tool_contract_prompt="tool contract",
    )

    assert "不要重复查询同一证据方向、同一候选表、同一 SQL" in prompt
    assert "允许开启新证据方向" in prompt
    assert "基础信息、任课/班级、获奖成果、请假考勤、学生评价、作业公示、导师活动" in prompt
    assert "德育、教学、后勤、安全、行政、人事" in prompt
