from __future__ import annotations

import re
from typing import Iterable


_KEYWORDS = {
    "select",
    "from",
    "where",
    "group",
    "by",
    "order",
    "limit",
    "offset",
    "as",
    "desc",
    "asc",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "and",
    "or",
    "on",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "having",
}


def normalize_sql_to_canonical(raw_sql: str) -> str:
    """Return a deterministic SQL representation for hashing and execution.

    If sqlglot is installed, use its PostgreSQL parser/renderer. The fallback is
    deliberately conservative: it removes comments, normalizes whitespace,
    normalizes common aggregate spacing, adds missing ``AS`` for simple aliases,
    and rewrites ``ORDER BY <select-position>`` to the matching select alias when
    this can be inferred safely.
    """

    raw = _strip_sql_comments(str(raw_sql or "")).strip().rstrip(";").strip()
    if not raw:
        return ""
    parsed = _sqlglot_canonical(raw)
    if parsed:
        return parsed
    return _fallback_canonical(raw)


def _sqlglot_canonical(sql: str) -> str:
    try:
        import sqlglot  # type: ignore

        expression = sqlglot.parse_one(sql, read="postgres")
        return expression.sql(dialect="postgres", pretty=False).strip().rstrip(";")
    except Exception:
        return ""


def _fallback_canonical(sql: str) -> str:
    text = _normalize_common_sql_text(sql)
    select_clause, rest = _split_select_rest(text)
    if select_clause:
        select_items = _split_top_level_commas(select_clause)
        normalized_items = [_normalize_select_item(item) for item in select_items]
        text = "SELECT " + ", ".join(normalized_items) + " " + rest.strip()
        text = _normalize_order_by_position(text, normalized_items)
    return _uppercase_keywords(_normalize_common_sql_text(text))


def _strip_sql_comments(sql: str) -> str:
    text = re.sub(r"(?m)--.*?$", " ", sql)
    text = re.sub(r"(?s)/\*.*?\*/", " ", text)
    return text


def _normalize_common_sql_text(sql: str) -> str:
    text = re.sub(r"\s+", " ", sql).strip().rstrip(";").strip()
    text = re.sub(r"\bcount\s*\(\s*\*\s*\)", "COUNT(*)", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsum\s*\(", "SUM(", text, flags=re.IGNORECASE)
    text = re.sub(r"\bavg\s*\(", "AVG(", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmin\s*\(", "MIN(", text, flags=re.IGNORECASE)
    text = re.sub(r"\bmax\s*\(", "MAX(", text, flags=re.IGNORECASE)
    return text


def _split_select_rest(sql: str) -> tuple[str, str]:
    match = re.match(r"(?is)^\s*select\s+(.*?)\s+from\s+(.+)$", sql)
    if not match:
        return "", sql
    return match.group(1).strip(), "FROM " + match.group(2).strip()


def _split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            items.append(text[start:index].strip())
            start = index + 1
    items.append(text[start:].strip())
    return [item for item in items if item]


def _normalize_select_item(item: str) -> str:
    clean = _normalize_common_sql_text(item)
    if re.search(r"(?i)\s+as\s+", clean):
        left, right = re.split(r"(?i)\s+as\s+", clean, maxsplit=1)
        return f"{left.strip()} AS {_normalize_identifier_token(right.strip())}"
    match = re.match(r"(?is)^(.+?)\s+([A-Za-z_][\w$]*|\"[^\"]+\")$", clean)
    if not match:
        return clean
    expression, alias = match.group(1).strip(), match.group(2).strip()
    if _looks_like_plain_identifier(expression):
        return clean
    return f"{expression} AS {_normalize_identifier_token(alias)}"


def _normalize_identifier_token(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value
    return value.lower()


def _looks_like_plain_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\"[^\"]+\"|[A-Za-z_][\w$]*)(?:\.(?:\"[^\"]+\"|[A-Za-z_][\w$]*))*", value.strip()))


def _normalize_order_by_position(sql: str, select_items: list[str]) -> str:
    aliases = list(_select_aliases(select_items))

    def replace(match: re.Match[str]) -> str:
        index = int(match.group("idx"))
        direction = (match.group("dir") or "").upper()
        if index < 1 or index > len(aliases) or not aliases[index - 1]:
            return match.group(0)
        suffix = f" {direction}" if direction else ""
        return f"ORDER BY {aliases[index - 1]}{suffix}"

    return re.sub(r"(?i)\border\s+by\s+(?P<idx>\d+)(?:\s+(?P<dir>asc|desc))?", replace, sql, count=1)


def _select_aliases(items: Iterable[str]) -> Iterable[str]:
    for item in items:
        match = re.search(r"(?i)\s+as\s+([A-Za-z_][\w$]*|\"[^\"]+\")\s*$", item)
        if match:
            yield _normalize_identifier_token(match.group(1))
            continue
        yield ""


def _uppercase_keywords(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        return word.upper() if word.lower() in _KEYWORDS else word

    return re.sub(r"\b[A-Za-z_]+\b", replace, sql)

