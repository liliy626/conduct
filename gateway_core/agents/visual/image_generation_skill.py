from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any

from gateway_core.agents.base_skill import BaseMultimodalAgentSkill, RuntimeContext
from gateway_core.agents.universal_hub.models import MultimodalOutputContract, SkillEvent
from gateway_core.agents.universal_hub.state import ImageArtifact, UniversalAgentState
from gateway_core.agents.visual.prompt_synthesizer import TripleAxisPromptSynthesizer
from gateway_core.tools.artifact_store import validate_external_artifact_url
from gateway_core.tools.image_tool import ImageTool
from gateway_core.tools.tool_core import AgentToolInput, ToolExecutionContext


class ImageGenerationSkill(BaseMultimodalAgentSkill):
    """Generate a visual artifact from an existing SQL lineage record.

    The skill deliberately does not query databases and does not append image
    URLs into ``messages``. It only converts already-audited SQL evidence into
    a late multimedia artifact event.
    """

    @property
    def name(self) -> str:
        return "image_generator"

    @property
    def provided_outputs(self) -> frozenset[str]:
        return frozenset({"image_artifact"})

    async def astream(
        self,
        state: UniversalAgentState,
        ctx: RuntimeContext | dict[str, Any],
    ) -> AsyncIterator[SkillEvent]:
        lineages = _executed_sql_lineages(state)
        if not lineages:
            yield SkillEvent(event_type="process", data={"text": "缺少已审计 SQL 证据，已跳过生图。"})
            return

        evidence = _primary_sql_lineage(lineages)
        sql_hash = str(evidence.get("sql_hash") or "")
        if len(sql_hash) != 64:
            yield SkillEvent(event_type="process", data={"text": "SQL 证据哈希不完整，已跳过生图。"})
            return

        tables = [str(item) for item in evidence.get("tables_used", []) if item]
        row_count = int(evidence.get("row_count") or 0)
        purpose = str(evidence.get("query_purpose") or "校园数据分析")

        yield SkillEvent(event_type="process", data={"text": f"正在绑定 SQL 证据 Hash: {sql_hash[:12]}...\n"})
        await asyncio.sleep(float(ctx.get("image_latency_sec", 0.05) or 0))

        prompt = TripleAxisPromptSynthesizer.synthesize(
            history_messages=list(state.get("messages") or []),
            purpose=purpose,
            tables=tables,
            row_count=row_count,
            data_rows=_lineage_data_rows(evidence),
        )
        prompt = f"{prompt}\n数据指纹：已绑定。"
        yield SkillEvent(event_type="process", data={"text": "正在生成校园大屏可视化插图...\n"})
        await asyncio.sleep(float(ctx.get("image_latency_sec", 0.05) or 0))

        try:
            image_result = await asyncio.wait_for(
                asyncio.to_thread(_generate_image, prompt=prompt, sql_hash=sql_hash, state=state, ctx=ctx),
                timeout=_image_timeout_sec(ctx),
            )
        except TimeoutError:
            message = "生图失败：图像生成超时，已释放后续多模态任务继续执行。"
            _record_multimodal_error(
                ctx,
                artifact_type="image_artifact",
                code="image_generation_timeout",
                message=message,
            )
            yield SkillEvent(event_type="process", data={"text": f"{message}\n"})
            return
        if image_result.get("error"):
            message = f"生图失败：{image_result['error']}"
            _record_multimodal_error(
                ctx,
                artifact_type="image_artifact",
                code=_image_error_code(str(image_result["error"])),
                message=str(image_result["error"]),
            )
            yield SkillEvent(event_type="process", data={"text": f"{message}\n"})
            return

        try:
            cdn_url = validate_external_artifact_url(str(image_result.get("url") or ""))
        except ValueError as exc:
            _record_multimodal_error(
                ctx,
                artifact_type="image_artifact",
                code="image_artifact_validation_error",
                message=str(exc),
            )
            yield SkillEvent(event_type="process", data={"text": f"生图失败：{exc}\n"})
            return
        if not cdn_url:
            message = "生图工具未返回可展示图片地址。"
            _record_multimodal_error(
                ctx,
                artifact_type="image_artifact",
                code="image_generation_missing_url",
                message=message,
            )
            yield SkillEvent(event_type="process", data={"text": f"{message}\n"})
            return
        image_md5_proof = hashlib.md5(cdn_url.encode("utf-8")).hexdigest()

        artifact = ImageArtifact(
            artifact_id=f"img_{sql_hash[:12]}",
            prompt_used=prompt,
            cdn_url=cdn_url,
            linked_sql_hash=sql_hash,
            status="completed",
        )

        yield self._event_from_multimodal_contract(
            MultimodalOutputContract(
                artifact_type="image_artifact",
                artifact_id=artifact.artifact_id,
                cdn_url=artifact.cdn_url,
                crypto_proof=image_md5_proof,
                meta_payload={
                    "markdown_render": f"\n\n![智能校园大屏分析插图]({artifact.cdn_url})\n\n",
                    "linked_sql_hash": artifact.linked_sql_hash,
                    "prompt_used": artifact.prompt_used,
                },
            )
        )


def _executed_sql_lineages(state: UniversalAgentState) -> list[dict[str, Any]]:
    meta_context = state.get("meta_context") or {}
    lineages = meta_context.get("executed_sql_lineage") or meta_context.get("sql_lineage") or []
    return [item for item in lineages if isinstance(item, dict)]


def _lineage_row_count(lineage: dict[str, Any]) -> int:
    try:
        return int(lineage.get("row_count") or 0)
    except (TypeError, ValueError):
        return 0


def _lineage_tables(lineage: dict[str, Any]) -> list[str]:
    return [str(item).strip() for item in lineage.get("tables_used", []) if str(item).strip()]


def _lineage_data_rows(lineage: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("row_sample", "top_items", "data_rows", "display_rows", "preview_rows"):
        rows = lineage.get(key)
        if isinstance(rows, list):
            return [dict(item) for item in rows[:5] if isinstance(item, dict)]
    summary = lineage.get("evidence_summary")
    if isinstance(summary, dict):
        for key in ("row_sample", "top_items"):
            rows = summary.get(key)
            if isinstance(rows, list):
                return [dict(item) for item in rows[:5] if isinstance(item, dict)]
    return []


def _primary_sql_lineage(lineages: list[dict[str, Any]]) -> dict[str, Any]:
    for lineage in reversed(lineages):
        if _lineage_row_count(lineage) > 0 and _lineage_tables(lineage):
            return lineage
    for lineage in reversed(lineages):
        if _lineage_row_count(lineage) > 0:
            return lineage
    return next(reversed(lineages))


def _record_multimodal_error(
    ctx: RuntimeContext | dict[str, Any],
    *,
    artifact_type: str,
    code: str,
    message: str,
) -> None:
    if not isinstance(ctx, dict):
        return
    errors = ctx.setdefault("multimodal_errors", [])
    if isinstance(errors, list):
        errors.append(
            {
                "artifact_type": artifact_type,
                "code": code,
                "message": message,
            }
        )


def _image_error_code(message: str) -> str:
    if "artifact URL" in message or "允许域名" in message:
        return "image_artifact_validation_error"
    return "image_generation_error"


def _generate_image(
    *,
    prompt: str,
    sql_hash: str,
    state: UniversalAgentState,
    ctx: RuntimeContext | dict[str, Any],
) -> dict[str, str]:
    factory = ctx.get("image_url_factory")
    if callable(factory):
        try:
            return {"url": validate_external_artifact_url(str(factory(sql_hash)))}
        except ValueError as exc:
            return {"error": str(exc)}

    if ctx.get("image_mock_mode", False):
        digest = hashlib.md5(sql_hash.encode("utf-8")).hexdigest()[:16]
        return {"url": f"https://cdn.yili-edu.com/artifacts/img_{digest}.png"}

    session_context = state.get("session_context") or {}
    tenant_id = str(session_context.get("school_id") or session_context.get("tenant_id") or "default")
    tool = ImageTool(enabled=True, openai_call=ctx.get("openai_image_call"))
    output = tool.run(
        AgentToolInput(arguments={"prompt": prompt, "size": str(ctx.get("image_size") or "1024x1024")}),
        ToolExecutionContext(tenant_id=tenant_id, request_id=f"img_{sql_hash[:12]}"),
    )
    try:
        if not output.ok:
            return {"error": output.error or "image generation failed"}
        for artifact in output.artifacts:
            if isinstance(artifact, dict):
                url = str(artifact.get("download_url") or artifact.get("image_url") or artifact.get("url") or "").strip()
                if url:
                    try:
                        return {"url": validate_external_artifact_url(url)}
                    except ValueError as exc:
                        return {"error": str(exc)}
        return {"error": "image generation returned no artifact"}
    finally:
        del output


def _image_timeout_sec(ctx: RuntimeContext | dict[str, Any]) -> float:
    raw = ctx.get("image_timeout_sec") or os.getenv("UNIVERSAL_HUB_IMAGE_TIMEOUT_SEC", "120")
    try:
        return max(0.001, min(float(raw or 120), 120.0))
    except (TypeError, ValueError):
        return 120.0
