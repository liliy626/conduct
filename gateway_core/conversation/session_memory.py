from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    question: str
    answer: str
    created_at: float = field(default_factory=time.time)


_LOCK = threading.Lock()
_STORE: "OrderedDict[str, list[ConversationTurn]]" = OrderedDict()


def conversation_memory_key(*, chat_id: str, token: str = "", school_scope: str = "") -> str:
    clean_chat = str(chat_id or "").strip()
    if not clean_chat:
        return ""
    token_hash = hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:16] if token else "anonymous"
    scope_hash = hashlib.sha256(str(school_scope or "").encode("utf-8")).hexdigest()[:8] if school_scope else "default"
    chat_hash = hashlib.sha256(clean_chat.encode("utf-8")).hexdigest()[:24]
    return f"{token_hash}:{scope_hash}:{chat_hash}"


def get_conversation_context(key: str, *, max_turns: int | None = None, max_chars: int | None = None) -> str:
    clean_key = str(key or "").strip()
    if not clean_key:
        return ""
    max_turns = max_turns or _max_turns()
    max_chars = max_chars or _max_chars()
    now = time.time()
    with _LOCK:
        _evict_expired_locked(now)
        turns = list(_STORE.get(clean_key) or [])[-max_turns:]
    lines: list[str] = []
    for turn in turns:
        question = sanitize_conversation_text(turn.question)
        answer = sanitize_conversation_text(turn.answer)
        if question:
            lines.append(f"用户: {_clip(question, 900)}")
        if answer:
            lines.append(f"助手: {_clip(answer, 1400)}")
    context = "\n".join(lines).strip()
    if len(context) > max_chars:
        context = context[-max_chars:].lstrip()
    return context


def remember_conversation_turn(key: str, *, question: str, answer: str) -> None:
    clean_key = str(key or "").strip()
    clean_question = str(question or "").strip()
    clean_answer = sanitize_conversation_text(answer)
    if not clean_key or not clean_question or not clean_answer:
        return
    now = time.time()
    with _LOCK:
        _evict_expired_locked(now)
        turns = _STORE.setdefault(clean_key, [])
        turns.append(ConversationTurn(question=_clip(clean_question, 1600), answer=_clip(clean_answer, 2600), created_at=now))
        del turns[: max(0, len(turns) - _max_turns())]
        _STORE.move_to_end(clean_key)
        while len(_STORE) > _max_sessions():
            _STORE.popitem(last=False)


def _evict_expired_locked(now: float) -> None:
    ttl = _ttl_seconds()
    if ttl <= 0:
        return
    expired: list[str] = []
    for key, turns in _STORE.items():
        if not turns or now - turns[-1].created_at > ttl:
            expired.append(key)
    for key in expired:
        _STORE.pop(key, None)


def _clip(text: Any, limit: int) -> str:
    clean = " ".join(str(text or "").replace("\x00", " ").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rstrip() + "...[截断]"


def sanitize_conversation_text(text: Any) -> str:
    clean = str(text or "").replace("\x00", " ")
    if not clean.strip():
        return ""
    clean = _strip_reasoning_details(clean)
    unescaped = html.unescape(clean)
    if unescaped != clean:
        clean = _strip_reasoning_details(unescaped)
    return clean.strip()


def _strip_reasoning_details(text: str) -> str:
    return re.sub(
        r"<details\b[^>]*\btype=[\"']?reasoning[\"']?[^>]*>.*?</details>",
        " ",
        str(text or ""),
        flags=re.IGNORECASE | re.DOTALL,
    )


def _max_turns() -> int:
    return _int_env("GATEWAY_CONVERSATION_MEMORY_MAX_TURNS", 8, minimum=1, maximum=30)


def _max_chars() -> int:
    return _int_env("GATEWAY_CONVERSATION_MEMORY_MAX_CHARS", 6000, minimum=500, maximum=20000)


def _max_sessions() -> int:
    return _int_env("GATEWAY_CONVERSATION_MEMORY_MAX_SESSIONS", 1000, minimum=10, maximum=10000)


def _ttl_seconds() -> int:
    return _int_env("GATEWAY_CONVERSATION_MEMORY_TTL_SECONDS", 86400, minimum=0, maximum=604800)


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except Exception:
        return default
    return max(minimum, min(maximum, value))
