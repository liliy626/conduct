from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import AsyncIterator
from typing import Any

from gateway_core.agents.base_skill import BaseMultimodalAgentSkill, RuntimeContext
from gateway_core.agents.universal_hub.models import MultimodalOutputContract, SkillEvent
from gateway_core.agents.universal_hub.state import UniversalAgentState
from gateway_core.tools.slide_tool import SlideTool
from gateway_core.tools.artifact_store import validate_external_artifact_url
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext


class PptGenerationSkill(BaseMultimodalAgentSkill):
    """Generate a proof-bearing PPT artifact from the current campus analysis state."""

    @property
    def name(self) -> str:
        return "ppt_generator"

    @property
    def provided_outputs(self) -> frozenset[str]:
        return frozenset({"ppt_artifact"})

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        yield SkillEvent(event_type="process", data={"text": "正在根据校园分析痕迹动态编排 PPT 幻灯片...\n"})
        try:
            async for event in super().astream(state, ctx):
                yield event
        except ValueError as exc:
            yield SkillEvent(event_type="process", data={"text": f"PPT 生成失败：{exc}\n"})

    async def _execute_multimodal_core(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> MultimodalOutputContract:
        await asyncio.sleep(float(ctx.get("ppt_latency_sec", 0.05) or 0))
        provider_result: dict[str, Any] | None = None
        try:
            provider_result = await _call_bailian_provider(state, ctx)
            ppt_title = _ppt_title(state, ctx, provider_result)
            pages_preview = _pages_preview(ctx, provider_result)
            cdn_url = _ppt_url(state, ctx, provider_result, title=ppt_title, pages_preview=pages_preview)
            ppt_sha256 = hashlib.sha256(cdn_url.encode("utf-8")).hexdigest()
            return MultimodalOutputContract(
                artifact_type="ppt_artifact",
                artifact_id=f"ppt_{ppt_sha256[:12]}",
                cdn_url=cdn_url,
                crypto_proof=ppt_sha256,
                meta_payload={
                    "ppt_title": ppt_title,
                    "title": ppt_title,
                    "page_count": _ppt_page_count(ctx, provider_result),
                    "pages_preview": pages_preview,
                    "render_engine": str(
                        provider_result.get("render_engine") or "阿里云百炼大模型演示文稿组件"
                    ),
                },
            )
        finally:
            del provider_result


async def _call_bailian_provider(
    state: UniversalAgentState,
    ctx: RuntimeContext | dict[str, Any],
) -> dict[str, Any]:
    call = ctx.get("bailian_ppt_call") or ctx.get("ppt_generation_call")
    if not callable(call):
        return {}
    result = call(_bailian_payload(state, ctx))
    return dict(await result if inspect.isawaitable(result) else result)


def _bailian_payload(state: UniversalAgentState, ctx: RuntimeContext | dict[str, Any]) -> dict[str, Any]:
    return {
        "model": str(ctx.get("bailian_ppt_model") or "bailian-ppt-v1"),
        "purpose": _lineage_purpose(state),
        "school_id": _school_id(state),
        "lineage": _sql_lineages(state)[-3:],
    }


def _ppt_url(
    state: UniversalAgentState,
    ctx: RuntimeContext | dict[str, Any],
    provider_result: dict[str, Any],
    *,
    title: str,
    pages_preview: list[dict[str, str]],
) -> str:
    factory = ctx.get("ppt_url_factory")
    if callable(factory):
        return validate_external_artifact_url(str(factory(state)))
    explicit_url = provider_result.get("download_url") or provider_result.get("cdn_url") or provider_result.get("url") or ctx.get("ppt_mock_url")
    if explicit_url:
        return validate_external_artifact_url(str(explicit_url))
    return _local_ppt_url(state, title=title, pages_preview=pages_preview)


def _ppt_title(
    state: UniversalAgentState,
    ctx: RuntimeContext | dict[str, Any],
    provider_result: dict[str, Any],
) -> str:
    if not provider_result:
        return str(ctx.get("ppt_title") or "2026校园假勤与行规数据深度审计报告")
    return str(
        provider_result.get("ppt_title")
        or provider_result.get("title")
        or ctx.get("ppt_title")
        or f"2026年美兰湖中学【{_lineage_purpose(state)}】深层数据审计汇报文稿"
    )


def _ppt_page_count(ctx: RuntimeContext | dict[str, Any], provider_result: dict[str, Any]) -> int:
    return int(provider_result.get("page_count") or ctx.get("ppt_page_count") or 12)


def _pages_preview(ctx: RuntimeContext | dict[str, Any], provider_result: dict[str, Any]) -> list[dict[str, str]]:
    pages_preview = provider_result.get("pages_preview") or ctx.get("ppt_pages_preview")
    if isinstance(pages_preview, list):
        return [page for page in pages_preview if isinstance(page, dict)]
    return [
        {"slide_title": "行政摘要", "slide_summary": "请假趋势与行规风险总览"},
        {"slide_title": "核心数据血缘", "slide_summary": "绑定已审计数据与多模态资产指纹"},
    ]


def _local_ppt_url(state: UniversalAgentState, *, title: str, pages_preview: list[dict[str, str]]) -> str:
    tenant_id = _school_id(state)
    output = SlideTool().run(
        AgentToolInput(
            arguments={
                "title": title,
                "sections": [
                    {
                        "title": str(page.get("slide_title") or "页面"),
                        "bullets": [str(page.get("slide_summary") or "需补充核实后生成汇报内容")],
                    }
                    for page in pages_preview
                ],
            }
        ),
        ToolExecutionContext(tenant_id=tenant_id, request_id=f"ppt_{tenant_id}"),
    )
    if not output.ok:
        raise ValueError(output.error or "PPT 本地落盘失败")
    for artifact in output.artifacts:
        if isinstance(artifact, dict) and artifact.get("type") == "pptx":
            return validate_external_artifact_url(str(artifact.get("download_url") or ""))
    raise ValueError("PPT 本地落盘未返回下载地址")


def _sql_lineages(state: UniversalAgentState) -> list[dict[str, Any]]:
    meta_context = state.get("meta_context") or {}
    lineages = meta_context.get("executed_sql_lineage") or meta_context.get("sql_lineage") or []
    return [item for item in lineages if isinstance(item, dict)]


def _lineage_purpose(state: UniversalAgentState) -> str:
    return next(
        (
            str(lineage.get("query_purpose"))
            for lineage in reversed(_sql_lineages(state))
            if str(lineage.get("query_purpose") or "").strip()
        ),
        "教务例行分析",
    )


def _school_id(state: UniversalAgentState) -> str:
    session_context = state.get("session_context") or {}
    return str(session_context.get("school_id") or session_context.get("tenant_id") or "default")
