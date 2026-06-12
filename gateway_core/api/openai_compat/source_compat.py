from __future__ import annotations

from typing import Any


def merge_openwebui_sources(target: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {openwebui_source_url(item) for item in target}
    added: list[dict[str, Any]] = []
    for item in incoming:
        if not isinstance(item, dict):
            continue
        url = openwebui_source_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        target.append(item)
        added.append(item)
    return added


def openwebui_source_url(source: dict[str, Any]) -> str:
    nested = source.get("source") if isinstance(source.get("source"), dict) else {}
    url = str(source.get("url") or nested.get("url") or "").strip()
    if url:
        return url
    metadata = source.get("metadata")
    if isinstance(metadata, list):
        for item in metadata:
            if isinstance(item, dict) and str(item.get("url") or "").strip():
                return str(item.get("url") or "").strip()
    return ""
