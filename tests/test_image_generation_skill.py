from __future__ import annotations

import ast
import asyncio
import gc
import inspect
import json
import textwrap
import time
from typing import AsyncIterator

import pytest
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


def test_base_multimodal_skill_emits_ppt_proof_key() -> None:
    from gateway_core.agents.base_skill import BaseMultimodalAgentSkill
    from gateway_core.agents.universal_hub.models import MultimodalOutputContract

    ppt_hash = "s" * 64

    class _PptSkill(BaseMultimodalAgentSkill):
        @property
        def name(self) -> str:
            return "ppt_generator"

        @property
        def provided_outputs(self) -> frozenset[str]:
            return frozenset({"ppt_artifact"})

        async def _execute_multimodal_core(self, state, ctx) -> MultimodalOutputContract:
            return MultimodalOutputContract(
                artifact_type="ppt_artifact",
                artifact_id="ppt_report_001",
                cdn_url="https://cdn.example.test/report.pptx",
                crypto_proof=ppt_hash,
                meta_payload={"title": "校园假勤审计报告", "page_count": 12},
            )

    async def collect() -> list:
        return [event async for event in _PptSkill().astream({}, {})]

    event = asyncio.run(collect())[0]
    assert event.event_type == "evidence_completed"
    assert event.data["type"] == "ppt_artifact"
    assert event.data["payload"]["artifact_id"] == "ppt_report_001"
    assert event.data["payload"]["ppt_sha256"] == ppt_hash
    assert event.data["payload"]["title"] == "校园假勤审计报告"


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
            "latest_answer_context": "结论：7年级眼保健操纪律风险最高，共发现11条重点记录。",
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


def test_image_generation_skill_rejects_untrusted_artifact_url() -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    state = {
        "messages": [HumanMessage(content="把教师请假排行做成一张管理大屏图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "latest_answer_context": "结论：7年级眼保健操纪律风险最高，共发现11条重点记录。",
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

    ctx = {"image_latency_sec": 0, "image_url_factory": lambda _hash: "https://evil.example.test/img.png"}

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx=ctx,
            )
        ]

    events = asyncio.run(collect())

    assert [event.event_type for event in events] == ["process", "process", "process"]
    assert "不在允许域名" in events[-1].data["text"]
    assert ctx["multimodal_errors"] == [
        {
            "artifact_type": "image_artifact",
            "code": "image_artifact_validation_error",
            "message": events[-1].data["text"].strip().removeprefix("生图失败："),
        }
    ]


def test_artifact_url_validation_raises_explicit_error() -> None:
    from gateway_core.tools.artifact_store import ArtifactValidationError, validate_external_artifact_url

    with pytest.raises(ArtifactValidationError):
        validate_external_artifact_url("https://evil.example.test/img.png")


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
    assert "用户原始问题" in payload["prompt_used"]
    assert "最终回答结论" in payload["prompt_used"]


def test_image_generation_skill_prompt_uses_final_answer_context_not_row_sample() -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    state = {
        "messages": [HumanMessage(content="把眼保健操纪律最差的年级画成警示图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "latest_answer_context": "结论：7年级本周眼保健操纪律最差，总扣分5分，需要重点提醒。",
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.行规检查_行规检查"],
                    "row_count": 3,
                    "query_purpose": "眼保健操纪律年级排行",
                    "row_sample": [
                        {"年级": "6年级", "总扣分": 2.0, "违纪次数": 2},
                        {"年级": "7年级", "总扣分": 1.0, "违纪次数": 1},
                    ],
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

    payload = asyncio.run(collect())[-1].data["payload"]

    assert "把眼保健操纪律最差的年级画成警示图" in payload["prompt_used"]
    assert "结论：7年级本周眼保健操纪律最差，总扣分5分，需要重点提醒。" in payload["prompt_used"]
    assert "6年级" not in payload["prompt_used"]
    assert "总扣分：2" not in payload["prompt_used"]


def test_image_generation_skill_prompt_uses_answer_context_not_aggregate_top_items() -> None:
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    state = {
        "messages": [HumanMessage(content="本学期全校教师请假概况如何？生成一张纯中文管理图")],
        "session_context": {"school_id": "sch_zx_mlh", "schema_name": "zx_mlh"},
        "required_outputs": ["image_artifact"],
        "completed_outputs": ["data_evidence"],
        "artifact_refs": [],
        "multimodal_artifacts": {},
        "meta_context": {
            "latest_answer_context": "结论：教师请假排行已完成，最高请假次数为8次，总体呈现头部集中。",
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 20,
                    "query_purpose": "教师请假概况",
                    "evidence_summary": {
                        "top_items": [
                            {"教师姓名": "张老师", "请假次数": 8, "总请假时长_小时": 32.5},
                            {"教师姓名": "李老师", "请假次数": 6, "总请假时长_小时": 20.0},
                        ]
                    },
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

    payload = asyncio.run(collect())[-1].data["payload"]

    assert "本学期全校教师请假概况如何？生成一张纯中文管理图" in payload["prompt_used"]
    assert "结论：教师请假排行已完成，最高请假次数为8次，总体呈现头部集中。" in payload["prompt_used"]
    assert "张老师" not in payload["prompt_used"]
    assert "总请假时长_小时：32.5" not in payload["prompt_used"]


def test_sql_lineage_preserves_limited_row_sample_for_visual_workers() -> None:
    from gateway_core.agents.school_sql.sql_tools import _sql_evidence_lineage

    lineage = _sql_evidence_lineage(
        task_id="task_1",
        sql="select * from zx_mlh.行规检查_行规检查",
        tables_used=["zx_mlh.行规检查_行规检查"],
        row_count=2,
        query_purpose="眼保健操纪律年级排行",
        rows=[
            {"年级": "6年级", "总扣分": 2.0, "违纪次数": 2},
            {"年级": "7年级", "总扣分": 1.0, "违纪次数": 1},
        ],
        tenant_id="sch_zx_mlh",
        schema_name="zx_mlh",
        effective_limit=20,
        total_row_count=2,
    )

    assert lineage["row_sample"] == [
        {"年级": "6年级", "总扣分": 2.0, "违纪次数": 2},
        {"年级": "7年级", "总扣分": 1.0, "违纪次数": 1},
    ]


def test_image_generation_skill_has_no_legacy_prompt_or_blind_tail_lineage() -> None:
    import gateway_core.agents.visual.image_generation_skill as image_generation_skill

    source = inspect.getsource(image_generation_skill)

    assert "_compile_prompt" not in source
    assert "lineages[-1]" not in source


def test_school_sql_skill_strips_multimodal_request_for_dedicated_workers() -> None:
    from gateway_core.agents.school_sql.school_sql_skill import SchoolSqlSkill

    captured: dict = {}

    async def stream_fn(**kwargs):
        captured.update(kwargs)
        if False:
            yield {}

    state = {
        "messages": [HumanMessage(content="眼保健操纪律最差的年级是哪个？生成一张纯中文管理插图")],
        "session_context": {"stream_fn": stream_fn, "token": "key_a", "school_scope": "zx_mlh"},
        "required_outputs": ["data_evidence", "image_artifact"],
    }

    async def collect() -> list:
        return [event async for event in SchoolSqlSkill().astream(state, {})]

    asyncio.run(collect())

    assert "生成一张纯中文管理插图" not in captured["question"]
    assert "只查询支撑该多模态请求所需的真实数据证据" in captured["question"]
    assert {"generate_image_tool", "plot", "slide", "chart"} <= set(captured["disabled_tool_names"])


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


def test_adapter_renders_ppt_artifact_from_output_matrices() -> None:
    from gateway_core.agents.universal_hub.models import SkillEvent
    from gateway_core.api.openai_compat.adapter import UniversalHubStreamAdapter

    ppt_hash = "s" * 64

    async def stream() -> AsyncIterator[SkillEvent]:
        yield SkillEvent(
            event_type="evidence_completed",
            data={
                "type": "ppt_artifact",
                "payload": {
                    "artifact_id": "ppt_report_001",
                    "cdn_url": "https://cdn.example.test/report.pptx",
                    "ppt_sha256": ppt_hash,
                    "title": "校园假勤审计报告",
                    "page_count": 12,
                    "pages_preview": [
                        {"slide_title": "行政摘要", "slide_summary": "假勤趋势与行规风险总览"},
                        {"slide_title": "核心血缘", "slide_summary": "绑定已审计 SQL 与多模态资产"},
                    ],
                },
            },
        )

    async def collect() -> list[dict]:
        chunks = [
            chunk
            async for chunk in UniversalHubStreamAdapter.to_openai_sse(
                stream(),
                model_id="yili-model",
                completion_id="chatcmpl-ppt-test",
                include_done=False,
            )
        ]
        return _json_chunks(chunks)

    payloads = asyncio.run(collect())
    content = "".join(str(payload["choices"][0]["delta"].get("content", "")) for payload in payloads)
    sources = [source for payload in payloads for source in payload.get("sources", [])]

    assert "智能汇报 PPT 已生成" in content
    assert "校园假勤审计报告" in content
    assert "https://cdn.example.test/report.pptx" in content
    assert any(source["metadata"][0].get("ppt_sha256") == ppt_hash for source in sources)


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
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-2")
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
            "latest_answer_context": "结论：7年级眼保健操纪律风险最高，共发现11条重点记录。",
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
    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["size"] == "1024x1024"
    assert "数据指纹：已绑定" in calls[0]["prompt"]

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
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-2")
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


def test_image_generation_skill_timeout_does_not_block_graph(monkeypatch) -> None:
    import gateway_core.agents.visual.image_generation_skill as image_generation_skill
    from gateway_core.agents.visual.image_generation_skill import ImageGenerationSkill

    def slow_generate(**_kwargs):
        time.sleep(0.2)
        return {"url": "https://cdn.example.test/late.png"}

    monkeypatch.setattr(image_generation_skill, "_generate_image", slow_generate)

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

    ctx = {"image_latency_sec": 0, "image_timeout_sec": 0.01}

    async def collect() -> list:
        return [
            event
            async for event in ImageGenerationSkill().astream(
                state,
                ctx=ctx,
            )
        ]

    events = asyncio.run(collect())

    assert events[-1].event_type == "process"
    assert "生图失败" in events[-1].data["text"]
    assert "超时" in events[-1].data["text"]
    assert ctx["multimodal_errors"] == [
        {
            "artifact_type": "image_artifact",
            "code": "image_generation_timeout",
            "message": events[-1].data["text"].strip(),
        }
    ]


def test_image_generation_skill_default_timeout_matches_openai_image_window(monkeypatch) -> None:
    from gateway_core.agents.visual.image_generation_skill import _image_timeout_sec

    monkeypatch.delenv("UNIVERSAL_HUB_IMAGE_TIMEOUT_SEC", raising=False)

    assert _image_timeout_sec({}) == 120.0


def test_triple_axis_prompt_synthesizer_aligns_style_entity_and_data() -> None:
    from gateway_core.agents.visual.prompt_synthesizer import TripleAxisPromptSynthesizer
    from gateway_core.prompts.prompt_domains import IMAGE_STYLE_THEMES

    prompt = TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="帮我分析下眼保健操最差的年级，要一张有警示感的管理插图。")],
        purpose="眼保健操违纪扣分分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=11,
        answer_context="结论：7年级纪律风险最高，需要重点关注。",
    )

    assert IMAGE_STYLE_THEMES["warning"] in prompt
    assert "学生行为规范与日常检查看板" in prompt
    assert "用户原始问题" in prompt
    assert "最终回答结论：结论：7年级纪律风险最高，需要重点关注。" in prompt


def test_triple_axis_prompt_synthesizer_uses_simplified_chinese_goal_and_style_contract() -> None:
    from gateway_core.agents.visual.prompt_synthesizer import TripleAxisPromptSynthesizer

    prompt = TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="请把男女请假比例排行做成一张苹果飞书风格的大屏图")],
        purpose="男女请假比例排行",
        tables=["zx_mlh.教师销假_请假明细"],
        row_count=20,
        answer_context="结论：男女请假比例已完成统计，女教师占比较高。",
    )

    assert "绘图员工明确指令" in prompt
    assert "用户分析目标：比例与分布分析" in prompt
    assert "最终回答结论：结论：男女请假比例已完成统计，女教师占比较高。" in prompt
    assert "苹果极简设计语言" in prompt
    assert "飞书企业应用布局" in prompt
    assert "只能使用清晰、现代、规整的简体中文字体" in prompt
    assert "男女请假比例排行" in prompt
    assert not __import__("re").search(r"[A-Za-z]", prompt)


def test_visual_prompt_sanitizer_keeps_structured_chinese_prompt_sections() -> None:
    from gateway_core.tools.privacy import sanitize_visual_prompt

    prompt = "用户原始问题：眼保健操违纪扣分分析\n最终回答结论：7年级风险最高"

    sanitized = sanitize_visual_prompt(prompt)

    assert "多人名单" not in sanitized
    assert "眼保健操违纪扣分分析" in sanitized
    assert "最终回答结论" in sanitized


def test_prompt_synthesizer_uses_centralized_master_template(monkeypatch) -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    monkeypatch.setattr(
        prompt_synthesizer,
        "IMAGE_MASTER_TEMPLATE",
        "STYLE={style_theme}; ENTITY={entity_context}; QUESTION={user_goal_text}; ANSWER={answer_context}; CENTRALIZED",
    )

    prompt = prompt_synthesizer.TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="请做一张警示图")],
        purpose="眼保健操违纪扣分分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=11,
        answer_context="结论：7年级风险最高。",
    )

    assert "CENTRALIZED" in prompt
    assert prompt.startswith("STYLE=")


def test_prompt_synthesizer_routes_by_configured_matrices(monkeypatch) -> None:
    import gateway_core.agents.visual.prompt_synthesizer as prompt_synthesizer

    monkeypatch.setitem(prompt_synthesizer.IMAGE_STYLE_THEMES, "calm", "舒缓绿色校园修复风格")
    monkeypatch.setitem(prompt_synthesizer.IMAGE_ENTITY_CONTEXTS, "student_rest", "学生午休纪律看板")
    monkeypatch.setitem(prompt_synthesizer.STYLE_ROUTER_MATRIX, "calm", ("舒缓",))
    monkeypatch.setitem(prompt_synthesizer.ENTITY_ROUTER_MATRIX, "student_rest", ("午休纪律",))

    prompt = prompt_synthesizer.TripleAxisPromptSynthesizer.synthesize(
        history_messages=[HumanMessage(content="午休纪律做一张舒缓风格的图")],
        purpose="午休纪律分析",
        tables=["zx_mlh.行规检查_行规检查"],
        row_count=7,
        answer_context="结论：午休纪律整体平稳。",
    )

    assert "舒缓绿色校园修复风格" in prompt
    assert "学生午休纪律看板" in prompt
    assert "结论：午休纪律整体平稳。" in prompt


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
    monkeypatch.setenv("GATEWAY_IMAGE_OPENAI_MODEL", "gpt-image-2")
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
            "latest_answer_context": "结论：7年级眼保健操纪律风险最高，共发现11条重点记录。",
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
    prompt = events[-1].data["payload"]["prompt_used"]
    assert "警示型行政数据图" in prompt
    assert "学生行为规范与日常检查看板" in prompt
    assert "结论：7年级眼保健操纪律风险最高，共发现11条重点记录。" in prompt
    assert "真实记录数：11" not in prompt
    assert "数据指纹：已绑定" in calls[0]["prompt"]
    assert not __import__("re").search(r"[A-Za-z]", calls[0]["prompt"])


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


def test_local_artifact_download_url_is_openwebui_reachable(monkeypatch, tmp_path) -> None:
    from gateway_core.tools.artifact_store import artifact_download_url, safe_artifact_path

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE_URL", "http://your-server-domain:8008")

    path = safe_artifact_path(tenant_id="sch_zx_mlh", tool_name="image", suffix=".png")
    path.write_bytes(b"png")

    assert artifact_download_url(path).startswith("http://127.0.0.1:8008/v1/artifacts/")


def test_artifact_download_url_can_use_allowlisted_server_base(monkeypatch, tmp_path) -> None:
    from gateway_core.tools.artifact_store import artifact_download_url, safe_artifact_path, validate_external_artifact_url

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE_URL", "http://gateway.school.test:8008")
    monkeypatch.setenv("GATEWAY_ALLOWED_ARTIFACT_URL_HOSTS", "gateway.school.test")

    path = safe_artifact_path(tenant_id="sch_zx_mlh", tool_name="image", suffix=".png")
    path.write_bytes(b"png")
    url = artifact_download_url(path)

    assert url.startswith("http://gateway.school.test:8008/v1/artifacts/")
    assert validate_external_artifact_url(url) == url


def test_artifact_endpoint_supports_browser_head_probe(monkeypatch, tmp_path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gateway_core.tools.artifact_endpoints import router
    from gateway_core.tools.artifact_store import artifact_relative_path, safe_artifact_path

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    path = safe_artifact_path(tenant_id="sch_zx_mlh", tool_name="image", suffix=".png")
    path.write_bytes(b"png")

    app = FastAPI()
    app.include_router(router)
    url = f"/v1/artifacts/{artifact_relative_path(path)}"

    assert TestClient(app).head(url).status_code == 200
    assert TestClient(app).get(url).headers["content-type"] == "image/png"


def test_ppt_generation_skill_uses_multimodal_contract_and_registered_route() -> None:
    from gateway_core.agents.ppt.ppt_generation_skill import PptGenerationSkill
    from gateway_core.agents.universal_hub.registry import mandatory_candidate_skill_names

    async def collect() -> list:
        return [
            event
            async for event in PptGenerationSkill().astream(
                {"messages": [], "session_context": {"school_id": "sch_zx_mlh"}},
                ctx={"ppt_latency_sec": 0, "ppt_mock_url": "https://cdn.example.test/report.pptx"},
            )
        ]

    events = asyncio.run(collect())
    payload = events[-1].data["payload"]

    assert "ppt_artifact" in PptGenerationSkill().provided_outputs
    assert mandatory_candidate_skill_names(frozenset({"ppt_artifact"}), frozenset()) == ["ppt_generator"]
    assert [event.event_type for event in events] == ["process", "evidence_completed"]
    assert events[-1].data["type"] == "ppt_artifact"
    assert payload["ppt_sha256"] == __import__("hashlib").sha256(payload["cdn_url"].encode("utf-8")).hexdigest()
    assert payload["title"] == "2026校园假勤与行规数据深度审计报告"


def test_ppt_generation_skill_default_writes_local_ppt_artifact(tmp_path, monkeypatch) -> None:
    from gateway_core.agents.ppt.ppt_generation_skill import PptGenerationSkill

    monkeypatch.setenv("GATEWAY_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE_URL", "http://your-server-domain:8008")

    async def collect() -> list:
        return [
            event
            async for event in PptGenerationSkill().astream(
                {"messages": [], "session_context": {"school_id": "sch_zx_mlh"}},
                ctx={"ppt_latency_sec": 0},
            )
        ]

    events = asyncio.run(collect())
    payload = events[-1].data["payload"]

    assert payload["cdn_url"].startswith("http://127.0.0.1:8008/v1/artifacts/sch_zx_mlh/slide/")
    assert payload["cdn_url"].endswith(".pptx")
    assert list((tmp_path / "artifacts" / "sch_zx_mlh" / "slide").glob("*.pptx"))


def test_ppt_generation_skill_uses_injected_bailian_provider_without_naked_event() -> None:
    from gateway_core.agents.ppt.ppt_generation_skill import PptGenerationSkill

    calls = []

    async def bailian_call(payload: dict) -> dict:
        calls.append(payload)
        return {
            "download_url": "https://cdn.example.test/bailian/report.pptx",
            "ppt_title": "2026年美兰湖中学【教师请假排行】深层数据审计汇报文稿",
            "page_count": 9,
            "pages_preview": [{"slide_title": "行政摘要", "slide_summary": "教师请假排行总览"}],
        }

    state = {
        "messages": [HumanMessage(content="生成一份汇报 PPT")],
        "session_context": {"school_id": "sch_zx_mlh"},
        "meta_context": {
            "executed_sql_lineage": [
                {
                    "sql_hash": SQL_HASH,
                    "tables_used": ["zx_mlh.教师销假_请假明细"],
                    "row_count": 20,
                    "query_purpose": "教师请假排行",
                }
            ]
        },
    }

    async def collect() -> list:
        return [
            event
            async for event in PptGenerationSkill().astream(
                state,
                ctx={"ppt_latency_sec": 0, "bailian_ppt_call": bailian_call},
            )
        ]

    events = asyncio.run(collect())
    payload = events[-1].data["payload"]

    assert calls[0]["purpose"] == "教师请假排行"
    assert calls[0]["school_id"] == "sch_zx_mlh"
    assert payload["cdn_url"] == "https://cdn.example.test/bailian/report.pptx"
    assert payload["title"] == "2026年美兰湖中学【教师请假排行】深层数据审计汇报文稿"
    assert payload["render_engine"] == "阿里云百炼大模型演示文稿组件"
    assert payload["ppt_sha256"] == __import__("hashlib").sha256(payload["cdn_url"].encode("utf-8")).hexdigest()


def test_ppt_generation_skill_rejects_untrusted_bailian_url() -> None:
    from gateway_core.agents.ppt.ppt_generation_skill import PptGenerationSkill

    async def bailian_call(_payload: dict) -> dict:
        return {"download_url": "https://evil.example.test/report.pptx", "ppt_title": "恶意外链"}

    async def collect() -> list:
        return [
            event
            async for event in PptGenerationSkill().astream(
                {"messages": [], "session_context": {"school_id": "sch_zx_mlh"}},
                ctx={"ppt_latency_sec": 0, "bailian_ppt_call": bailian_call},
            )
        ]

    events = asyncio.run(collect())

    assert [event.event_type for event in events] == ["process", "process"]
    assert "不在允许域名" in events[-1].data["text"]


def test_ppt_generation_skill_has_no_naked_evidence_completed_dict() -> None:
    import gateway_core.agents.ppt.ppt_generation_skill as ppt_generation_skill

    source = inspect.getsource(ppt_generation_skill)

    assert 'event_type="evidence_completed"' not in source
    assert "MultimodalOutputContract" in source


def test_image_tool_style_prompt_forbids_missing_data_placeholders() -> None:
    from gateway_core.tools.image_tool import BI_IMAGE_STYLE_PROMPT

    assert "严禁在图中写“未提供数据”" in BI_IMAGE_STYLE_PROMPT
    assert "可以改成“未提供数据”" not in BI_IMAGE_STYLE_PROMPT
