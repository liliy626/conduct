from __future__ import annotations

import asyncio
import gc
import json
import time
from typing import AsyncIterator

from langchain_core.messages import HumanMessage


SQL_HASH = "a1d11461f1ed8e9063bd83015b67272199859f51ced96d11e8df1ad961234567"


def _json_chunks(chunks: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for chunk in chunks:
        if not chunk.startswith("data: ") or chunk.strip() == "data: [DONE]":
            continue
        payloads.append(json.loads(chunk.removeprefix("data: ").strip()))
    return payloads


def test_image_generation_skill_contract_and_lineage_lock() -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    state = {
        "messages": [HumanMessage(content="把教师请假排行做成一张管理大屏图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 20,
                    "query_purpose": "统计教师请假排行",
                }
            ]
        },
    }

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx={"image_latency_sec": 0, "image_mock_mode": True},
            )
        ]

    events = asyncio.run(collect())
    assert "image_artifact" in ImageGenerationSkill().provided_outputs
    assert [event.event_type for event in events] == ["process", "process", "evidence_completed"]

    payload = events[-1].data["payload"]
    assert payload["linked_sql_hash"] == SQL_HASH
    assert payload["artifact_id"].startswith("img_")
    assert "![智能校园大屏分析插图]" in payload["markdown_render"]

    # 图片 URL 只能作为资产事件出现，不能写回对话历史，避免下一轮上下文污染。
    assert state["messages"] == [HumanMessage(content="把教师请假排行做成一张管理大屏图")]
    assert state["multimodal_artifacts"] == {}


def test_image_generation_skill_prefers_non_empty_sql_lineage() -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    non_empty_hash = "b" * 64
    empty_hash = "c" * 64
    state = {
        "messages": [HumanMessage(content="把真正查出来的请假数据画成图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": non_empty_hash,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 73,
                    "query_purpose": "教师请假排行",
                },
                {
                    "sql_hash": empty_hash,
                    "tables_used": ["zx_mlh.空配置表"],
                    "row_count": 0,
                    "query_purpose": "后置空探测",
                },
            ]
        },
    }

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx={"image_latency_sec": 0, "image_mock_mode": True},
            )
        ]

    events = asyncio.run(collect())
    payload = events[-1].data["payload"]

    assert payload["linked_sql_hash"] == non_empty_hash
    assert "73 real-time data records" in payload["prompt_used"]
    assert "zx_mlh.教师销假_请假明细" in payload["prompt_used"]


def test_image_artifact_event_streams_markdown_and_sources() -> None:
    from gateway_core.agents.universal_hub.models import SkillEvent
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter

    async def stream() -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "image_artifact",
                "payload": {
                    "artifact_id": "img_demo",
                    "markdown_render": "\n\n![智能校园大屏分析插图](https://cdn.example.test/img_demo.png)\n\n",
                    "linked_sql_hash": SQL_HASH,
                    "prompt_used": "school dashboard illustration",
                },
            },
        )

    async def collect() -> list[str]:
        return [
            chunk
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                stream(),
                model_id="yili-model",
                completion_id="chatcmpl-image-test",
            )
        ]

    chunks = asyncio.run(collect())
    payloads = _json_chunks(chunks)
    content = "".join(str(payload["choices"][0]["delta"].get("content", "")) for payload in payloads)

    assert "![智能校园大屏分析插图](https://cdn.example.test/img_demo.png)" in content
    source_payloads = [payload for payload in payloads if payload.get("sources")]
    assert len(source_payloads) == 1
    metadata = source_payloads[0]["sources"][0]["metadata"][0]
    assert metadata["type"] == "image_artifact"
    assert metadata["linked_sql_hash"] == SQL_HASH


def test_image_generation_skill_uses_openai_provider_and_artifact_store(tmp_path, monkeypatch) -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-1.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    calls = []

    class _OpenAIImageItem:
        b64_json = "cG5nLWJ5dGVz"
        url = ""

    class _OpenAIImageResponse:
        data = [_OpenAIImageItem()]

    def openai_call(**kwargs):
        calls.append(kwargs)
        return _OpenAIImageResponse()

    state = {
        "messages": [HumanMessage(content="把教师请假排行做成一张管理大屏图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 20,
                    "query_purpose": "统计教师请假排行",
                }
            ]
        },
    }

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx={"image_latency_sec": 0, "openai_image_call": openai_call},
            )
        ]

    events = asyncio.run(collect())
    assert calls, "OpenAI image provider should be called through the existing ImageTool path"
    assert calls[0]["model"] == "gpt-image-1.5"
    assert calls[0]["size"] == "1024x1024"
    assert "SQL Hash: a1d11461f1ed" in calls[0]["prompt"]

    payload = events[-1].data["payload"]
    assert payload["linked_sql_hash"] == SQL_HASH
    assert "/v1/artifacts/sch_zx_mlh/image/" in payload["cdn_url"]
    assert "![智能校园大屏分析插图](" in payload["markdown_render"]
    assert "/v1/artifacts/sch_zx_mlh/image/" in payload["markdown_render"]


def test_image_generation_skill_keeps_event_loop_free_during_openai_write(tmp_path, monkeypatch) -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-1.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _OpenAIImageItem:
        b64_json = "cG5nLWJ5dGVz"
        url = ""

    class _OpenAIImageResponse:
        data = [_OpenAIImageItem()]

    def slow_openai_call(**_kwargs):
        time.sleep(0.08)
        return _OpenAIImageResponse()

    state = {
        "messages": [HumanMessage(content="把教师请假排行做成一张管理大屏图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 20,
                    "query_purpose": "统计教师请假排行",
                }
            ]
        },
    }

    async def run_with_ticker() -> int:
        ticks = 0

        async def consume_skill() -> None:
            async for _event in ImageGenerationSkill().astream(
                state,
                ctx={"image_latency_sec": 0, "openai_image_call": slow_openai_call},
            ):
                pass

        async def ticker() -> None:
            nonlocal ticks
            deadline = time.perf_counter() + 0.06
            while time.perf_counter() < deadline:
                await asyncio.sleep(0.005)
                ticks += 1

        await asyncio.gather(consume_skill(), ticker())
        return ticks

    assert asyncio.run(run_with_ticker()) >= 3


def test_triple_axis_prompt_synthesizer_aligns_style_entity_and_data() -> None:
    from gateway_core.agents.visual.prompt_synthesizer import TripleAxisPromptSynthesizer

    prompt = TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="帮我分析下眼保健操最差的年级，要一张有警示感的管理插图。")],
        purpose="眼保健操违纪扣分分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=11,
    )

    assert "amber and deep orange alert style" in prompt
    assert "student behavior discipline" in prompt
    assert "11 real-time data records" in prompt
    assert "zx_mlh.行规检查_行规检查" in prompt


def test_image_generation_skill_passes_triple_axis_prompt_to_openai(tmp_path, monkeypatch) -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_IMAGE_PROVIDER", "openai")
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-1.5")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    calls = []

    class _OpenAIImageItem:
        b64_json = "cG5nLWJ5dGVz"
        url = ""

    class _OpenAIImageResponse:
        data = [_OpenAIImageItem()]

    def openai_call(**kwargs):
        calls.append(kwargs)
        return _OpenAIImageResponse()

    state = {
        "messages": [
            HumanMessage(content="眼保健操最差的是哪个年级？"),
            HumanMessage(content="帮我做一张有警示感、橙色风格的管理插图。"),
        ],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.行规检查_行规检查"],
                    "row_count": 11,
                    "query_purpose": "眼保健操违纪扣分分析",
                }
            ]
        },
    }

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx={"image_latency_sec": 0, "openai_image_call": openai_call},
            )
        ]

    events = asyncio.run(collect())
    assert events[-1].event_type == "evidence_completed"
    prompt = calls[0]["prompt"]
    assert "amber and deep orange alert style" in prompt
    assert "student behavior discipline" in prompt
    assert "11 real-time data records" in prompt
    assert "SQL Hash: a1d11461f1ed" in prompt


def test_generate_image_releases_tool_output_after_url(monkeypatch) -> None:
    import gateway_core.agents.visual.image_generation_skill as image_generation_skill

    released = {"value": False}

    class _LargeOutput:
        ok = True
        artifacts = [{"url": "https://cdn.example.test/image.png"}]
        error = ""
        payload = "x" * 1_000_000

        def __del__(self) -> None:
            released["value"] = True

    class _FakeImageTool:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self, *_args, **_kwargs) -> _LargeOutput:
            return _LargeOutput()

    monkeypatch.setattr(image_generation_skill, "ImageTool", _FakeImageTool)

    result = image_generation_skill._generate_image(
        prompt="prompt",
        sql_hash=SQL_HASH,
        state={"session_context": {"school_id": "sch_zx_mlh"}},
        ctx={},
    )
    gc.collect()

    assert result == {"url": "https://cdn.example.test/image.png"}
    assert released["value"] is True
