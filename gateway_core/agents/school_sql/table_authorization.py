from __future__ import annotations


def table_ref_in_allowlist(ref: str, allowed_refs: list[str], *, schema_name: str) -> bool:
    # inspect/sample 只能补证据，不能把未被 ddl_search 召回的表扩进 SQL 白名单。
    ref_set = _normalized_ref_set([ref], schema_name=schema_name)
    allowed_set = _normalized_ref_set(allowed_refs, schema_name=schema_name)
    return bool(ref_set & allowed_set)


def table_ref_not_authorized_payload(*, source: str, error: str, table_ref: str) -> dict[str, object]:
    return {"source": source, "allowed": False, "error": error, "table_ref": table_ref}


def _normalized_ref_set(refs: list[str], *, schema_name: str) -> set[str]:
    normalized: set[str] = set()
    for ref in refs:
        clean = str(ref or "").strip()
        if not clean:
            continue
        lowered = clean.lower()
        normalized.add(lowered)
        if "." not in lowered and schema_name:
            normalized.add(f"{str(schema_name).lower()}.{lowered}")
    return normalized
