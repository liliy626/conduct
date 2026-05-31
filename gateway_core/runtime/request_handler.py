from __future__ import annotations

"""请求预处理工具。

职责：
- 规范化请求头；
- 完成网关鉴权；
- 组装会话上下文（token 与学校范围）。
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from gateway_core.runtime.gateway_config import _require_gateway_auth, _set_viewer_access


def normalize_header_text(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    return None


@dataclass(frozen=True)
class ChatSessionContext:
    token: str
    school_scope: Optional[str]


def prepare_chat_session_context(
    authorization: Optional[str],
    x_school_scope: Optional[str],
    x_user_id: Optional[str],
    x_user_role: Optional[str],
    x_user_permissions: Optional[str],
) -> Tuple[str, ChatSessionContext]:
    normalized_authorization = normalize_header_text(authorization)
    normalized_school_scope = normalize_header_text(x_school_scope)
    normalized_user_id = normalize_header_text(x_user_id)
    normalized_user_role = normalize_header_text(x_user_role)
    normalized_user_permissions = normalize_header_text(x_user_permissions)

    token = _require_gateway_auth(normalized_authorization)
    _set_viewer_access(normalized_user_role, normalized_user_permissions, normalized_user_id)

    return token, ChatSessionContext(token=token, school_scope=normalized_school_scope)
