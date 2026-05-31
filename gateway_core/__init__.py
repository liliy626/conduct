"""
Gateway core modules split out from app.py.

Keep package thin and avoid side-effect imports here; import targets explicitly where needed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Dict

_COMPAT_MODULES: Dict[str, str] = {
    "chat_pipeline": "gateway_core.api.openai_compat.chat_pipeline",
    "question_monitor": "gateway_core.observability.question_monitor",
    "gateway_runtime": "gateway_core.runtime.gateway_runtime",
    "gateway_config": "gateway_core.runtime.gateway_config",
    "runtime_context": "gateway_core.runtime.runtime_context",
    "request_handler": "gateway_core.runtime.request_handler",
    "admin_endpoints": "gateway_core.runtime.admin.endpoints",
}


def __getattr__(name: str):
    target = _COMPAT_MODULES.get(name)
    if not target:
        raise AttributeError(f"module 'gateway_core' has no attribute '{name}'")
    module = import_module(target)
    globals()[name] = module
    return module


__all__ = sorted(_COMPAT_MODULES.keys())
