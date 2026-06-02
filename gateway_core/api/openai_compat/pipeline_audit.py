from __future__ import annotations

import time
from functools import wraps
from typing import Any, Awaitable, Callable, TypeVar


T = TypeVar("T")


def trace_pipeline_audit(route_name: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    clean_route = str(route_name or "").strip()

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            started = time.time()
            try:
                return await func(*args, **kwargs)
            finally:
                setattr(wrapper, "__last_pipeline_elapsed_ms__", int((time.time() - started) * 1000))

        setattr(wrapper, "__pipeline_route_name__", clean_route)
        return wrapper

    return decorator
