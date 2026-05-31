from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


_SAFE_THREAD_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def extract_openwebui_chat_id(req: Any, request: Any = None) -> str:
    """Extract a stable OpenWebUI chat/conversation id from body metadata or headers."""
    for value in _candidate_values(req, request):
        clean = str(value or "").strip()
        if clean:
            return clean
    return ""


def build_langgraph_thread_id(*, chat_id: str, school_id: str = "", tenant_id: str = "", gateway_token_hash: str = "") -> str:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return ""
    clean_school = _safe_segment(school_id or tenant_id or "unknown_school")
    token_part = _safe_segment(gateway_token_hash or "unknown_key")
    chat_part = _safe_segment(clean_chat)
    if len(chat_part) > 96:
        chat_part = hashlib.sha256(clean_chat.encode("utf-8")).hexdigest()
    return f"owui:{token_part}:{clean_school}:{chat_part}"


def _candidate_values(req: Any, request: Any = None) -> list[Any]:
    values: list[Any] = []
    for attr in ["thread_id", "chat_id", "conversation_id", "session_id"]:
        values.append(getattr(req, attr, None))

    metadata = getattr(req, "metadata", None)
    if isinstance(metadata, Mapping):
        values.extend(_metadata_values(metadata))

    model_extra = getattr(req, "model_extra", None)
    if isinstance(model_extra, Mapping):
        for attr in ["thread_id", "chat_id", "conversation_id", "session_id"]:
            values.append(model_extra.get(attr))
        extra_metadata = model_extra.get("metadata")
        if isinstance(extra_metadata, Mapping):
            values.extend(_metadata_values(extra_metadata))

    headers = getattr(request, "headers", None)
    if headers is not None:
        for header in [
            "x-openwebui-chat-id",
            "x-open-webui-chat-id",
            "x-chat-id",
            "x-conversation-id",
            "x-thread-id",
            "x-session-id",
        ]:
            try:
                values.append(headers.get(header))
            except Exception:
                pass
    return values


def _metadata_values(metadata: Mapping[str, Any]) -> list[Any]:
    values = []
    for key in ["thread_id", "chat_id", "conversation_id", "session_id"]:
        values.append(metadata.get(key))
    chat = metadata.get("chat")
    if isinstance(chat, Mapping):
        values.extend([chat.get("id"), chat.get("chat_id")])
    conversation = metadata.get("conversation")
    if isinstance(conversation, Mapping):
        values.extend([conversation.get("id"), conversation.get("conversation_id")])
    return values


def _safe_segment(value: str) -> str:
    clean = _SAFE_THREAD_RE.sub("_", str(value or "").strip()).strip("._:-")
    return clean or hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]
