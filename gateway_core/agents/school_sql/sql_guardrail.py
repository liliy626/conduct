from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from gateway_core.agents.school_sql.sql_utils import is_non_business_identifier, normalize_ref
from gateway_core.school.schema_index import SchoolDatasetIndex, SchoolSchemaIndex


_FORBIDDEN_SQL_TOKENS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "copy",
    "call",
    "do",
    "create",
    "grant",
    "revoke",
    "vacuum",
    "analyze",
    "refresh",
    "execute",
}


@dataclass(frozen=True)
class RawSqlGuardrailResult:
    allowed: bool
    sql: str = ""
    reason: str = ""
    allowed_views: list[str] = field(default_factory=list)
    blocked_tokens: list[str] = field(default_factory=list)
    referenced_views: list[str] = field(default_factory=list)
    limit_applied: bool = False


def validate_raw_sql(
    package_index: SchoolSchemaIndex,
    raw_sql: str,
    *,
    max_limit: int = 100,
    extra_allowed_refs: list[str] | None = None,
    allowed_schema: str | None = None,
) -> RawSqlGuardrailResult:
    clean_sql = _clean_sql(raw_sql)
    allowed_views = _allowed_view_refs(package_index, extra_allowed_refs=extra_allowed_refs)
    clean_allowed_schema = str(allowed_schema or package_index.source_schema or "").strip()
    if not clean_sql:
        return RawSqlGuardrailResult(False, reason="empty_sql", allowed_views=allowed_views)
    if ";" in clean_sql.rstrip(";"):
        return RawSqlGuardrailResult(False, reason="multiple_statements_are_not_allowed", allowed_views=allowed_views)
    clean_sql = clean_sql.rstrip(";").strip()
    if re.search(r"(--|/\*|\*/)", clean_sql):
        return RawSqlGuardrailResult(False, reason="sql_comments_are_not_allowed", allowed_views=allowed_views)
    if not re.match(r"(?is)^\s*select\b", clean_sql):
        return RawSqlGuardrailResult(False, reason="only_single_select_is_allowed", allowed_views=allowed_views)

    blocked_tokens = sorted(
        {token for token in _FORBIDDEN_SQL_TOKENS if re.search(rf"(?is)\b{re.escape(token)}\b", clean_sql)}
    )
    if blocked_tokens:
        return RawSqlGuardrailResult(
            False,
            reason="forbidden_sql_token",
            allowed_views=allowed_views,
            blocked_tokens=blocked_tokens,
        )
    if re.search(r"(?is)\b(pg_catalog|information_schema)\b", clean_sql):
        return RawSqlGuardrailResult(False, reason="system_schema_is_not_allowed", allowed_views=allowed_views)

    refs = _extract_table_refs(clean_sql)
    if not refs:
        return RawSqlGuardrailResult(False, reason="no_tenant_view_referenced", allowed_views=allowed_views)
    unknown_refs = [ref for ref in refs if normalize_ref(ref) not in {normalize_ref(item) for item in allowed_views}]
    if unknown_refs:
        return RawSqlGuardrailResult(
            False,
            reason=f"referenced_view_not_in_school_schema: {', '.join(unknown_refs)}",
            allowed_views=allowed_views,
            referenced_views=refs,
        )
    schema_reason = _schema_scope_reason(refs, allowed_schema=clean_allowed_schema)
    if schema_reason:
        return RawSqlGuardrailResult(
            False,
            reason=schema_reason,
            allowed_views=allowed_views,
            referenced_views=refs,
        )

    sensitive_reason = _sensitive_field_reason(package_index, clean_sql, refs)
    if sensitive_reason:
        return RawSqlGuardrailResult(
            False,
            reason=sensitive_reason,
            allowed_views=allowed_views,
            referenced_views=refs,
        )

    non_business_reason = _non_business_field_reason(clean_sql)
    if non_business_reason:
        return RawSqlGuardrailResult(
            False,
            reason=non_business_reason,
            allowed_views=allowed_views,
            referenced_views=refs,
        )

    limited_sql, limit_applied = _cap_limit(clean_sql, max_limit=max_limit)
    return RawSqlGuardrailResult(
        True,
        sql=limited_sql,
        allowed_views=allowed_views,
        referenced_views=refs,
        limit_applied=limit_applied,
    )


def _clean_sql(raw_sql: str) -> str:
    return re.sub(r"\s+", " ", str(raw_sql or "").strip())


def _allowed_view_refs(package_index: SchoolSchemaIndex, *, extra_allowed_refs: list[str] | None = None) -> list[str]:
    refs = []
    for dataset in package_index.datasets:
        refs.append(f'{dataset.source_schema}.{dataset.source_view}')
        refs.append(dataset.source_view)
    for item in extra_allowed_refs or []:
        ref = str(item or "").strip()
        if not ref:
            continue
        refs.append(ref)
    return sorted(set(refs))


def _extract_table_refs(sql: str) -> list[str]:
    refs = []
    relation_sql = _strip_expression_from_keywords(sql)
    identifier = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_\u4e00-\u9fff]*)'
    pattern = re.compile(rf"(?is)\b(?:from|join)\s+({identifier}(?:\s*\.\s*{identifier})?)")
    for match in pattern.finditer(relation_sql):
        raw = match.group(1)
        ref = ".".join(_unquote_identifier(part.strip()) for part in raw.split("."))
        if ref.strip().lower() == "lateral":
            continue
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _strip_expression_from_keywords(sql: str) -> str:
    clean = str(sql or "")
    # PostgreSQL EXTRACT(field FROM expression) contains a FROM keyword that is
    # not a relation reference. Remove these expressions before scanning FROM/JOIN.
    return re.sub(r"(?is)\bextract\s*\(\s*[A-Za-z_]+\s+from\s+[^)]*\)", "EXTRACT_EXPR", clean)


def _unquote_identifier(value: str) -> str:
    clean = str(value or "").strip()
    if clean.startswith('"') and clean.endswith('"'):
        return clean[1:-1].replace('""', '"')
    return clean


def _schema_scope_reason(refs: list[str], *, allowed_schema: str) -> str:
    clean_allowed = str(allowed_schema or "").strip().lower()
    if not clean_allowed:
        return ""
    for ref in refs:
        parts = [part for part in str(ref or "").split(".") if part]
        if len(parts) >= 2 and parts[0].strip('"').lower() != clean_allowed:
            return f"cross_schema_is_not_allowed: {ref}"
    return ""


def _sensitive_field_reason(package_index: SchoolSchemaIndex, sql: str, refs: list[str]) -> str:
    datasets = _datasets_for_refs(package_index, refs)
    if _selects_star(sql):
        for dataset in datasets:
            if dataset.sensitive_fields:
                return "select_star_would_expose_sensitive_fields"
    for dataset in datasets:
        for field_id in dataset.sensitive_fields:
            try:
                field = dataset.field(field_id)
            except KeyError:
                continue
            if _identifier_mentioned(sql, field.source_field) or _identifier_mentioned(sql, field.field_id.split(".", 1)[-1]):
                return f"sensitive_field_is_not_allowed: {field.field_id}"
    return ""


def _datasets_for_refs(package_index: SchoolSchemaIndex, refs: list[str]) -> list[SchoolDatasetIndex]:
    normalized_refs = {normalize_ref(item) for item in refs}
    datasets = []
    for dataset in package_index.datasets:
        if normalize_ref(dataset.source_view) in normalized_refs or normalize_ref(
            f"{dataset.source_schema}.{dataset.source_view}"
        ) in normalized_refs:
            datasets.append(dataset)
    return datasets


def _selects_star(sql: str) -> bool:
    select_part = re.split(r"(?is)\bfrom\b", sql, maxsplit=1)[0]
    return "*" in select_part


def _identifier_mentioned(sql: str, identifier: str) -> bool:
    ident = str(identifier or "").strip()
    if not ident:
        return False
    quoted = '"' + ident.replace('"', '""') + '"'
    return quoted in sql or bool(re.search(rf"(?is)(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])", sql))


def _non_business_field_reason(sql: str) -> str:
    for identifier in _quoted_identifiers(sql):
        if is_non_business_identifier(identifier):
            return f"non_business_field_is_not_allowed: {identifier}"
    return ""


def _quoted_identifiers(sql: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r'"((?:[^"]|"")*)"', str(sql or "")):
        identifier = match.group(1).replace('""', '"')
        if identifier and identifier not in out:
            out.append(identifier)
    return out


def _cap_limit(sql: str, *, max_limit: int) -> tuple[str, bool]:
    clean_limit = max(1, int(max_limit or 100))
    match = re.search(r"(?is)\blimit\s+(\d+)\b", sql)
    if not match:
        return f"{sql} LIMIT {clean_limit}", True
    try:
        value = int(match.group(1))
    except Exception:
        return re.sub(r"(?is)\blimit\s+\S+", f"LIMIT {clean_limit}", sql), True
    if value <= clean_limit:
        return sql, False
    return sql[: match.start(1)] + str(clean_limit) + sql[match.end(1) :], True
