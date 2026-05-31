from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    messages: List[ChatMessage]
    # stream 缺省时不直接定为 False，而是在主链路里结合请求类型决定：
    # - 普通真实聊天：默认流式
    # - title/follow_ups/tags 等 meta 请求：默认非流式
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    chat_id: Optional[str] = None
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    thread_id: Optional[str] = None


def now_ts() -> int:
    return int(time.time())


def stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if t:
                    texts.append(str(t))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts)
    return str(content)


def to_lc_messages(messages: List[ChatMessage]) -> List[BaseMessage]:
    out: List[BaseMessage] = []
    for m in messages:
        role = (m.role or "").strip().lower()
        content = stringify_content(m.content)
        if role == "system":
            out.append(SystemMessage(content=content))
        elif role in {"assistant", "ai"}:
            out.append(AIMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out
