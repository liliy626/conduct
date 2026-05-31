from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator


async def legacy_stream_from_trace_payload(payload: dict[str, Any]) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Convert a persisted school trace payload into coarse legacy stream events."""

    yielded_process = False
    final_text = ""
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        output = step.get("output") if isinstance(step.get("output"), dict) else {}
        if name.endswith(".tool.start") or name == "agent_native.langgraph.tool.start":
            tool_name = _tool_name(step)
            if tool_name:
                if not yielded_process:
                    yielded_process = True
                    yield ("reasoning", {"text": "trace replay tool activity"})
                yield ("tool_start", {"tool": tool_name})
            if tool_name == "final_answer_handoff":
                final_text = final_text or _handoff_text(output)
        elif name.endswith(".tool.end") or name == "agent_native.langgraph.tool.end":
            artifact = _tool_artifact(output)
            if artifact:
                yield ("artifact", artifact)
        elif name == "context.build":
            sources = _source_views(output)
            if sources:
                yield ("sources", {"sources": sources})

    if final_text:
        yield ("final_content", {"text": final_text})


def _tool_name(step: dict[str, Any]) -> str:
    output = step.get("output") if isinstance(step.get("output"), dict) else {}
    input_payload = step.get("input") if isinstance(step.get("input"), dict) else {}
    return str(output.get("tool_name") or input_payload.get("tool_name") or "").strip()


def _handoff_text(output: dict[str, Any]) -> str:
    preview = str(output.get("input_preview") or output.get("output_preview") or "")
    if not preview:
        return ""
    data = _loads_jsonish(preview)
    if isinstance(data, dict):
        handoff = data.get("handoff_json") or data.get("handoff")
        if isinstance(handoff, str):
            handoff_data = _loads_jsonish(handoff)
            if isinstance(handoff_data, dict):
                return _handoff_summary(handoff_data)
        if isinstance(handoff, dict):
            return _handoff_summary(handoff)
    match = re.search(r'"conclusion"\s*:\s*"([^"]+)"', preview)
    return match.group(1) if match else ""


def _handoff_summary(payload: dict[str, Any]) -> str:
    for key in ("conclusion", "summary", "answer"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    facts = payload.get("key_facts")
    if isinstance(facts, list):
        return "；".join(str(item) for item in facts[:3] if str(item).strip())
    return ""


def _loads_jsonish(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _source_views(output: dict[str, Any]) -> list[dict[str, str]]:
    summary = output.get("payload_summary")
    if not isinstance(summary, dict):
        return []
    views = summary.get("source_views")
    if not isinstance(views, list):
        return []
    return [{"title": str(view), "url": ""} for view in views if str(view).strip()]


def _tool_artifact(output: dict[str, Any]) -> dict[str, str]:
    preview = str(output.get("output_preview") or "")
    if not preview:
        return {}
    data = _loads_jsonish(preview)
    if not isinstance(data, dict) or data.get("ok") is not True:
        return {}
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list):
        return {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        url = str(
            artifact.get("image_url")
            or artifact.get("preview_url")
            or artifact.get("download_url")
            or artifact.get("url")
            or ""
        ).strip()
        if url:
            return {"type": str(artifact.get("type") or "artifact"), "url": url}
    return {}
