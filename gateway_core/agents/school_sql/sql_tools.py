from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import threading
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from gateway_core.agents.school_sql.analysis_tools import analyze_trend, compare_cohort, detect_anomalies
from gateway_core.agents.school_sql.canonicalizer import normalize_sql_to_canonical
from gateway_core.schema_context.ddl_retriever import RetrievedDDLContext, retrieve_lean_ddl_context
from gateway_core.schema_context.query_experience import (
    EXPERIENCE_CACHE as _EXPERIENCE_CACHE,
    experience_cache_enabled as _experience_cache_enabled,
    experience_schema as _experience_schema,
    experience_table as _experience_table,
    experience_top_k_for_question as _experience_top_k_for_question,
    hash_payload as _hash_payload,
    record_experience_enabled as _record_experience_enabled,
    sanitize_experiences_for_question as _sanitize_experiences_for_question,
)
from gateway_core.agents.school_sql.evidence_board import EvidenceBoard
from gateway_core.school.schema_index import SchoolSchemaIndex
from gateway_core.schema_context.query_experience_store import extract_column_refs, search_query_experiences
from gateway_core.schema_context.sql_history_write_queue import enqueue_sql_history_write
from gateway_core.agents.school_sql.query_result_summarizer import (
    business_project_rows,
    display_rows_for_shape,
    infer_evidence_shape,
    rows_for_board,
    summarize_query_result,
)
from gateway_core.agents.school_sql.sql_guardrail import validate_raw_sql
from gateway_core.agents.school_sql.sql_utils import (
    coerce_tool_text as _coerce_tool_text,
    ddl_max_chars_per_doc as _ddl_max_chars_per_doc,
    ddl_top_k as _ddl_top_k,
    ddl_vector_table as _ddl_vector_table,
    env_value as _env_value,
    execute_query as _execute_query,
    format_rows as _format_rows,
    inspect_table_columns as _inspect_table_columns,
    is_json_or_array_sample_query as _is_json_or_array_sample_query,
    is_limited_non_aggregate_query as _is_limited_non_aggregate_query,
    json_sample_sql_hint as _json_sample_sql_hint,
    list_available_tables as _list_available_tables,
    load_table_ddl_summary as _load_table_ddl_summary,
    normalize_ref as _normalize_ref,
    quote_table_ref as _quote_table_ref,
    raw_rows_handle as _raw_rows_handle,
    raw_sql_max_rows as _raw_sql_max_rows,
    related_ddl_query as _related_ddl_query,
    requires_json_sample_before_aggregate as _requires_json_sample_before_aggregate,
    table_search_limit as _table_search_limit,
    school_table_ref as _school_table_ref,
    truncate_text as _truncate_text,
    quote_ident as _quote_ident,
)
from gateway_core.school.trace import set_step_output, trace_step


class DDLReactTools:
    """Tools for DDL-driven ReAct database analysis.

    This toolset intentionally does not expose the controlled JSON planner.
    SQL is model-written, but execution is bounded to the current school schema
    and to tables returned by ddl_search in this turn.
    """

    def __init__(
        self,
        *,
        question: str,
        school_id: str = "",
        tenant_id: str = "",
        package_index: SchoolSchemaIndex,
        dsn: str,
        psycopg_module: Any,
        embedding_fn: Callable[[str], list[float] | None] | None,
        trace: Any,
        sql_logger: Callable[..., None] | None = None,
    ) -> None:
        self.question = str(question or "").strip()
        self.tenant_id = str(school_id or tenant_id or "").strip()
        self.package_index = package_index
        self.dsn = dsn
        self.psycopg_module = psycopg_module
        self.embedding_fn = embedding_fn
        self.trace = trace
        self.sql_logger = sql_logger
        self.evidence_by_task: dict[str, Any] = {}
        self.source_views: list[str] = []
        self.selected_datasets_by_id: dict[str, dict[str, str]] = {}
        self.allowed_table_refs: list[str] = []
        self.ddl_contexts: list[dict[str, Any]] = []
        self.json_sampled_refs: set[str] = set()
        self._sql_query_counter = 0
        self._sql_query_counter_lock = threading.Lock()
        self.evidence_board = EvidenceBoard(
            question=self.question,
            school_id=self.tenant_id,
            school_name=self.package_index.tenant_name,
        )
        setattr(self.evidence_board, "evidence_by_task", self.evidence_by_task)

    def as_langchain_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                name="list_available_tables",
                description=(
                    "按关键词列出当前学校 schema 下可能相关的物理表/视图。"
                    "当 ddl_search 没找到合适业务表，或需要扩大搜索范围时调用。"
                    "输入是自然语言关键词，例如：教师请假、报修、德育扣分。"
                ),
                func=self.list_available_tables,
            ),
            StructuredTool.from_function(
                name="inspect_table_schema",
                description=(
                    "查看当前学校 schema 下某张表的字段、类型和 DDL 摘要，并把该表加入本轮 SQL 白名单。"
                    "输入表名，可以是 表名 或 schema.表名。"
                ),
                func=self.inspect_table_schema,
            ),
            StructuredTool.from_function(
                name="sample_table_rows",
                description=(
                    "抽样查看当前学校 schema 下某张表的真实数据行。"
                    "遇到 JSON/数组字段、字段含义不确定、聚合前需要确认数据结构时调用。"
                    "参数 table_name 是表名，limit 默认 5。"
                ),
                func=self.sample_table_rows,
            ),
            StructuredTool.from_function(
                name="inspect_jsonb_recordset",
                description=(
                    "探测当前学校 schema 下某张表的 JSONB/JSON 数组子表字段，返回内部 key、样例和 record_schema 建议。"
                    "当 DDL 中出现 JSONB 子表虚拟列，或用户要按子表里的星期、负责人、内容、项目、地点过滤时，先调用此工具。"
                    "输入 JSON：table_name、jsonb_column、limit。"
                ),
                func=self.inspect_jsonb_recordset,
            ),
            StructuredTool.from_function(
                name="jsonb_recordset_query",
                description=(
                    "把当前学校 schema 表里的 JSONB/JSON 数组字段用 jsonb_to_recordset 打平成虚拟子表后查询。"
                    "用于极速查询低代码主子表 JSONB 内部内容。"
                    "输入 JSON：table_name、jsonb_column、record_schema、select_main_fields、where、limit。"
                    "where 只能引用 m. 主表字段和 s. 子表字段；禁止子查询、多语句和写操作。"
                ),
                func=self.jsonb_recordset_query,
            ),
            StructuredTool.from_function(
                name="ddl_search",
                description=(
                    "按业务问题或线索从当前学校 schema 的 ddl_vector_documents 检索相关表结构。"
                    "输入是自然语言检索词。写 SQL 前必须先调用。"
                ),
                func=self.ddl_search,
            ),
            StructuredTool.from_function(
                name="sql_experience_search",
                description="检索当前学校历史相似 SQL 经验。输入是自然语言问题或子问题。",
                func=self.sql_experience_search,
            ),
            StructuredTool.from_function(
                name="sql_db_query",
                description=(
                    "执行一条 PostgreSQL SELECT。输入是 SQL 字符串。"
                    "只能查询 ddl_search 本轮返回过的当前学校 schema 表；禁止写操作、多语句、系统 schema 和裸表名。"
                ),
                func=self.sql_db_query,
            ),
            StructuredTool.from_function(
                name="suggest_related_queries",
                description=(
                    "基于 EvidenceBoard 已查到的数据线索，建议下一步业务补证方向。"
                    "输入可为空或当前想分析的线索。返回建议检索词，不直接查库。"
                ),
                func=self.suggest_related_queries,
            ),
            StructuredTool.from_function(
                name="trend_analysis",
                description=(
                    "对已查询到的 evidence rows 或输入 rows 做趋势分析。"
                    "适合月份、周、日期、学期等时间序列。可传 JSON：rows、time_field、metric_field。"
                ),
                func=self.trend_analysis,
            ),
            StructuredTool.from_function(
                name="anomaly_detection",
                description=(
                    "对已查询到的聚合结果或输入 rows 识别异常值和重点关注点。"
                    "可传 JSON：rows、metric_field、label_field。"
                ),
                func=self.anomaly_detection,
            ),
            StructuredTool.from_function(
                name="cohort_compare",
                description=(
                    "对同类群体结果做排名、分位数和目标对象对比。"
                    "可传 JSON：rows、target_name、name_field、metric_field。"
                ),
                func=self.cohort_compare,
            ),
        ]

    def list_available_tables(self, query: str = "") -> str:
        clean_query = _coerce_tool_text(query, keys=["query", "question", "input"])
        with trace_step(
            self.trace,
            "ddl_react.tool.list_available_tables",
            {"tenant_id": self.tenant_id, "schema_name": self.package_index.source_schema, "query": clean_query},
        ) as step:
            try:
                tables = _list_available_tables(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    schema_name=self.package_index.source_schema,
                    query=clean_query,
                    limit=_table_search_limit(),
                )
                payload = {
                    "source": "information_schema",
                    "schema_name": self.package_index.source_schema,
                    "query": clean_query,
                    "table_count": len(tables),
                    "tables": tables,
                }
            except Exception as exc:
                payload = {
                    "source": "information_schema",
                    "schema_name": self.package_index.source_schema,
                    "query": clean_query,
                    "table_count": 0,
                    "tables": [],
                    "error": str(exc),
                }
            set_step_output(step, payload)
        return json.dumps(payload, ensure_ascii=False, default=str)

    def inspect_table_schema(self, table_name: str) -> str:
        clean_table_name = _coerce_tool_text(table_name, keys=["table_name", "table", "name", "input"])
        table_ref = _school_table_ref(clean_table_name, schema_name=self.package_index.source_schema)
        with trace_step(
            self.trace,
            "ddl_react.tool.inspect_table_schema",
            {"tenant_id": self.tenant_id, "schema_name": self.package_index.source_schema, "table_name": clean_table_name},
        ) as step:
            if not table_ref:
                payload = {"source": "information_schema", "allowed": False, "error": "invalid_table_name"}
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            try:
                columns = _inspect_table_columns(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    schema_name=table_ref[0],
                    table_name=table_ref[1],
                )
                columns = _business_columns(columns)
                ddl_summary = _load_table_ddl_summary(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    schema_name=table_ref[0],
                    table_name=table_ref[1],
                    vector_table=_ddl_vector_table(),
                )
                if not columns:
                    payload = {
                        "source": "information_schema",
                        "allowed": False,
                        "error": "table_not_found_or_no_columns",
                        "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                    }
                else:
                    self._remember_table_ref(f"{table_ref[0]}.{table_ref[1]}")
                    payload = {
                        "source": "information_schema",
                        "allowed": True,
                        "schema_name": table_ref[0],
                        "table_name": table_ref[1],
                        "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                        "column_count": len(columns),
                        "columns": columns,
                        "ddl_summary": ddl_summary,
                    }
            except Exception as exc:
                payload = {
                    "source": "information_schema",
                    "allowed": False,
                    "error": str(exc),
                    "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                }
            set_step_output(
                step,
                {
                    **payload,
                    "ddl_summary": _truncate_text(str(payload.get("ddl_summary") or ""), 1200),
                },
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    def sample_table_rows(self, table_name: str, limit: int = 5) -> str:
        clean_table_name = _coerce_tool_text(table_name, keys=["table_name", "table", "name", "input"])
        table_ref = _school_table_ref(clean_table_name, schema_name=self.package_index.source_schema)
        clean_limit = max(1, min(int(limit or 5), 20))
        with trace_step(
            self.trace,
            "ddl_react.tool.sample_table_rows",
            {
                "tenant_id": self.tenant_id,
                "schema_name": self.package_index.source_schema,
                "table_name": clean_table_name,
                "limit": clean_limit,
            },
        ) as step:
            if not table_ref:
                payload = {"source": "school_schema", "allowed": False, "error": "invalid_table_name"}
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            self._remember_table_ref(f"{table_ref[0]}.{table_ref[1]}")
            sample_columns = _sample_select_columns(
                psycopg_module=self.psycopg_module,
                dsn=self.dsn,
                schema_name=table_ref[0],
                table_name=table_ref[1],
            )
            select_expr = ", ".join(_quote_ident(column) for column in sample_columns) if sample_columns else "*"
            sql = f"SELECT {select_expr} FROM {_quote_table_ref(table_ref[0], table_ref[1])} LIMIT {clean_limit}"
            guardrail = validate_raw_sql(
                self.package_index,
                sql,
                max_limit=clean_limit,
                extra_allowed_refs=self.allowed_table_refs,
                allowed_schema=self.package_index.source_schema,
            )
            if not guardrail.allowed:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": guardrail.reason,
                    "referenced_views": guardrail.referenced_views,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            try:
                rows = _execute_query(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    sql=guardrail.sql,
                    params=[],
                )
            except Exception as exc:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": str(exc),
                    "sql": guardrail.sql,
                    "referenced_views": guardrail.referenced_views,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            formatted_rows, field_labels = _format_rows(rows)
            formatted_rows = _compact_sample_rows(formatted_rows)
            self._mark_json_sampled(guardrail.referenced_views)
            payload = {
                "source": "school_schema",
                "allowed": True,
                "schema_name": table_ref[0],
                "table_name": table_ref[1],
                "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                "sql": guardrail.sql,
                "row_count": len(rows),
                "field_labels": field_labels,
                "rows": formatted_rows,
                "referenced_views": guardrail.referenced_views,
            }
            set_step_output(
                step,
                {
                    **payload,
                    "raw_rows": formatted_rows,
                },
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    def inspect_jsonb_recordset(self, input: str = "") -> str:
        payload_in = _json_tool_input(input)
        clean_table_name = str(payload_in.get("table_name") or payload_in.get("table") or "").strip()
        jsonb_column = str(payload_in.get("jsonb_column") or payload_in.get("column") or "").strip()
        clean_limit = max(1, min(_int_value(payload_in.get("limit"), 3), 10))
        table_ref = _school_table_ref(clean_table_name, schema_name=self.package_index.source_schema)
        with trace_step(
            self.trace,
            "ddl_react.tool.inspect_jsonb_recordset",
            {
                "tenant_id": self.tenant_id,
                "schema_name": self.package_index.source_schema,
                "table_name": clean_table_name,
                "jsonb_column": jsonb_column,
                "limit": clean_limit,
            },
        ) as step:
            base_error = self._jsonb_recordset_base_error(table_ref=table_ref, jsonb_column=jsonb_column)
            if base_error:
                set_step_output(step, base_error)
                return json.dumps(base_error, ensure_ascii=False, default=str)
            assert table_ref is not None
            sql = (
                f"SELECT {_quote_ident(jsonb_column)} AS jsonb_value "
                f"FROM {_quote_table_ref(table_ref[0], table_ref[1])} "
                f"WHERE {_quote_ident(jsonb_column)} IS NOT NULL "
                f"AND jsonb_typeof({_quote_ident(jsonb_column)}::jsonb) = 'array' "
                f"AND jsonb_array_length({_quote_ident(jsonb_column)}::jsonb) > 0 "
                f"LIMIT {clean_limit}"
            )
            try:
                rows = _execute_query(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    sql=sql,
                    params=[],
                )
                samples = _jsonb_array_samples(rows, column_name="jsonb_value")
                suggestion = _infer_jsonb_record_schema(samples)
                payload = {
                    "source": "school_schema",
                    "allowed": True,
                    "schema_name": table_ref[0],
                    "table_name": table_ref[1],
                    "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                    "jsonb_column": jsonb_column,
                    "row_count": len(rows),
                    "sample_count": len(samples),
                    "record_schema_suggestion": suggestion,
                    "sample_records": samples[:10],
                    "next_step_hint": (
                        "使用 jsonb_recordset_query，传入 table_name、jsonb_column、record_schema_suggestion，"
                        "并用 where 过滤 s. 子表字段，例如 s.\"星期\" = '星期三'。"
                    ),
                }
                self._mark_json_sampled([f"{table_ref[0]}.{table_ref[1]}"])
            except Exception as exc:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": str(exc),
                    "schema_name": table_ref[0],
                    "table_name": table_ref[1],
                    "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                    "jsonb_column": jsonb_column,
                    "sql": sql,
                }
            set_step_output(step, payload)
        return json.dumps(payload, ensure_ascii=False, default=str)

    def jsonb_recordset_query(self, input: str = "") -> str:
        payload_in = _json_tool_input(input)
        clean_table_name = str(payload_in.get("table_name") or payload_in.get("table") or "").strip()
        jsonb_column = str(payload_in.get("jsonb_column") or payload_in.get("column") or "").strip()
        record_schema = payload_in.get("record_schema") if isinstance(payload_in.get("record_schema"), dict) else {}
        select_main_fields = payload_in.get("select_main_fields")
        if not isinstance(select_main_fields, list):
            select_main_fields = []
        where = str(payload_in.get("where") or "").strip()
        clean_limit = max(1, min(_int_value(payload_in.get("limit"), _raw_sql_max_rows()), _raw_sql_max_rows()))
        table_ref = _school_table_ref(clean_table_name, schema_name=self.package_index.source_schema)
        task_id = self._next_sql_task_id()
        with trace_step(
            self.trace,
            "ddl_react.tool.jsonb_recordset_query",
            {
                "tenant_id": self.tenant_id,
                "task_id": task_id,
                "schema_name": self.package_index.source_schema,
                "table_name": clean_table_name,
                "jsonb_column": jsonb_column,
                "record_schema": record_schema,
                "where": where,
                "limit": clean_limit,
            },
        ) as step:
            base_error = self._jsonb_recordset_base_error(table_ref=table_ref, jsonb_column=jsonb_column)
            if base_error:
                set_step_output(step, base_error)
                return json.dumps(base_error, ensure_ascii=False, default=str)
            schema_error = _record_schema_error(record_schema)
            if schema_error:
                payload = {"source": "school_schema", "allowed": False, "error": schema_error}
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            where_error = _jsonb_recordset_where_error(where)
            if where_error:
                payload = {"source": "school_schema", "allowed": False, "error": where_error}
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            assert table_ref is not None
            main_fields = _safe_main_fields(select_main_fields)
            select_parts = [f"m.{_quote_ident(field)} AS {_quote_ident(field)}" for field in main_fields]
            select_parts.extend(f"s.{_quote_ident(field)} AS {_quote_ident(field)}" for field in record_schema)
            record_defs = ", ".join(f"{_quote_ident(field)} {_pg_record_type(str(pg_type))}" for field, pg_type in record_schema.items())
            sql = (
                "SELECT "
                + ", ".join(select_parts)
                + f" FROM {_quote_table_ref(table_ref[0], table_ref[1])} AS m "
                + f"CROSS JOIN LATERAL jsonb_to_recordset(m.{_quote_ident(jsonb_column)}::jsonb) AS s({record_defs})"
            )
            if where:
                sql += f" WHERE {where}"
            sql += f" LIMIT {clean_limit}"
            guardrail = validate_raw_sql(
                self.package_index,
                sql,
                max_limit=clean_limit,
                extra_allowed_refs=self.allowed_table_refs,
                allowed_schema=self.package_index.source_schema,
            )
            if not guardrail.allowed:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": guardrail.reason,
                    "referenced_views": guardrail.referenced_views,
                    "blocked_tokens": guardrail.blocked_tokens,
                    "sql": sql,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            try:
                rows = _execute_query(
                    psycopg_module=self.psycopg_module,
                    dsn=self.dsn,
                    sql=guardrail.sql,
                    params=[],
                )
            except Exception as exc:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": str(exc),
                    "sql": guardrail.sql,
                    "referenced_views": guardrail.referenced_views,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            formatted_rows, field_labels = _format_rows(rows)
            evidence_summary = summarize_query_result(
                intent="list",
                row_count=len(rows),
                formatted_rows=formatted_rows,
                field_labels=field_labels,
            )
            selected = self._selected_dataset_payloads(guardrail.referenced_views)
            payload = {
                "source": "school_schema",
                "sub_question": self.question,
                "purpose": "JSONB 子表字段经 jsonb_to_recordset 打平成虚拟子表后取得的证据。",
                "intent": "jsonb_recordset_select",
                "dataset_id": selected[0]["dataset_id"] if selected else "ddl_jsonb_recordset",
                "dataset_label": selected[0]["label"] if selected else table_ref[1],
                "allowed": True,
                "sql": guardrail.sql,
                "schema_name": table_ref[0],
                "table_name": table_ref[1],
                "table_ref": f"{table_ref[0]}.{table_ref[1]}",
                "jsonb_column": jsonb_column,
                "record_schema": record_schema,
                "row_count": len(rows),
                "field_labels": field_labels,
                "evidence_summary": evidence_summary,
                "row_sample": evidence_summary.get("row_sample", []),
                "referenced_views": guardrail.referenced_views,
                "limit_applied": guardrail.limit_applied,
                "raw_sql_handle": _raw_rows_handle(self.trace, task_id),
            }
            payload.update(display_rows_for_shape(evidence_shape="display", formatted_rows=formatted_rows))
            self.evidence_by_task[task_id] = payload
            for item in selected:
                self.selected_datasets_by_id[item["dataset_id"]] = item
                view = str(item.get("source_view") or "").strip()
                if view and view not in self.source_views:
                    self.source_views.append(view)
            board_snapshot = self._record_evidence_board(task_id, payload, rows=formatted_rows)
            payload["evidence_board"] = board_snapshot
            set_step_output(
                step,
                {
                    "task_id": task_id,
                    "allowed": True,
                    "sql": guardrail.sql,
                    "row_count": len(rows),
                    "columns": list(rows[0].keys()) if rows else [],
                    "raw_rows": formatted_rows,
                    "evidence_summary": evidence_summary,
                    "referenced_views": guardrail.referenced_views,
                    "evidence_board": board_snapshot,
                },
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    def ddl_search(self, query: str) -> str:
        clean_query = str(query or "").strip() or self.question
        with trace_step(
            self.trace,
            "ddl_react.tool.ddl_search",
            {
                "tenant_id": self.tenant_id,
                "schema_name": self.package_index.source_schema,
                "query": clean_query,
                "vector_table": _ddl_vector_table(),
            },
        ) as step:
            result = retrieve_lean_ddl_context(
                question=clean_query,
                schema_name=self.package_index.source_schema,
                dsn=self.dsn,
                psycopg_module=self.psycopg_module,
                embedding_fn=self.embedding_fn,
                vector_table=_ddl_vector_table(),
                top_k=_ddl_top_k(),
                max_chars_per_doc=_ddl_max_chars_per_doc(),
            )
            self._remember_ddl_context(result, query=clean_query)
            coverage_map = self._probe_candidate_evidence_map(result.table_refs, query=clean_query)
            payload = {
                "source": "ddl_vector_documents",
                "schema_name": result.schema_name,
                "query": clean_query,
                "doc_count": len(result.documents),
                "table_refs": result.table_refs,
                "candidate_evidence_map": coverage_map,
                "from_cache": result.from_cache,
                "error": result.error,
                "documents": [
                    {
                        "table_name": item.table_name,
                        "business_description": item.business_description,
                        "similarity": item.similarity,
                    }
                    for item in result.documents
                ],
                "next_step_hint": (
                    "先阅读 candidate_evidence_map：优先选择 current_period_count>0 或 latest_time 最新的活跃表；"
                    "对 likely_stale/empty 的旧表不要直接下结论为 0。"
                    "再从 table_refs 中选择相关表，调用 inspect_table_schema 获取精确字段；不要把无关表结构继续带入后续推理。"
                ),
            }
            if _ddl_search_returns_full_ddl():
                payload["ddl"] = result.ddl
            else:
                payload["ddl_preview"] = _ddl_search_preview(result.ddl)
                payload["ddl_omitted_chars"] = max(0, len(result.ddl) - len(payload["ddl_preview"]))
            set_step_output(
                step,
                {
                    "doc_count": payload["doc_count"],
                    "table_refs": payload["table_refs"],
                    "ddl_chars": len(result.ddl),
                    "from_cache": result.from_cache,
                    "error": result.error,
                    "documents": payload["documents"],
                    "candidate_evidence_map": coverage_map,
                },
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    def sql_experience_search(self, query: str) -> str:
        clean_query = str(query or "").strip() or self.question
        cache_key = _hash_payload(
            {
                "tenant_id": self.tenant_id,
                "query": clean_query,
                "schema": _experience_schema(self.package_index.source_schema),
                "table": _experience_table(),
                "limit": _experience_top_k_for_question(clean_query),
            }
        )
        with trace_step(
            self.trace,
            "ddl_react.tool.sql_experience_search",
            {"tenant_id": self.tenant_id, "query": clean_query},
        ) as step:
            hit = _EXPERIENCE_CACHE.get(cache_key) if _experience_cache_enabled() else None
            if hit is not None and isinstance(hit.value, list):
                experiences = hit.value
                from_cache = True
            else:
                experiences = search_query_experiences(
                    question=clean_query,
                    tenant_id=self.tenant_id,
                    dsn=self.dsn,
                    psycopg_module=self.psycopg_module,
                    embedding_fn=self.embedding_fn,
                    schema=_experience_schema(self.package_index.source_schema),
                    table=_experience_table(),
                    limit=_experience_top_k_for_question(clean_query),
                )
                if _experience_cache_enabled():
                    _EXPERIENCE_CACHE.set(cache_key, experiences)
                from_cache = False
            experiences = _sanitize_experiences_for_question(clean_query, experiences)
            payload = {
                "source": "sql_history_vector_documents",
                "query": clean_query,
                "experience_count": len(experiences),
                "experiences": experiences,
                "from_cache": from_cache,
            }
            set_step_output(step, payload)
        return json.dumps(payload, ensure_ascii=False, default=str)

    def sql_db_query(self, query: str) -> str:
        raw_sql = str(query or "").strip()
        canonical_sql = normalize_sql_to_canonical(raw_sql)
        task_id = self._next_sql_task_id()
        with trace_step(
            self.trace,
            "ddl_react.tool.sql_db_query",
            {
                "task_id": task_id,
                "raw_sql": raw_sql,
                "canonical_sql": canonical_sql,
                "allowed_table_refs": self.allowed_table_refs,
            },
        ) as step:
            if not self.allowed_table_refs:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                "error": "ddl_search_required_before_sql_db_query",
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            guardrail = validate_raw_sql(
                self.package_index,
                canonical_sql,
                max_limit=_raw_sql_max_rows(),
                extra_allowed_refs=self.allowed_table_refs,
                allowed_schema=self.package_index.source_schema,
            )
            if not guardrail.allowed:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": guardrail.reason,
                    "referenced_views": guardrail.referenced_views,
                    "blocked_tokens": guardrail.blocked_tokens,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            if _requires_json_sample_before_aggregate(guardrail.sql) and not self._has_json_sample_for_refs(
                guardrail.referenced_views
            ):
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "requires_sample": True,
                    "error": "json_or_array_sample_required_before_aggregation",
                    "message": (
                        "检测到 SQL 正在对 JSON/数组字段做聚合。请先执行一个样例查询，"
                        "例如 SELECT 原始 JSON/数组字段 FROM 同一张表 WHERE 条件 LIMIT 5，确认结构后再聚合。"
                    ),
                    "sql": guardrail.sql,
                    "referenced_views": guardrail.referenced_views,
                    "sample_sql_hint": _json_sample_sql_hint(guardrail.sql),
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            try:
                rows = _execute_query(psycopg_module=self.psycopg_module, dsn=self.dsn, sql=guardrail.sql, params=[])
            except Exception as exc:
                payload = {
                    "source": "school_schema",
                    "allowed": False,
                    "error": str(exc),
                    "sql": guardrail.sql,
                    "referenced_views": guardrail.referenced_views,
                    "limit_applied": guardrail.limit_applied,
                }
                set_step_output(step, payload)
                return json.dumps(payload, ensure_ascii=False, default=str)
            if self.sql_logger is not None:
                self.sql_logger(
                    school_id=self.tenant_id,
                    dataset_id="ddl_raw_sql",
                    intent="raw_sql_select",
                    sql=guardrail.sql,
                    params=[],
                )
            max_rows = _raw_sql_max_rows()
            evidence_shape = infer_evidence_shape(question=self.question, intent="list")
            effective_limit = _sql_limit(guardrail.sql) or max_rows
            query_may_have_more = _query_may_have_more(
                row_count=len(rows),
                effective_limit=effective_limit,
                limit_applied=guardrail.limit_applied,
            )
            total_row_count: int | None = None
            total_count_sql = ""
            total_count_error = ""
            expanded_to_full_rows = False
            if query_may_have_more:
                total_count_sql = _count_sql_for_limited_select(guardrail.sql)
                if total_count_sql:
                    try:
                        total_count_rows = _execute_query(
                            psycopg_module=self.psycopg_module,
                            dsn=self.dsn,
                            sql=total_count_sql,
                            params=[],
                        )
                        total_row_count = _first_int(total_count_rows)
                    except Exception as exc:
                        total_count_error = str(exc)
                if (
                    total_row_count is not None
                    and total_row_count > len(rows)
                    and total_row_count <= max_rows
                    and evidence_shape in {"display", "export"}
                ):
                    expanded_sql = _replace_trailing_limit(guardrail.sql, total_row_count)
                    if expanded_sql != guardrail.sql:
                        try:
                            expanded_rows = _execute_query(
                                psycopg_module=self.psycopg_module,
                                dsn=self.dsn,
                                sql=expanded_sql,
                                params=[],
                            )
                            if len(expanded_rows) >= len(rows):
                                rows = expanded_rows
                                effective_limit = _sql_limit(expanded_sql) or max_rows
                                expanded_to_full_rows = len(rows) >= total_row_count
                        except Exception:
                            expanded_to_full_rows = False
                query_may_have_more = _query_may_have_more(
                    row_count=len(rows),
                    effective_limit=effective_limit,
                    limit_applied=guardrail.limit_applied,
                    total_row_count=total_row_count,
                )
            formatted_rows, field_labels = _format_rows(rows)
            formatted_rows, field_labels = business_project_rows(formatted_rows, field_labels)
            entity_stats = _entity_distinct_stats(formatted_rows, question=self.question)
            if entity_stats:
                entity_stats["entity_count_scope"] = "returned_rows" if query_may_have_more else "full_result"
            evidence_summary = summarize_query_result(
                intent="list",
                row_count=len(rows),
                formatted_rows=formatted_rows,
                field_labels=field_labels,
            )
            if total_row_count is not None:
                evidence_summary["total_row_count"] = total_row_count
            if entity_stats:
                evidence_summary.update(entity_stats)
                entity_key = entity_stats.get("entity_key", "")
                distinct_count = entity_stats.get("distinct_entity_count")
                duplicate_count = entity_stats.get("duplicate_row_count")
                if duplicate_count:
                    evidence_summary["notable_findings"] = [
                        *evidence_summary.get("notable_findings", []),
                        (
                            f"本次结果存在一人/一实体多行：按 {entity_key} 去重后为 {distinct_count} 个，"
                            f"重复展开行数为 {duplicate_count}；回答人数/教师数/学生数时应优先使用去重数。"
                        ),
                    ]
            if query_may_have_more:
                evidence_summary["notable_findings"] = [
                    *evidence_summary.get("notable_findings", []),
                    (
                        f"本次结果达到 SQL LIMIT {effective_limit} 行，属于截断后的展示结果；"
                        "不能把该 row_count 当作全量总数。若要回答总数，必须另查 COUNT(*)；"
                        "若要完整名单，必须继续分页查询或按条件拆分。"
                    ),
                ]
            elif total_row_count is not None:
                evidence_summary["notable_findings"] = [
                    *evidence_summary.get("notable_findings", []),
                    f"已自动核验全量总数为 {total_row_count} 条；本次返回 {len(rows)} 条。",
                ]
            selected = self._selected_dataset_payloads(guardrail.referenced_views)
            sql_lineage = _sql_evidence_lineage(
                task_id=task_id,
                sql=guardrail.sql,
                tables_used=guardrail.referenced_views,
                row_count=len(rows),
                query_purpose=self.question,
                rows=formatted_rows,
                tenant_id=self.tenant_id,
                schema_name=self.package_index.source_schema,
                effective_limit=effective_limit,
                total_row_count=total_row_count,
            )
            if _is_json_or_array_sample_query(guardrail.sql) or _is_limited_non_aggregate_query(guardrail.sql):
                self._mark_json_sampled(guardrail.referenced_views)
            payload = {
                "source": "school_schema",
                "sub_question": self.question,
                "purpose": "DDL ReAct Agent 基于 DDL 检索和业务推理自主生成 SQL 后取得的证据。",
                "intent": "raw_sql_select",
                "dataset_id": selected[0]["dataset_id"] if selected else "ddl_raw_sql",
                "dataset_label": selected[0]["label"] if selected else "DDL Raw SQL",
                "row_count": len(rows),
                "evidence_shape": evidence_shape,
                "field_labels": field_labels,
                "evidence_summary": evidence_summary,
                "row_sample": evidence_summary.get("row_sample", []),
                "referenced_views": guardrail.referenced_views,
                "sql_lineage": sql_lineage,
                "limit_applied": guardrail.limit_applied,
                "effective_limit": effective_limit,
                "total_row_count": total_row_count,
                **entity_stats,
                "total_count_sql": total_count_sql,
                "total_count_error": total_count_error,
                "expanded_to_full_rows": expanded_to_full_rows,
                "query_may_have_more": query_may_have_more,
                "next_query_hint": (
                    f"本次结果达到 SQL LIMIT {effective_limit} 行，可能还有更多数据；"
                    "如果用户问“多少/总数/共有”，请另查 COUNT(*) 或 COUNT(DISTINCT ...)；"
                    f"如果用户需要完整明细，继续用相同条件加 OFFSET {effective_limit}，"
                    "或按时间、班级、类别、教师等条件拆分查询，直到最后一批返回行数低于上限。"
                    if query_may_have_more
                    else ""
                ),
                "raw_sql_handle": _raw_rows_handle(self.trace, task_id),
            }
            display_payload = display_rows_for_shape(evidence_shape=evidence_shape, formatted_rows=formatted_rows)
            if query_may_have_more:
                display_payload["display_rows_has_more"] = True
            if total_row_count is not None:
                display_payload["total_row_count"] = total_row_count
            display_payload.update(entity_stats)
            payload.update(display_payload)
            self.evidence_by_task[task_id] = payload
            for item in selected:
                self.selected_datasets_by_id[item["dataset_id"]] = item
                view = str(item.get("source_view") or "").strip()
                if view and view not in self.source_views:
                    self.source_views.append(view)
            board_snapshot = self._record_evidence_board(task_id, payload, rows=formatted_rows)
            payload["evidence_board"] = board_snapshot
            experience_recorded = self._record_sql_experience(
                task_id=task_id,
                sql=guardrail.sql,
                row_count=len(rows),
                selected=selected,
                referenced_views=guardrail.referenced_views,
            )
            payload["experience_recorded"] = experience_recorded
            set_step_output(
                step,
                {
                    "task_id": task_id,
                    "allowed": True,
                    "sql": guardrail.sql,
                    "row_count": len(rows),
                    "columns": list(rows[0].keys()) if rows else [],
                    "raw_rows": formatted_rows,
                    "evidence_summary": evidence_summary,
                    "referenced_views": guardrail.referenced_views,
                    "sql_lineage": sql_lineage,
                    "limit_applied": guardrail.limit_applied,
                    "effective_limit": effective_limit,
                    "total_row_count": total_row_count,
                    "total_count_sql": total_count_sql,
                    "total_count_error": total_count_error,
                    "expanded_to_full_rows": expanded_to_full_rows,
                    "query_may_have_more": query_may_have_more,
                    **entity_stats,
                    "evidence_board": board_snapshot,
                    "experience_recorded": experience_recorded,
                },
            )
        return json.dumps(payload, ensure_ascii=False, default=str)

    def suggest_related_queries(self, query: str = "") -> str:
        clean_query = str(query or "").strip()
        with trace_step(
            self.trace,
            "ddl_react.tool.suggest_related_queries",
            {"tenant_id": self.tenant_id, "query": clean_query, "task_count": len(self.evidence_by_task)},
        ) as step:
            board_payload = self.evidence_board_payload()
            clues = board_payload.get("business_clues", []) if isinstance(board_payload, dict) else []
            suggestions = []
            seen: set[str] = set()
            for clue in clues[:8]:
                if not isinstance(clue, dict):
                    continue
                value = str(clue.get("value") or "").strip()
                domains = [str(item or "") for item in clue.get("domains") or []]
                if not value:
                    continue
                search_query = _related_ddl_query(value=value, domains=domains)
                if search_query in seen:
                    continue
                seen.add(search_query)
                suggestions.append(
                    {
                        "source": "ddl_vector_documents",
                        "question": f"围绕“{value}”继续检索相关业务表并补查数据",
                        "ddl_query": search_query,
                        "reason": f"EvidenceBoard 发现业务线索“{value}”，可先 ddl_search 再 sql_db_query 补证。",
                    }
                )
            payload = {
                "source": "evidence_board",
                "query": clean_query,
                "evidence_board": board_payload,
                "suggestions": suggestions[:6],
                "safety": "ddl_search_then_guarded_sql_only",
            }
            set_step_output(step, {"suggestion_count": len(payload["suggestions"]), "clue_count": len(clues)})
        return json.dumps(payload, ensure_ascii=False, default=str)

    def trend_analysis(self, input: str = "") -> str:
        payload_in = self._analysis_input(input)
        rows = self._analysis_rows(payload_in)
        time_field = str(payload_in.get("time_field") or "").strip()
        metric_field = str(payload_in.get("metric_field") or "").strip()
        with trace_step(
            self.trace,
            "ddl_react.tool.trend_analysis",
            {
                "tenant_id": self.tenant_id,
                "row_count": len(rows),
                "time_field": time_field,
                "metric_field": metric_field,
            },
        ) as step:
            result = analyze_trend(rows, time_field=time_field, metric_field=metric_field)
            set_step_output(
                step,
                {
                    "point_count": result.get("point_count", 0),
                    "direction": result.get("direction", ""),
                    "summary": result.get("summary", ""),
                    "error": result.get("error", ""),
                    "time_field": result.get("time_field", time_field),
                    "metric_field": result.get("metric_field", metric_field),
                },
            )
        return json.dumps(result, ensure_ascii=False, default=str)

    def anomaly_detection(self, input: str = "") -> str:
        payload_in = self._analysis_input(input)
        rows = self._analysis_rows(payload_in)
        metric_field = str(payload_in.get("metric_field") or "").strip()
        label_field = str(payload_in.get("label_field") or "").strip()
        with trace_step(
            self.trace,
            "ddl_react.tool.anomaly_detection",
            {
                "tenant_id": self.tenant_id,
                "row_count": len(rows),
                "metric_field": metric_field,
                "label_field": label_field,
            },
        ) as step:
            result = detect_anomalies(rows, metric_field=metric_field, label_field=label_field)
            set_step_output(
                step,
                {
                    "anomaly_count": len(result.get("anomalies", [])) if isinstance(result.get("anomalies"), list) else 0,
                    "summary": result.get("summary", ""),
                    "error": result.get("error", ""),
                    "metric_field": result.get("metric_field", metric_field),
                    "label_field": result.get("label_field", label_field),
                },
            )
        return json.dumps(result, ensure_ascii=False, default=str)

    def cohort_compare(self, input: str = "") -> str:
        payload_in = self._analysis_input(input)
        rows = self._analysis_rows(payload_in)
        target_name = str(payload_in.get("target_name") or payload_in.get("name") or "").strip()
        metric_field = str(payload_in.get("metric_field") or "").strip()
        name_field = str(payload_in.get("name_field") or "").strip()
        with trace_step(
            self.trace,
            "ddl_react.tool.cohort_compare",
            {
                "tenant_id": self.tenant_id,
                "row_count": len(rows),
                "target_name": target_name,
                "name_field": name_field,
                "metric_field": metric_field,
            },
        ) as step:
            result = compare_cohort(
                rows,
                target_name=target_name,
                name_field=name_field,
                metric_field=metric_field,
            )
            set_step_output(
                step,
                {
                    "cohort_size": result.get("cohort_size", 0),
                    "target_rank": result.get("target", {}).get("rank") if isinstance(result.get("target"), dict) else None,
                    "summary": result["summary"],
                },
            )
        return json.dumps(result, ensure_ascii=False, default=str)

    def evidence_board_payload(self) -> dict[str, Any]:
        payload = self.evidence_board.to_payload(include_tasks=True)
        payload["task_ids"] = list(self.evidence_by_task)
        payload["task_count"] = len(self.evidence_by_task)
        payload["selected_datasets"] = list(self.selected_datasets_by_id.values())
        payload["source_views"] = list(self.source_views)
        payload["ddl_contexts"] = self.ddl_contexts
        return payload

    def _analysis_input(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        text = str(value or "").strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        return {"input": text}

    def _analysis_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("rows", "data", "items", "row_sample"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [dict(item) for item in rows if isinstance(item, dict)]
        gathered: list[dict[str, Any]] = []
        for task in self.evidence_by_task.values():
            if not isinstance(task, dict):
                continue
            for key in ("rows", "row_sample", "top_items", "table_rows", "items"):
                rows = task.get(key)
                if isinstance(rows, list):
                    gathered.extend(dict(item) for item in rows if isinstance(item, dict))
            summary = task.get("evidence_summary")
            if isinstance(summary, dict) and isinstance(summary.get("row_sample"), list):
                gathered.extend(dict(item) for item in summary["row_sample"] if isinstance(item, dict))
        return gathered

    def _remember_ddl_context(self, result: RetrievedDDLContext, *, query: str) -> None:
        for ref in result.table_refs:
            self._remember_table_ref(ref)
        self.ddl_contexts.append(
            {
                "query": query,
                "source": result.source,
                "schema_name": result.schema_name,
                "table_refs": result.table_refs,
                "doc_count": len(result.documents),
                "from_cache": result.from_cache,
                "error": result.error,
            }
        )

    def _remember_table_ref(self, ref: str) -> None:
        clean = str(ref or "").strip()
        if clean and clean not in self.allowed_table_refs:
            self.allowed_table_refs.append(clean)

    def _probe_candidate_evidence_map(self, refs: list[str], *, query: str) -> list[dict[str, Any]]:
        if not _coverage_probe_enabled():
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in refs[:_coverage_probe_max_tables()]:
            clean = str(ref or "").strip()
            if not clean or _normalize_ref(clean) in seen:
                continue
            seen.add(_normalize_ref(clean))
            out.append(self._probe_single_candidate_table(clean, query=query))
        return out

    def _probe_single_candidate_table(self, ref: str, *, query: str) -> dict[str, Any]:
        table_ref = _school_table_ref(ref, schema_name=self.package_index.source_schema)
        base_payload: dict[str, Any] = {"table_ref": ref, "allowed": bool(table_ref)}
        if not table_ref:
            return {**base_payload, "status": "invalid_table_ref"}
        schema_name, table_name = table_ref
        try:
            columns = _inspect_table_columns(
                psycopg_module=self.psycopg_module,
                dsn=self.dsn,
                schema_name=schema_name,
                table_name=table_name,
            )
            time_column = _pick_probe_time_column(columns)
            quoted_table = _quote_table_ref(schema_name, table_name)
            if time_column:
                period = _probe_period(query)
                quoted_time = _quote_ident(time_column)
                sql = (
                    f"SELECT COUNT(*)::bigint AS total_count, "
                    f"MAX({quoted_time}) AS latest_time, "
                    f"COUNT(*) FILTER (WHERE {quoted_time} >= {period['start_sql']} "
                    f"AND {quoted_time} < {period['end_sql']})::bigint AS current_period_count "
                    f"FROM {quoted_table}"
                )
                rows = _execute_query(psycopg_module=self.psycopg_module, dsn=self.dsn, sql=sql, params=[])
                row = rows[0] if rows else {}
                total_count = int(row.get("total_count") or 0)
                current_period_count = int(row.get("current_period_count") or 0)
                latest_time = row.get("latest_time")
                return {
                    **base_payload,
                    "status": _probe_status(
                        total_count=total_count,
                        current_period_count=current_period_count,
                        latest_time=latest_time,
                    ),
                    "total_count": total_count,
                    "time_field": time_column,
                    "latest_time": latest_time,
                    "current_period": period["label"],
                    "current_period_count": current_period_count,
                }
            sql = f"SELECT COUNT(*)::bigint AS total_count FROM {quoted_table}"
            rows = _execute_query(psycopg_module=self.psycopg_module, dsn=self.dsn, sql=sql, params=[])
            total_count = int((rows[0] if rows else {}).get("total_count") or 0)
            return {
                **base_payload,
                "status": "no_time_field" if total_count else "empty",
                "total_count": total_count,
                "time_field": "",
                "latest_time": None,
                "current_period": "",
                "current_period_count": None,
            }
        except Exception as exc:
            return {**base_payload, "status": "probe_failed", "error": _truncate_text(str(exc), 240)}

    def _record_evidence_board(self, task_id: str, task_payload: dict[str, Any], *, rows: list[dict[str, Any]]) -> dict[str, Any]:
        with trace_step(
            self.trace,
            "ddl_react.evidence_board.record",
            {"task_id": task_id, "tenant_id": self.tenant_id},
        ) as step:
            self.evidence_board.record_task_evidence(
                task_id=task_id,
                question=str(task_payload.get("sub_question") or self.question),
                source=str(task_payload.get("source") or "school_schema"),
                dataset_ids=[str(task_payload.get("dataset_id") or "ddl_raw_sql")],
                row_count=int(task_payload.get("row_count") or 0),
                rows=rows_for_board(task_payload.get("evidence_summary") if isinstance(task_payload.get("evidence_summary"), dict) else {}),
                metadata={"evidence_payload": task_payload},
                evidence_payload={**task_payload, "rows": rows[:20]},
            )
            board = self.evidence_board_payload()
            set_step_output(step, {"task_id": task_id, "evidence_board": board})
            return board

    def _selected_dataset_payloads(self, refs: list[str]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for ref in refs:
            clean = str(ref or "").strip()
            if not clean:
                continue
            source_schema = clean.rsplit(".", 1)[0] if "." in clean else self.package_index.source_schema
            source_view = clean.rsplit(".", 1)[-1]
            out.append(
                {
                    "dataset_id": f"ddl_table:{source_view}",
                    "label": source_view,
                    "source_schema": source_schema,
                    "source_view": source_view,
                }
            )
        return out

    def _record_sql_experience(
        self,
        *,
        task_id: str,
        sql: str,
        row_count: int,
        selected: list[dict[str, str]],
        referenced_views: list[str],
    ) -> bool:
        if not _record_experience_enabled():
            return False
        with trace_step(
            self.trace,
            "ddl_react.experience.record",
            {"tenant_id": self.tenant_id, "task_id": task_id, "selected_path": "ddl_react_raw_sql"},
        ) as step:
            mode = enqueue_sql_history_write(
                payload={
                    "tenant_id": self.tenant_id,
                    "schema": _experience_schema(self.package_index.source_schema),
                    "table": _experience_table(),
                    "question": self.question,
                    "sql": sql,
                    "row_count": int(row_count or 0),
                    "used_datasets": selected or [{"dataset_id": "ddl_raw_sql", "source_schema": self.package_index.source_schema, "source_view": ""}],
                    "table_refs": referenced_views,
                    "column_refs": extract_column_refs(sql),
                    "guardrail_version": "v1",
                },
                dsn=self.dsn,
                psycopg_module=self.psycopg_module,
                embedding_fn=self.embedding_fn,
            )
            recorded = mode not in {"disabled", "skipped"}
            if recorded:
                _EXPERIENCE_CACHE.clear()
            set_step_output(
                step,
                {
                    "recorded": recorded,
                    "write_mode": mode,
                    "row_count": row_count,
                    "used_datasets": selected,
                },
            )
            return recorded

    def _next_sql_task_id(self) -> str:
        with self._sql_query_counter_lock:
            self._sql_query_counter += 1
            return f"ddl_sql_query_{self._sql_query_counter}"

    def _has_json_sample_for_refs(self, refs: list[str]) -> bool:
        normalized = {_normalize_ref(item) for item in refs}
        return bool(normalized) and normalized.issubset(self.json_sampled_refs)

    def _mark_json_sampled(self, refs: list[str]) -> None:
        for ref in refs:
            normalized = _normalize_ref(ref)
            if normalized:
                self.json_sampled_refs.add(normalized)

    def _jsonb_recordset_base_error(self, *, table_ref: tuple[str, str] | None, jsonb_column: str) -> dict[str, Any]:
        if not table_ref:
            return {"source": "school_schema", "allowed": False, "error": "invalid_table_name"}
        ref = f"{table_ref[0]}.{table_ref[1]}"
        if _normalize_ref(ref) not in {_normalize_ref(item) for item in self.allowed_table_refs}:
            return {
                "source": "school_schema",
                "allowed": False,
                "error": "ddl_search_or_inspect_required_before_jsonb_recordset",
                "table_ref": ref,
            }
        if not _valid_column_name(jsonb_column):
            return {"source": "school_schema", "allowed": False, "error": "invalid_jsonb_column", "table_ref": ref}
        columns = _inspect_table_columns(
            psycopg_module=self.psycopg_module,
            dsn=self.dsn,
            schema_name=table_ref[0],
            table_name=table_ref[1],
        )
        matched = next((item for item in columns if str(item.get("column_name") or "") == jsonb_column), None)
        if not matched or not bool(matched.get("is_json_or_array")):
            return {
                "source": "school_schema",
                "allowed": False,
                "error": "jsonb_column_not_found_or_not_json",
                "table_ref": ref,
                "jsonb_column": jsonb_column,
            }
        return {}


def _ddl_search_returns_full_ddl() -> bool:
    mode = _env_value("SCHOOL_DDL_SEARCH_RESPONSE_MODE", "TENANT_DDL_SEARCH_RESPONSE_MODE", "compact").lower()
    return mode in {"full", "legacy", "raw"}


def _ddl_search_preview(ddl: str) -> str:
    max_chars = _ddl_search_preview_max_chars()
    return _truncate_text(str(ddl or ""), max_chars)


def _ddl_search_preview_max_chars() -> int:
    try:
        return max(
            400,
            min(
                int(_env_value("SCHOOL_DDL_SEARCH_PREVIEW_MAX_CHARS", "TENANT_DDL_SEARCH_PREVIEW_MAX_CHARS", "1800") or "1800"),
                8000,
            ),
        )
    except Exception:
        return 1800


def _coverage_probe_enabled() -> bool:
    return str(os.getenv("SCHOOL_EVIDENCE_COVERAGE_ENABLED", "1") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _coverage_probe_max_tables() -> int:
    try:
        return max(1, min(int(os.getenv("SCHOOL_EVIDENCE_COVERAGE_MAX_TABLES", "12") or "12"), 12))
    except Exception:
        return 12


def _pick_probe_time_column(columns: list[dict[str, Any]]) -> str:
    typed: list[dict[str, Any]] = []
    for column in columns:
        name = str(column.get("column_name") or "")
        data_type = str(column.get("data_type") or "").lower()
        udt_name = str(column.get("udt_name") or "").lower()
        if not name or _is_non_business_probe_column(name):
            continue
        if any(token in data_type for token in ["date", "time"]) or any(token in udt_name for token in ["date", "time"]):
            typed.append(column)
    for column in sorted(typed, key=lambda item: _probe_time_name_score(str(item.get("column_name") or "")), reverse=True):
        return str(column.get("column_name") or "")
    return ""


def _is_non_business_probe_column(name: str) -> bool:
    clean = str(name or "").strip().lower()
    if not clean:
        return True
    if clean in {"__raw_row_json", "__raw_value_json"}:
        return True
    if clean.startswith("__") and clean not in {"__instance_time"}:
        return True
    return any(token in clean for token in ["raw", "原始", "审批记录", "附件"])


def _probe_time_name_score(name: str) -> int:
    clean = str(name or "").strip().lower()
    scores = [
        ("event_date", 120),
        ("业务日期", 115),
        ("检查日期", 112),
        ("请假日期", 112),
        ("销假日期", 112),
        ("提交时间", 110),
        ("审批完成时间", 108),
        ("开始时间", 106),
        ("结束时间", 104),
        ("reported_time", 102),
        ("created_at", 100),
        ("updated_at", 98),
        ("__instance_time", 96),
        ("日期", 90),
        ("时间", 80),
        ("date", 70),
        ("time", 60),
    ]
    for token, score in scores:
        if token.lower() in clean:
            return score
    return 0


def _probe_period(query: str) -> dict[str, str]:
    text = str(query or "")
    if "上周" in text:
        return {
            "label": "last_week",
            "start_sql": "date_trunc('week', CURRENT_DATE) - interval '1 week'",
            "end_sql": "date_trunc('week', CURRENT_DATE)",
        }
    if "本周" in text or "这周" in text:
        return {
            "label": "current_week",
            "start_sql": "date_trunc('week', CURRENT_DATE)",
            "end_sql": "date_trunc('week', CURRENT_DATE) + interval '1 week'",
        }
    if "上月" in text or "上个月" in text:
        return {
            "label": "last_month",
            "start_sql": "date_trunc('month', CURRENT_DATE) - interval '1 month'",
            "end_sql": "date_trunc('month', CURRENT_DATE)",
        }
    return {
        "label": "current_month",
        "start_sql": "date_trunc('month', CURRENT_DATE)",
        "end_sql": "date_trunc('month', CURRENT_DATE) + interval '1 month'",
    }


def _probe_status(*, total_count: int, current_period_count: int, latest_time: Any) -> str:
    if total_count <= 0:
        return "empty"
    if current_period_count > 0:
        return "active_current_period"
    if latest_time is None:
        return "no_latest_time"
    latest_text = str(latest_time)
    current_month = _dt.date.today().strftime("%Y-%m")
    if latest_text.startswith(current_month):
        return "recent_no_period_match"
    return "likely_stale_or_no_current_period"


def _sample_select_columns(
    *,
    psycopg_module: Any,
    dsn: str,
    schema_name: str,
    table_name: str,
) -> list[str]:
    try:
        columns = _inspect_table_columns(
            psycopg_module=psycopg_module,
            dsn=dsn,
            schema_name=schema_name,
            table_name=table_name,
        )
    except Exception:
        return []
    out: list[str] = []
    for column in _business_columns(columns):
        name = str(column.get("column_name") or "").strip()
        if not name:
            continue
        out.append(name)
        if len(out) >= _sample_column_limit():
            break
    return out


def _business_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(column)
        for column in columns
        if not _is_heavy_sample_column(str(column.get("column_name") or ""))
    ]


def _is_heavy_sample_column(name: str) -> bool:
    clean = str(name or "").strip()
    lower = clean.lower()
    if lower in {"__raw_row_json", "__raw_value_json"}:
        return True
    if lower.startswith("__") and lower not in {
        "__instance_time",
        "__created_time",
        "__modified_time",
        "__title",
        "__status",
    }:
        return True
    return any(token in clean for token in ["原始", "raw", "Raw", "RAW", "审批记录"])


def _sample_column_limit() -> int:
    try:
        return max(
            8,
            min(
                int(_env_value("SCHOOL_SAMPLE_TABLE_COLUMN_LIMIT", "TENANT_SAMPLE_TABLE_COLUMN_LIMIT", "24") or "24"),
                40,
            ),
        )
    except Exception:
        return 24


def _sample_cell_max_chars() -> int:
    try:
        return max(
            120,
            min(
                int(_env_value("SCHOOL_SAMPLE_TABLE_CELL_MAX_CHARS", "TENANT_SAMPLE_TABLE_CELL_MAX_CHARS", "400") or "400"),
                1200,
            ),
        )
    except Exception:
        return 400


def _compact_sample_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    cell_limit = _sample_cell_max_chars()
    for row in rows:
        if not isinstance(row, dict):
            continue
        out: dict[str, Any] = {}
        for key, value in row.items():
            key_text = str(key)
            if _is_heavy_sample_column(key_text):
                continue
            out[key_text] = _compact_sample_value(value, limit=cell_limit)
            if len(out) >= _sample_column_limit():
                break
        compacted.append(out)
    return compacted


def _compact_sample_value(value: Any, *, limit: int) -> Any:
    if isinstance(value, (dict, list)):
        return _truncate_text(json.dumps(value, ensure_ascii=False, default=str), limit)
    if isinstance(value, str):
        return _truncate_text(value, limit)
    return value


def _sql_limit(sql: str) -> int | None:
    import re

    match = re.search(r"(?is)\blimit\s+(\d+)\b", str(sql or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _query_may_have_more(
    *,
    row_count: int,
    effective_limit: int,
    limit_applied: bool,
    total_row_count: int | None = None,
) -> bool:
    try:
        clean_row_count = int(row_count or 0)
        clean_limit = int(effective_limit or 0)
    except Exception:
        return False
    if clean_limit <= 0:
        return False
    if total_row_count is not None:
        try:
            return clean_row_count < int(total_row_count)
        except Exception:
            pass
    return clean_row_count >= clean_limit or (bool(limit_applied) and clean_row_count >= clean_limit)


def _count_sql_for_limited_select(sql: str) -> str:
    base = _strip_trailing_limit_offset(sql)
    if not base:
        return ""
    return f"SELECT COUNT(*) AS total_row_count FROM ({base}) AS _gateway_count_subquery"


def _strip_trailing_limit_offset(sql: str) -> str:
    import re

    clean = str(sql or "").strip().rstrip(";").strip()
    if not clean:
        return ""
    clean = re.sub(r"(?is)\s+offset\s+\d+\s*$", "", clean).strip()
    clean = re.sub(r"(?is)\s+limit\s+\d+\s*$", "", clean).strip()
    return clean


def _replace_trailing_limit(sql: str, limit: int) -> str:
    import re

    clean = str(sql or "").strip().rstrip(";").strip()
    if not clean:
        return ""
    clean = re.sub(r"(?is)\s+offset\s+\d+\s*$", "", clean).strip()
    if re.search(r"(?is)\blimit\s+\d+\s*$", clean):
        return re.sub(r"(?is)\blimit\s+\d+\s*$", f"LIMIT {int(limit)}", clean).strip()
    return f"{clean} LIMIT {int(limit)}"


def _first_int(rows: list[dict[str, Any]]) -> int | None:
    if not rows:
        return None
    row = rows[0]
    if not isinstance(row, dict):
        return None
    for value in row.values():
        try:
            return int(value)
        except Exception:
            continue
    return None


def _entity_distinct_stats(rows: list[dict[str, Any]], *, question: str = "") -> dict[str, Any]:
    if not rows:
        return {}
    key = _entity_key_for_rows(rows, question=question)
    if not key:
        return {}
    values = [_normalize_entity_value(row.get(key)) for row in rows if isinstance(row, dict)]
    values = [item for item in values if item]
    if not values:
        return {}
    distinct_values = set(values)
    duplicate_count = max(0, len(values) - len(distinct_values))
    payload: dict[str, Any] = {
        "entity_key": key,
        "distinct_entity_count": len(distinct_values),
        "duplicate_row_count": duplicate_count,
        "entity_count_scope": "returned_rows",
    }
    if duplicate_count:
        payload["entity_count_warning"] = (
            f"当前结果中 {key} 存在重复展开行；回答人数、教师数、学生数或班级数时，"
            "应优先使用 distinct_entity_count，而不是 row_count。"
        )
    return payload


def _sql_evidence_lineage(
    *,
    task_id: str,
    sql: str,
    tables_used: list[str],
    row_count: int,
    query_purpose: str,
    rows: list[dict[str, Any]],
    tenant_id: str,
    schema_name: str,
    effective_limit: int | None,
    total_row_count: int | None,
) -> dict[str, Any]:
    sql_hash = hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()
    return {
        "evidence_ref_id": f"{task_id}:{sql_hash[:12]}",
        "sql_hash": sql_hash,
        "tables_used": [str(item or "").strip() for item in tables_used if str(item or "").strip()],
        "row_count": int(row_count or 0),
        "time_range": _sql_time_range(sql),
        "query_purpose": str(query_purpose or "").strip(),
        "sample_row_fingerprint": _row_fingerprint(rows),
        "meta_context": {
            "tenant_id": str(tenant_id or ""),
            "schema_name": str(schema_name or ""),
            "effective_limit": effective_limit,
            "total_row_count": total_row_count,
        },
    }


def _row_fingerprint(rows: list[dict[str, Any]]) -> str:
    sample = rows[:20] if isinstance(rows, list) else []
    payload = json.dumps(sample, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _sql_time_range(sql: str) -> dict[str, str]:
    dates = re.findall(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", str(sql or ""))
    if not dates:
        return {}
    normalized = sorted(_normalize_date_string(item) for item in dates)
    return {"start": normalized[0], "end": normalized[-1]}


def _normalize_date_string(value: str) -> str:
    try:
        return _dt.date.fromisoformat(str(value)).isoformat()
    except Exception:
        parts = str(value).split("-")
        if len(parts) != 3:
            return str(value)
        year, month, day = parts
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _entity_key_for_rows(rows: list[dict[str, Any]], *, question: str = "") -> str:
    if not rows or not isinstance(rows[0], dict):
        return ""
    keys = [str(key) for key in rows[0].keys()]
    question_text = str(question or "")
    if any(token in question_text for token in ("老师", "教师", "教职工", "任课")):
        key = _first_matching_key(
            keys,
            exact=("教师ID", "教师用户ID", "teacher_id", "user_id", "id", "教师姓名", "老师姓名", "姓名", "name"),
            contains=("教师ID", "教师用户ID", "teacher_id", "user_id", "教师姓名", "老师姓名", "姓名", "name"),
        )
        if key:
            return key
    if any(token in question_text for token in ("学生", "同学")):
        key = _first_matching_key(
            keys,
            exact=("学生ID", "student_id", "id", "学生姓名", "姓名", "name"),
            contains=("学生ID", "student_id", "学生姓名", "姓名", "name"),
        )
        if key:
            return key
    if "班级" in question_text:
        key = _first_matching_key(
            keys,
            exact=("班级ID", "class_id", "id", "班级名称", "班级", "class_name", "name"),
            contains=("班级ID", "class_id", "班级名称", "班级", "class_name", "name"),
        )
        if key:
            return key
    return _first_matching_key(
        keys,
        exact=("教师ID", "学生ID", "班级ID", "id", "姓名", "名称", "name"),
        contains=("教师ID", "学生ID", "班级ID", "_id", "姓名", "名称", "name"),
    )


def _first_matching_key(keys: list[str], *, exact: tuple[str, ...], contains: tuple[str, ...]) -> str:
    normalized = {key.lower(): key for key in keys}
    for candidate in exact:
        found = normalized.get(candidate.lower())
        if found:
            return found
    for candidate in contains:
        needle = candidate.lower()
        for key in keys:
            if needle in key.lower():
                return key
    return ""


def _normalize_entity_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, dict)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value).strip()
    return str(value).strip()


def _json_tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {"input": text}


def _int_value(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _jsonb_array_samples(rows: list[dict[str, Any]], *, column_name: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get(column_name) if isinstance(row, dict) else None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                samples.append(dict(item))
    return samples


def _infer_jsonb_record_schema(samples: list[dict[str, Any]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for row in samples:
        for key, value in row.items():
            clean_key = str(key or "").strip()
            if not _valid_column_name(clean_key):
                continue
            inferred = _infer_pg_type(value)
            fields[clean_key] = _merge_pg_type(fields.get(clean_key), inferred)
    return fields


def _infer_pg_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "numeric"
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return "date"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?", text):
        return "timestamp"
    if re.fullmatch(r"-?\d+", text):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", text):
        return "numeric"
    return "text"


def _merge_pg_type(previous: str | None, current: str) -> str:
    if not previous or previous == current:
        return current
    if previous in {"int", "numeric"} and current in {"int", "numeric"}:
        return "numeric"
    return "text"


_ALLOWED_RECORD_TYPES = {
    "text": "text",
    "date": "date",
    "timestamp": "timestamp",
    "timestamptz": "timestamptz",
    "numeric": "numeric",
    "int": "int",
    "integer": "int",
    "bigint": "bigint",
    "boolean": "boolean",
    "bool": "boolean",
}


def _record_schema_error(record_schema: Any) -> str:
    if not isinstance(record_schema, dict) or not record_schema:
        return "record_schema_required"
    for field, pg_type in record_schema.items():
        if not _valid_column_name(str(field or "")):
            return f"invalid_record_field: {field}"
        if not _pg_record_type(str(pg_type or "")):
            return f"invalid_record_type: {pg_type}"
    return ""


def _pg_record_type(value: str) -> str:
    return _ALLOWED_RECORD_TYPES.get(str(value or "").strip().lower(), "")


def _valid_column_name(value: str) -> bool:
    clean = str(value or "").strip()
    return bool(clean) and '"' not in clean and "\x00" not in clean and len(clean) <= 128


def _safe_main_fields(fields: list[Any]) -> list[str]:
    out: list[str] = []
    for field in fields:
        clean = str(field or "").strip()
        if _valid_column_name(clean) and clean not in out:
            out.append(clean)
    return out[:12]


def _jsonb_recordset_where_error(where: str) -> str:
    clean = str(where or "").strip()
    if not clean:
        return ""
    if ";" in clean or re.search(r"(?is)(--|/\*|\*/)", clean):
        return "invalid_where_clause"
    if re.search(
        r"(?is)\b(insert|update|delete|drop|alter|truncate|copy|call|do|create|grant|revoke|execute|select|from|join|union)\b",
        clean,
    ):
        return "invalid_where_clause"
    refs = re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\.", clean)
    if any(alias not in {"m", "s"} for alias in refs):
        return "where_may_only_reference_m_or_s"
    return ""
