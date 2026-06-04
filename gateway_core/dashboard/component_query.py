from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from gateway_core.agents.school_sql.agent_stream import stream_school_sql_agent_native
from gateway_core.infra.db_pool import connect_db
from gateway_core.infra.postgres_dsn import postgres_dsn
from gateway_core.runtime import gateway_runtime as rt
from gateway_core.runtime.request_handler import prepare_chat_session_context


class DashboardComponentTimeRange(BaseModel):
    start: str
    end: str


class DashboardComponentQueryRequest(BaseModel):
    schema_name: str = Field(alias="schema")
    component_name: str
    purpose: str
    time_range: DashboardComponentTimeRange

    @model_validator(mode="before")
    @classmethod
    def normalize_dashboard_agent_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if all(key in value for key in ("schema", "component_name", "purpose", "time_range")):
            return value
        question = value.get("question")
        if not isinstance(question, dict):
            return value

        dashboard_need = str(question.get("dashboardNeed") or question.get("content") or "").strip()
        component_request = question.get("componentRequest")
        if not isinstance(component_request, dict):
            component_request = {}
        normalized = dict(value)
        normalized["schema"] = (
            question.get("targetSchema")
            or question.get("schema")
            or _nested_get(question, ("taskContext", "targetSchema"))
            or _nested_get(question, ("taskContext", "schema"))
            or _nested_get(question, ("organization", "dataSchema"))
            or _nested_get(question, ("orgStructure", "dataSchema"))
            or _nested_get(question, ("organization", "schema"))
            or _nested_get(question, ("orgStructure", "schema"))
        )
        normalized["component_name"] = (
            component_request.get("componentName")
            or component_request.get("component_name")
            or _extract_bracket_value(dashboard_need, "我要做组件")
            or "组件"
        )
        normalized["purpose"] = (
            component_request.get("purpose")
            or _extract_bracket_value(dashboard_need, "用途是")
            or dashboard_need
        )
        normalized["time_range"] = (
            _nested_get(question, ("taskContext", "timeRange"))
            or _nested_get(question, ("taskContext", "time_range"))
            or _extract_time_range(dashboard_need)
            or {
            "start": "",
            "end": "",
            }
        )
        return normalized


class DashboardComponentQueryResponse(BaseModel):
    status: str
    component_name: str
    sql_queries: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    row_count: int
    sample_rows: list[dict[str, Any]]
    limitations: list[str]


async def route_dashboard_component_query_async(
    request: DashboardComponentQueryRequest,
    *,
    authorization: str | None = None,
    x_school_scope: str | None = None,
) -> dict[str, Any]:
    return await run_dashboard_component_data_agent(
        request,
        authorization=authorization,
        x_school_scope=x_school_scope,
    )


async def run_dashboard_component_data_agent(
    request: DashboardComponentQueryRequest,
    *,
    authorization: str | None = None,
    x_school_scope: str | None = None,
    token: str | None = None,
    school_scope: str | None = None,
    model: Any | None = None,
    dsn: str | None = None,
    psycopg_module: Any | None = None,
    embedding_fn: Any | None = None,
    stream_fn: Any = stream_school_sql_agent_native,
    validate_sql: bool = True,
) -> dict[str, Any]:
    """Call the school SQL data agent and stop before final-answer LLM.

    This wrapper intentionally passes ``final_model=None``. The data agent may
    still use its tool-loop model to plan SQL and execute data tools, but it
    must not enter ``agent_native.final_fast.llm``.
    """
    clean_token = str(token or "").strip()
    clean_school_scope = school_scope
    if not clean_token:
        clean_token, session_context = prepare_chat_session_context(
            authorization,
            x_school_scope,
            None,
            None,
            None,
        )
        clean_school_scope = session_context.school_scope

    agent_model = model if model is not None else _default_component_agent_model()
    content_chunks: list[str] = []
    limitations: list[str] = []
    try:
        async for event in stream_fn(
            question=_component_agent_question(request),
            token=clean_token,
            school_scope=clean_school_scope,
            dsn=dsn if dsn is not None else postgres_dsn(),
            psycopg_module=psycopg_module if psycopg_module is not None else rt.psycopg,
            model=agent_model,
            final_model=None,
            embedding_fn=embedding_fn if embedding_fn is not None else rt._rag_embed_text,
            openwebui_chat_id="",
            conversation_context="",
            sql_logger=None,
            disabled_tool_names=("final_answer_handoff",),
        ):
            if str(event.get("type") or "") == "content":
                content_chunks.append(str(event.get("text") or ""))
    except Exception as exc:
        limitations.append(f"数据 Agent 查询失败：{type(exc).__name__}: {exc}")

    parsed = _extract_component_response("".join(content_chunks))
    if parsed:
        parsed["component_name"] = request.component_name
        if validate_sql:
            _keep_only_valid_sql_queries(
                parsed,
                dsn=dsn if dsn is not None else postgres_dsn(),
                psycopg_module=psycopg_module if psycopg_module is not None else rt.psycopg,
            )
        return DashboardComponentQueryResponse.model_validate(parsed).model_dump(mode="json")

    if not limitations:
        limitations.append("数据 Agent 未返回可解析的组件 JSON；请按组件继续追问 SQL、字段和样本数据。")
    return _empty_component_response(request, limitations=limitations)


def _default_component_agent_model() -> Any:
    model_id = os.getenv("DASHBOARD_COMPONENT_AGENT_MODEL", "").strip() or None
    spec = rt.GATEWAY.resolve_model(model_id)
    return rt.GATEWAY.get_client(spec, None, None)


def _component_agent_question(request: DashboardComponentQueryRequest) -> str:
    return (
        "你是大屏组件查数 Agent，只负责查数据、生成 SQL 和返回样本。"
        "不要生成自然语言总结，不要调用最终回答模型。\n"
        "请按下面固定 JSON 结构直接输出："
        '{"status":"ready","component_name":"","sql_queries":[],"fields":[],'
        '"row_count":0,"sample_rows":[],"limitations":[]}。\n'
        f"schema: {request.schema_name}\n"
        f"component_name: {request.component_name}\n"
        f"purpose: {request.purpose}\n"
        f"time_range.start: {request.time_range.start}\n"
        f"time_range.end: {request.time_range.end}\n"
        "要求：每个 sql_queries 项包含 task_id、sql、purpose、table_refs；"
        "fields 说明字段名和类型；sample_rows 返回前 10 行以内；"
        "limitations 说明口径、失败 SQL 或未覆盖项。"
    )


def _extract_component_response(text: str) -> dict[str, Any] | None:
    clean = str(text or "").strip()
    if not clean:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(clean):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(clean[index:])
        except Exception:
            continue
        if isinstance(payload, dict) and _looks_like_component_response(payload):
            return payload
    return None


def _looks_like_component_response(payload: dict[str, Any]) -> bool:
    return all(
        key in payload
        for key in (
            "status",
            "component_name",
            "sql_queries",
            "fields",
            "row_count",
            "sample_rows",
            "limitations",
        )
    )


def _empty_component_response(
    request: DashboardComponentQueryRequest,
    *,
    limitations: list[str],
) -> dict[str, Any]:
    return DashboardComponentQueryResponse(
        status="ready",
        component_name=request.component_name,
        sql_queries=[],
        fields=[],
        row_count=0,
        sample_rows=[],
        limitations=limitations,
    ).model_dump(mode="json")


def _keep_only_valid_sql_queries(
    payload: dict[str, Any],
    *,
    dsn: str,
    psycopg_module: Any,
) -> None:
    sql_queries = payload.get("sql_queries")
    if not isinstance(sql_queries, list):
        payload["sql_queries"] = []
        return

    limitations = payload.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
        payload["limitations"] = limitations

    valid_queries: list[dict[str, Any]] = []
    for index, item in enumerate(sql_queries):
        if not isinstance(item, dict):
            limitations.append(f"SQL 校验失败：第 {index + 1} 个 sql_queries 项不是对象，已忽略。")
            continue
        sql = str(item.get("sql") or item.get("canonical_sql") or "").strip()
        task_id = str(item.get("task_id") or item.get("query_key") or f"query_{index + 1}")
        if not sql:
            limitations.append(f"SQL 校验失败：{task_id} 缺少 sql 字段，已忽略。")
            continue
        safe_sql = _readonly_sql(sql)
        if not safe_sql:
            limitations.append(f"SQL 校验失败：{task_id} 不是只读 SELECT/WITH SQL，已忽略。")
            continue
        try:
            _probe_sql_execution(
                safe_sql,
                dsn=dsn,
                psycopg_module=psycopg_module,
            )
        except Exception as exc:
            limitations.append(f"SQL 校验失败：{task_id} 执行失败：{type(exc).__name__}: {exc}")
            continue

        clean_item = dict(item)
        clean_item["sql"] = safe_sql
        clean_item["validation_report"] = {"ok": True, "status": "passed"}
        valid_queries.append(clean_item)

    payload["sql_queries"] = valid_queries


def _readonly_sql(sql: str) -> str:
    clean = str(sql or "").strip()
    clean = re.sub(r";+\s*$", "", clean)
    if not clean:
        return ""
    if not re.match(r"(?is)^\s*(select|with)\b", clean):
        return ""
    forbidden = re.compile(
        r"(?is)\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|merge|call|copy|vacuum|refresh)\b"
    )
    if forbidden.search(clean):
        return ""
    return clean


def _probe_sql_execution(
    sql: str,
    *,
    dsn: str,
    psycopg_module: Any,
) -> None:
    if not str(dsn or "").strip():
        raise RuntimeError("postgres dsn is not configured")
    if psycopg_module is None:
        raise RuntimeError("psycopg is not available")
    wrapped_sql = f"select * from ({sql}) as dashboard_component_validation limit 1"
    with connect_db(psycopg_module, dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("set local statement_timeout = '3000ms'")
            cur.execute(wrapped_sql)


def _nested_get(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extract_bracket_value(text: str, label: str) -> str:
    pattern = re.escape(label) + r"\s*【([^】]+)】"
    match = re.search(pattern, text or "")
    return match.group(1).strip() if match else ""


def _extract_time_range(text: str) -> dict[str, str] | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:至|到|-|~|—|--)\s*(\d{4}-\d{2}-\d{2})", text or "")
    if not match:
        return None
    return {"start": match.group(1), "end": match.group(2)}
