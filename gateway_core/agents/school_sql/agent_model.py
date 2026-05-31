from __future__ import annotations

import os
from typing import Any


def agent_model_for_tool_loop(model: Any) -> Any:
    """Disable provider-native hidden thinking for tool loops when supported.

    Provider-specific thinking payloads are easy to lose across LangGraph tool
    turns, so the public process stream is emitted by the gateway instead.
    """
    if not disable_provider_thinking_enabled():
        return model
    bind = getattr(model, "bind", None)
    if not callable(bind):
        return model
    try:
        return bind(extra_body={"enable_thinking": False})
    except TypeError:
        try:
            return bind(model_kwargs={"extra_body": {"enable_thinking": False}})
        except Exception:
            return model
    except Exception:
        return model


def disable_provider_thinking_enabled() -> bool:
    raw = os.getenv("SCHOOL_REACT_AGENT_DISABLE_PROVIDER_THINKING", "").strip()
    if not raw:
        raw = os.getenv("TENANT_REACT_AGENT_DISABLE_PROVIDER_THINKING", "1").strip()
    return raw.lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
