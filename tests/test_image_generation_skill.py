from __future__ import annotations

import ast
import asyncio
import gc
import inspect
import json
import textwrap
import time
from typing import AsyncIterator

from langchain_core.messages import HumanMessage


SQL_HASH = "a1d11461f1ed8e9063bd83015b67272199859f51ced96d11e8df1ad961234567"
PDF_HASH = "p" * 64


def _json_chunks(chunks: list[str]) -> list[dict]:
    payloads: list[dict] = []
    for chunk in chunks:
        if not chunk.startswith("data: ") or chunk.strip() == "data: [DONE]":
            continue
        payloads.append(json.loads(chunk.removeprefix("data: ").strip()))
    return payloads


def test_base_multimodal_skill_emits_contract_event_and_proof_key() -> None:
    from gateway_core.agents.base_skill import BaseMultimodalAgentSkill
    from gateway_core.agents.universal_hub.models import MultimodalOutputContract

    class _PdfSkill(BaseMultimodalAgentSkill):
        @property
        def name(self) -> str:
            return "pdf_reader"

        @property
        def provided_outputs(self) -> frozenset[str]:
            return frozenset({"pdf_artifact"})

        async def _execute_multimodal_core(self, state, ctx) -> MultimodalOutputContract:
            return MultimodalOutputContract(
                artifact_type="pdf_artifact",
                artifact_id="pdf_policy_001",
                cdn_url="https://cdn.example.test/policy.pdf",
                crypto_proof=PDF_HASH,
                meta_payload={"file_name": "教师考勤管理办法.pdf"},
            )

    async def collect() -> list:
        return [event async for event in _PdfSkill().astream({}, {})]

    event = asyncio.run(collect())[0]
    assert event.event_type == "evidence_completed"
    assert event.data["type"] == "pdf_artifact"
    assert event.data["payload"]["artifact_id"] == "pdf_policy_001"
    assert event.data["payload"]["cdn_url"] == "https://cdn.example.test/policy.pdf"
    assert event.data["payload"]["status"] == "completed"
    assert event.data["payload"]["pdf_sha256"] == PDF_HASH
    assert event.data["payload"]["file_name"] == "教师考勤管理办法.pdf"


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


def test_image_generation_skill_has_no_legacy_prompt_or_blind_tail_lineage() -> None:
    import gateway_core.agents.visual.image_generation_skill as image_generation_skill

    source = inspect.getsource(image_generation_skill)

    assert "_compile_prompt" not in source
    assert "lineages[-1]" not in source


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


def test_image_artifact_event_uses_output_render_matrix(monkeypatch) -> None:
    from gateway_core.agents.universal_hub.models import SkillEvent
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter
    from gateway_core.prompts import prompt_domains

    monkeypatch.setitem(
        prompt_domains.OUTPUT_RENDER_MATRIX,
        "image_artifact",
        lambda payload: f"\n\nCUSTOM_RENDER::{payload['cdn_url']}\n\n",
    )

    async def stream() -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "image_artifact",
                "payload": {
                    "artifact_id": "img_demo",
                    "cdn_url": "https://cdn.example.test/img_demo.png",
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
                include_done=False,
            )
        ]

    payloads = _json_chunks(asyncio.run(collect()))
    content = "".join(str(payload["choices"][0]["delta"].get("content", "")) for payload in payloads)

    assert "CUSTOM_RENDER::https://cdn.example.test/img_demo.png" in content


def test_image_artifact_sources_use_output_source_matrix(monkeypatch) -> None:
    from gateway_core.agents.universal_hub.models import SkillEvent
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter
    from gateway_core.prompts import prompt_domains

    monkeypatch.setitem(
        prompt_domains.OUTPUT_SOURCE_MATRIX,
        "image_artifact",
        lambda payload: [
            {
                "source": {"name": f"CUSTOM_SOURCE::{payload['artifact_id']}", "url": ""},
                "document": ["custom document"],
                "metadata": [{"type": "custom_source", "image_md5_proof": payload["image_md5_proof"]}],
            }
        ],
    )

    async def stream() -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "image_artifact",
                "payload": {
                    "artifact_id": "img_demo",
                    "cdn_url": "https://cdn.example.test/img_demo.png",
                    "linked_sql_hash": SQL_HASH,
                    "image_md5_proof": "proof123",
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
                include_done=False,
            )
        ]

    source_payloads = [payload for payload in _json_chunks(asyncio.run(collect())) if payload.get("sources")]

    assert source_payloads[0]["sources"][0]["source"]["name"] == "CUSTOM_SOURCE::img_demo"
    assert source_payloads[0]["sources"][0]["metadata"][0]["image_md5_proof"] == "proof123"


def test_adapter_has_no_inline_multimodal_markdown_renderer() -> None:
    import gateway_core.api.openai_compat.adapter as adapter

    source = inspect.getsource(adapter)

    assert "![可视化画布]" not in source
    assert 'artifact_type == "image"' not in source
    assert "pdf_artifact" not in source
    assert "table_artifact" not in source


def test_adapter_renders_pdf_and_table_artifacts_from_output_matrices() -> None:
    from gateway_core.agents.universal_hub.models import SkillEvent
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter

    async def stream() -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "pdf_artifact",
                "payload": {
                    "artifact_id": "pdf_policy_001",
                    "file_name": "教师考勤管理办法.pdf",
                    "download_url": "https://cdn.example.test/policy.pdf",
                    "pdf_sha256": "p" * 64,
                    "extracted_sections": [
                        {"title": "请假纪律", "page": 7, "content_summary": "无故缺勤需按校纪处理。"}
                    ],
                },
            },
        )
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "table_artifact",
                "payload": {
                    "artifact_id": "tbl_leave_001",
                    "linked_table": "zx_mlh.教师销假_请假明细",
                    "csv_preview_url": "https://cdn.example.test/leave.csv",
                    "table_hash": "t" * 64,
                    "row_count": 20,
                    "headers": ["教师", "请假次数"],
                    "preview_rows": [["张三", 3], ["李四", 2]],
                },
            },
        )

    async def collect() -> list[dict[str, Any]]:
        chunks = [
            chunk
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                stream(),
                model_id="yili-model",
                completion_id="chatcmpl-multimodal-test",
                include_done=False,
            )
        ]
        return _json_chunks(chunks)

    payloads = asyncio.run(collect())
    content = "".join(str(payload["choices"][0]["delta"].get("content", "")) for payload in payloads)
    sources = [source for payload in payloads for source in payload.get("sources", [])]

    assert "文件审计高光" in content
    assert "教师考勤管理办法.pdf" in content
    assert "| 教师 | 请假次数 |" in content
    assert "| 张三 | 3 |" in content
    assert any(source["metadata"][0].get("pdf_sha256") == "p" * 64 for source in sources)
    assert any(source["metadata"][0].get("table_hash") == "t" * 64 for source in sources)


def test_stream_adapter_releases_event_payload_with_finally() -> None:
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter

    source = inspect.getsource(UniversalHubStreamAdapter.to_openai_sse)

    assert "finally:" in source
    assert "del event" in source


def test_stream_adapter_loop_delegates_openai_frame_building() -> None:
    from gateway_core.api.openai_compat import adapter
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter

    source = inspect.getsource(UniversalHubStreamAdapter.to_openai_sse)
    adapter_source = inspect.getsource(adapter)

    assert hasattr(adapter, "_to_openai_chunk")
    assert hasattr(adapter, "_to_openai_done_chunk")
    assert "for chunk in _openai_chunks_for_event" in source
    assert "delta={" not in source
    assert "delta={" not in adapter_source


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
    assert payload["image_md5_proof"] == __import__("hashlib").md5(payload["cdn_url"].encode("utf-8")).hexdigest()
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
    from gateway_core.prompts.prompt_domains import IMAGE_STYLE_THEMES

    prompt = TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="帮我分析下眼保健操最差的年级，要一张有警示感的管理插图。")],
        purpose="眼保健操违纪扣分分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=11,
    )

    assert IMAGE_STYLE_THEMES["warning"] in prompt
    assert "student behavior discipline" in prompt
    assert "11 real-time data records" in prompt
    assert "zx_mlh.行规检查_行规检查" in prompt


def test_prompt_synthesizer_uses_centralized_master_template(monkeypatch) -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    monkeypatch.setattr(
        prompt_synthesizer,
        "IMAGE_MASTER_TEMPLATE",
        "STYLE={style_theme}; ENTITY={entity_context}; DATA={data_signal}; CENTRALIZED",
    )

    prompt = prompt_synthesizer.TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="请做一张警示图")],
        purpose="眼保健操违纪扣分分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=11,
    )

    assert "CENTRALIZED" in prompt
    assert prompt.startswith("STYLE=")


def test_prompt_synthesizer_routes_by_configured_matrices(monkeypatch) -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    monkeypatch.setitem(prompt_synthesizer.IMAGE_STYLE_THEMES, "calm", "Calm green restorative campus style")
    monkeypatch.setitem(prompt_synthesizer.IMAGE_ENTITY_CONTEXTS, "student_rest", "student lunch break routine dashboard")
    monkeypatch.setitem(prompt_synthesizer.STYLE_ROUTER_MATRIX, "calm", ("舒缓",))
    monkeypatch.setitem(prompt_synthesizer.ENTITY_ROUTER_MATRIX, "student_rest", ("午休纪律",))

    prompt = prompt_synthesizer.TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="午休纪律做一张舒缓风格的图")],
        purpose="午休纪律分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=7,
    )

    assert "Calm green restorative campus style" in prompt
    assert "student lunch break routine dashboard" in prompt
    assert "7 real-time data records" in prompt


def test_prompt_synthesizer_router_functions_have_no_explicit_if_branches() -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    function_source = inspect.getsource(prompt_synthesizer.TripleAxisPromptSynthesizer.synthesize)
    tree = ast.parse(textwrap.dedent(function_source))
    assert not any(isinstance(node, ast.If) for node in ast.walk(tree))
    assert not any(
        isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension))
        for node in ast.walk(tree)
    )


def test_prompt_synthesizer_exposes_no_private_axis_helpers() -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    assert not any(
        hasattr(prompt_synthesizer, helper_name)
        for helper_name in ("_style_axis", "_entity_axis", "_route_key", "_data_axis")
    )


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
