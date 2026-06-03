from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from langchain_core.tools import StructuredTool

from gateway_core.tools import (
    ChartTool,
    GenerateImageTool,
    PolicyTool,
    PlotTool,
    SlideTool,
    TimeTool,
    WebSearchTool,
)
from gateway_core.tools.tool_core import AgentTool, AgentToolInput, ToolExecutionContext
from gateway_core.school.trace import set_step_output, trace_step


def build_langchain_agent_tools(
    *,
    school_id: str = "",
    tenant_id: str = "",
    trace: Any,
    policy_evidence_search_fn: Any = None,
    question: str = "",
    tool_contract: Any = None,
) -> list[StructuredTool]:
    """Expose optional non-SQL tools at the same LangGraph tools layer as sql_db_query."""
    clean_school_id = str(school_id or tenant_id or "").strip()
    tool_policy = _tool_policy_for_contract(tool_contract) if tool_contract is not None else _default_tool_policy()
    tool_call_counts: dict[str, int] = {}
    tools: list[AgentTool] = _filter_agent_tools(
        [
            TimeTool(),
            PolicyTool(provider=policy_evidence_search_fn),
            ChartTool(),
            PlotTool(),
            WebSearchTool(enabled=_truthy_env("GATEWAY_WEB_SEARCH_ENABLED", "0"), provider=_build_web_search_provider()),
            SlideTool(),
        ],
        allowed_names=tool_policy,
    )
    structured_tools = [
        _to_structured_tool(tool, school_id=clean_school_id, trace=trace, tool_call_counts=tool_call_counts)
        if tool_contract is None
        else _to_structured_tool(
            tool,
            school_id=clean_school_id,
            trace=trace,
            tool_call_counts=tool_call_counts,
            tool_contract=tool_contract,
        )
        for tool in tools
    ]
    if "generate_image_tool" in tool_policy:
        structured_tools.append(
            _generate_image_structured_tool(
                school_id=clean_school_id,
                trace=trace,
                tool_call_counts=tool_call_counts,
                tool_contract=tool_contract,
            )
        )
    return structured_tools


def _filter_agent_tools(tools: list[AgentTool], *, allowed_names: set[str]) -> list[AgentTool]:
    return [tool for tool in tools if tool.name in allowed_names]


def _default_tool_policy() -> set[str]:
    return {
        "time",
        "official_policy_search",
        "web_search",
        "plot",
        "chart",
        "generate_image_tool",
        "slide",
    }


def _tool_policy_for_contract(tool_contract: Any) -> set[str]:
    names = {"time"}
    allowed_tools = getattr(tool_contract, "allowed_tools", set()) or set()
    default_policy = _default_tool_policy()
    names.update(str(item or "").strip() for item in allowed_tools if str(item or "").strip() in default_policy)
    required_outputs = getattr(tool_contract, "required_outputs", set()) or set()
    output_to_tool = {
        "policy_evidence": "official_policy_search",
        "web_evidence": "web_search",
        "chart_artifact": "chart",
        "plot_artifact": "plot",
        "image_artifact": "generate_image_tool",
        "slide_artifact": "slide",
    }
    for output_name in required_outputs:
        tool_name = output_to_tool.get(str(output_name or "").strip())
        if tool_name:
            names.add(tool_name)
    if _dynamic_tool_pruning_disabled():
        names.update(default_policy)
    return names


def _dynamic_tool_pruning_disabled() -> bool:
    return _truthy_env("SCHOOL_AGENT_DYNAMIC_TOOL_PRUNING_DISABLED", "0") or _truthy_env(
        "TENANT_AGENT_DYNAMIC_TOOL_PRUNING_DISABLED",
        "0",
    )


def _generate_image_structured_tool(
    *,
    school_id: str,
    trace: Any,
    tool_call_counts: dict[str, int],
    tool_contract: Any = None,
) -> StructuredTool:
    def _run(
        prompt: str,
        image_url: str = "",
        image_urls: Any = None,
        image_path: str = "",
        image_paths: Any = None,
        images: Any = None,
        mask_url: str = "",
        mask_path: str = "",
        size: str = "",
    ) -> str:
        allowed, count, max_calls = _consume_tool_budget("generate_image_tool", tool_call_counts)
        if not allowed:
            return f"\n❌ 生成失败: tool call budget exceeded: generate_image_tool max {max_calls} per answer"
        if not _image_tool_enabled():
            return "\n❌ 生成失败: image tool disabled"
        arguments = {
            "prompt": str(prompt or ""),
            "image_url": image_url,
            "image_urls": image_urls,
            "image_path": image_path,
            "image_paths": image_paths,
            "images": images,
            "mask_url": mask_url,
            "mask_path": mask_path,
        }
        if size:
            arguments["size"] = size
        with trace_step(
            trace,
            "agent_native.tool.generate_image_tool",
            {"school_id": school_id, "arguments": arguments, "call_count": count, "max_calls": max_calls},
        ) as step:
            output = GenerateImageTool(enabled=True).run(
                AgentToolInput(arguments=arguments),
                ToolExecutionContext(tenant_id=school_id, metadata={"layer": "langgraph_agent_tools"}),
            )
            payload = output.to_dict()
            _contract_record(tool_contract, "generate_image_tool", payload)
            set_step_output(step, payload)
            return json.dumps(payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name="generate_image_tool",
        description=(
            "当用户要求“生成图片”、“画图”、“趋势图”、“分布图”、“视觉化展示”或“编辑/修改图片”时调用此工具。"
            "输入 prompt 应包含图表主题、真实数据、统计周期、指标、分类和需要标注的关键点。"
            "如果要修改已有图片，请同时传 image_url、image_path、images、image_urls 或 image_paths；可选 mask_url/mask_path 做局部编辑。"
            "工具会自动套用浅色高级 SaaS BI / Apple + Notion + Linear + 飞书 Analytics 风格，并返回图片 URL 地址。"
        ),
        func=_run,
    )


def _to_structured_tool(
    tool: AgentTool,
    *,
    school_id: str,
    trace: Any,
    tool_call_counts: dict[str, int],
    tool_contract: Any = None,
) -> StructuredTool:
    def _run(input: Any = "") -> str:
        arguments, evidence, sources = _coerce_agent_tool_payload(input)
        allowed, count, max_calls = _consume_tool_budget(tool.name, tool_call_counts)
        if not allowed:
            return json.dumps(
                {
                    "ok": False,
                    "error": f"tool call budget exceeded: {tool.name} max {max_calls} per answer",
                    "warnings": [
                        f"{tool.name} 已经调用 {count} 次；请停止重复调用该工具，基于已有结果或失败原因给出回答。"
                    ],
                    "evidence": [],
                    "artifacts": [],
                    "sources": [],
                    "duration_ms": 0,
                },
                ensure_ascii=False,
                default=str,
            )
        with trace_step(
            trace,
            f"agent_native.tool.{tool.name}",
            {"school_id": school_id, "arguments": arguments, "call_count": count, "max_calls": max_calls},
        ) as step:
            output = tool.run(
                AgentToolInput(arguments=arguments, evidence=evidence, sources=sources),
                ToolExecutionContext(tenant_id=school_id, metadata={"layer": "langgraph_agent_tools"}),
            )
            payload = output.to_dict()
            _contract_record(tool_contract, tool.name, payload)
            set_step_output(step, payload)
        return json.dumps(payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name=tool.name,
        description=tool.description,
        func=_run,
    )


def _contract_record(tool_contract: Any, tool_name: str, payload: dict[str, Any]) -> None:
    if tool_contract is None:
        return
    try:
        tool_contract.record_tool_result(tool_name, payload)
    except Exception:
        return

def _consume_tool_budget(tool_name: str, tool_call_counts: dict[str, int]) -> tuple[bool, int, int]:
    max_calls = _tool_max_calls(tool_name)
    current = int(tool_call_counts.get(tool_name, 0) or 0)
    if current >= max_calls:
        return False, current, max_calls
    current += 1
    tool_call_counts[tool_name] = current
    return True, current, max_calls


def _tool_max_calls(tool_name: str) -> int:
    specific = os.getenv(f"GATEWAY_TOOL_MAX_CALLS_{tool_name.upper()}", "").strip()
    if specific:
        return _positive_int(specific, 1)
    if tool_name in {"generate_image_tool", "slide"}:
        return _positive_int(os.getenv("GATEWAY_VISUAL_TOOL_MAX_CALLS", "1"), 1)
    return _positive_int(os.getenv("GATEWAY_TOOL_MAX_CALLS_DEFAULT", "8"), 8)


def _positive_int(raw: str, default: int) -> int:
    try:
        return max(1, int(str(raw or default).strip()))
    except Exception:
        return default


def _coerce_agent_tool_payload(value: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                payload = json.loads(text)
            except Exception:
                payload = {"input": text}
        else:
            payload = {"input": text, "query": text}
    if not isinstance(payload, dict):
        payload = {"input": str(value or "")}

    arguments = dict(payload.get("arguments") or payload.get("args") or payload)
    evidence = payload.get("evidence")
    sources = payload.get("sources")
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(sources, list):
        sources = []
    return arguments, [item for item in evidence if isinstance(item, dict)], [item for item in sources if isinstance(item, dict)]


def _build_web_search_provider():
    if not _truthy_env("GATEWAY_WEB_SEARCH_ENABLED", "0"):
        return None
    provider = str(os.getenv("GATEWAY_WEB_SEARCH_PROVIDER", "searxng") or "searxng").strip().lower()
    endpoint = os.getenv("GATEWAY_WEB_SEARCH_ENDPOINT", "").strip()
    if provider == "bocha":
        endpoint = endpoint or os.getenv("BOCHA_WEB_SEARCH_ENDPOINT", "").strip() or "https://api.bochaai.com/v1/web-search"
    if not endpoint:
        return None
    timeout = _float_env("GATEWAY_WEB_SEARCH_TIMEOUT_SEC", 5.0)

    def _provider(query: str, context: dict[str, Any]) -> Any:
        if provider == "bocha":
            body = json.dumps(
                {
                    "query": query,
                    "freshness": str(context.get("freshness") or os.getenv("BOCHA_FRESHNESS", "noLimit") or "noLimit"),
                    "summary": _truthy_env("BOCHA_SUMMARY", "1"),
                    "count": _positive_int(os.getenv("BOCHA_COUNT", os.getenv("GATEWAY_WEB_SEARCH_MAX_RESULTS", "8")), 8),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            api_key = os.getenv("BOCHA_API_KEY", "").strip() or os.getenv("GATEWAY_WEB_SEARCH_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("missing env var: BOCHA_API_KEY")
            req = urllib_request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
        elif provider == "json_post":
            body = json.dumps({"query": query, **context}, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            api_key = os.getenv("GATEWAY_WEB_SEARCH_API_KEY", "").strip()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
        else:
            sep = "&" if "?" in endpoint else "?"
            url = endpoint + sep + urllib_parse.urlencode({"q": query, "format": "json"})
            req = urllib_request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # nosec - endpoint is operator configured.
            text = resp.read().decode("utf-8")
        try:
            payload = json.loads(text)
        except Exception:
            return {"results": [{"title": "web_search_response", "content": text[:2000]}]}
        if provider == "bocha":
            return _normalize_bocha_search_payload(payload)
        return payload

    return _provider


def _normalize_bocha_search_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"results": []}
    web_pages = ((payload.get("data") or {}).get("webPages") or {}) if isinstance(payload.get("data"), dict) else {}
    raw_items = web_pages.get("value") if isinstance(web_pages, dict) else None
    if not isinstance(raw_items, list):
        raw_items = payload.get("results") if isinstance(payload.get("results"), list) else []
    results: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": item.get("name") or item.get("title") or "",
                "url": item.get("url") or "",
                "content": item.get("summary") or item.get("snippet") or item.get("content") or "",
                "source": item.get("siteName") or item.get("source") or "bocha",
                "published_at": item.get("datePublished") or item.get("published_at") or "",
            }
        )
    return {"results": results, "provider": "bocha"}


def _truthy_env(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _image_tool_enabled() -> bool:
    return (
        _truthy_env("GATEWAY_IMAGE_ENABLED", "0")
        or _truthy_env("GATEWAY_IMAGE_TOOL_ENABLED", "0")
        or bool(os.getenv(os.getenv("GATEWAY_IMAGE_QWEN_API_KEY_ENV", "DASHSCOPE_API_KEY"), "").strip())
    )


def _float_env(name: str, default: float) -> float:
    try:
        return max(1.0, min(float(os.getenv(name, str(default)) or default), 60.0))
    except Exception:
        return default
