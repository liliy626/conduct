from __future__ import annotations

import re
from typing import Any

from gateway_core.api.openai_compat.answer_quality_payload import extract_context_json_payload


def apply_numbered_citations(
    text: str,
    *,
    meilanhu_context: str | None,
    wants_json: bool = False,
) -> str:
    if wants_json:
        return str(text or "")
    body = str(text or "")
    if not body.strip():
        return body
    sources = citation_sources_from_text_payload(meilanhu_context)
    if not sources:
        return body

    main_body, _existing_references = _split_reference_section(body)
    out = main_body
    used_refs: set[int] = set()
    for source in sources:
        ref = int(source["ref"])
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        if url and url in out:
            out = _replace_markdown_url(out, url, ref)
            used_refs.add(ref)
        elif title and title in out:
            out = out.replace(title, f"{title}[{ref}]", 1)
            used_refs.add(ref)

    out = _remove_remaining_explicit_urls(out, sources)
    out = re.sub(r"(\[\d+])\s+\1", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if not used_refs:
        return main_body.rstrip()
    return out.rstrip()


def openwebui_sources_from_payload(meilanhu_context: str | None) -> list[dict[str, Any]]:
    return openwebui_sources_from_citation_sources(citation_sources_from_text_payload(meilanhu_context))


def openwebui_sources_from_citation_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for source in sources:
        title = str(source.get("title") or source.get("url") or "引用来源").strip()
        url = str(source.get("url") or "").strip()
        supports = str(source.get("supports") or title or url).strip()
        metadata: dict[str, Any] = {
            "source": title,
            "type": str(source.get("source_type") or "source"),
        }
        if url:
            metadata["url"] = url
        out.append(
            {
                "document": [supports or title or url],
                "metadata": [metadata],
                "source": {
                    "name": title or url,
                    **({"url": url} if url else {}),
                },
            }
        )
    return out


def citation_sources_from_text_payload(meilanhu_context: str | None) -> list[dict[str, Any]]:
    payload = extract_context_json_payload(meilanhu_context)
    if not isinstance(payload, dict):
        return []
    return citation_sources_from_payload(payload)


def citation_sources_from_payload(payload: Any) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add_source(raw: Any, *, source_type: str) -> None:
        if not isinstance(raw, dict):
            return
        url = str(
            raw.get("official_url")
            or raw.get("url")
            or raw.get("source_url")
            or raw.get("canonical_url")
            or ""
        ).strip()
        title = str(raw.get("title") or raw.get("name") or raw.get("source") or raw.get("source_name") or url).strip()
        if not title and not url:
            return
        key = (title, url)
        if key in seen:
            return
        seen.add(key)
        sources.append(
            {
                "ref": len(sources) + 1,
                "title": title or f"引用来源{len(sources) + 1}",
                "url": url,
                "source_type": source_type,
                "supports": str(raw.get("chunk") or raw.get("snippet") or raw.get("content") or raw.get("summary") or "")[:220],
            }
        )

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("policy_evidence"), list):
                for item in value.get("policy_evidence") or []:
                    add_source(item, source_type="official_policy")
            if isinstance(value.get("web_evidence"), list):
                for item in value.get("web_evidence") or []:
                    add_source(item, source_type="web")
            sources_payload = value.get("sources")
            if isinstance(sources_payload, dict):
                for item in sources_payload.get("web_sources") or []:
                    add_source(item, source_type="web")
                for url in sources_payload.get("policy_urls") or []:
                    add_source({"title": str(url or ""), "official_url": str(url or "")}, source_type="official_policy")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return sources[:12]


def _replace_markdown_url(text: str, url: str, ref: int) -> str:
    escaped_url = re.escape(url)
    # Replace [url](url) or [title](url) with [ref]. Keep the surrounding title
    # text outside the link if the model already printed it nearby.
    out = re.sub(rf"\[[^\]]*?]\({escaped_url}\)", f"[{ref}]", text)
    out = out.replace(url, f"[{ref}]")
    return out


def _remove_remaining_explicit_urls(text: str, sources: list[dict[str, Any]]) -> str:
    out = text
    for source in sources:
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        escaped_url = re.escape(url)
        ref = int(source["ref"])
        out = re.sub(rf"\s*\[[^\]]*?]\({escaped_url}\)", f"[{ref}]", out)
        out = out.replace(url, f"[{ref}]")
    return re.sub(r"(?m)^\s*https?://\S+\s*$", "", out)


def _split_reference_section(text: str) -> tuple[str, str]:
    match = re.search(r"\n\s*(?:#{1,6}\s*)?(?:\*\*)?引用来源[：:]?(?:\*\*)?\s*\n", text)
    if not match:
        return text, ""
    return text[: match.start()].rstrip(), text[match.start():].strip()


__all__ = [
    "apply_numbered_citations",
    "citation_sources_from_text_payload",
    "citation_sources_from_payload",
    "openwebui_sources_from_payload",
    "openwebui_sources_from_citation_sources",
]
