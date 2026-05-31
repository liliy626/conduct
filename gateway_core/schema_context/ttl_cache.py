from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheHit:
    value: Any
    age_seconds: float


class TTLCache:
    def __init__(self, *, max_entries: int = 256, ttl_seconds: int = 300) -> None:
        self.max_entries = max(1, int(max_entries or 256))
        self.ttl_seconds = max(1, int(ttl_seconds or 300))
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> CacheHit | None:
        clean_key = str(key or "").strip()
        if not clean_key:
            return None
        now = time.time()
        with self._lock:
            item = self._items.get(clean_key)
            if item is None:
                return None
            created_at, value = item
            age = now - created_at
            if age > self.ttl_seconds:
                self._items.pop(clean_key, None)
                return None
            return CacheHit(value=value, age_seconds=age)

    def set(self, key: str, value: Any) -> None:
        clean_key = str(key or "").strip()
        if not clean_key:
            return
        with self._lock:
            self._items[clean_key] = (time.time(), value)
            while len(self._items) > self.max_entries:
                oldest_key = min(self._items, key=lambda item_key: self._items[item_key][0])
                self._items.pop(oldest_key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
