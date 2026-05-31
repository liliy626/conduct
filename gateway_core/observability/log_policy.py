from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Sequence

LOGGER = logging.getLogger("gateway.log_policy")

_CLEANUP_LAST_RUN: Dict[str, float] = {}


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def runtime_writable_base_dir(project_root: Optional[Path] = None) -> Path:
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(Path(project_root).expanduser())
    raw_runtime = os.getenv("YILI_RUNTIME_DIR", "").strip()
    if raw_runtime:
        candidates.append(Path(raw_runtime).expanduser())
    xdg_runtime = os.getenv("XDG_RUNTIME_DIR", "").strip()
    if xdg_runtime:
        candidates.append(Path(xdg_runtime).expanduser() / "yili")
    candidates.extend(
        [
            Path("/tmp/yili_runtime"),
            Path.home() / ".cache" / "yili_runtime",
        ]
    )

    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_probe"
            with probe.open("a", encoding="utf-8"):
                pass
            probe.unlink(missing_ok=True)
            return base
        except Exception:
            continue
    return Path("/tmp/yili_runtime")


def observability_log_dir(project_root: Optional[Path] = None) -> Path:
    raw = os.getenv("OBSERVABILITY_LOG_DIR", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            with probe.open("a", encoding="utf-8"):
                pass
            probe.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            LOGGER.warning(
                "observability_log_dir_unwritable path=%s err=%s",
                candidate,
                exc.__class__.__name__,
            )
    return runtime_writable_base_dir(project_root=project_root) / "logs" / "question_monitor"


def observability_retention_days(default: int = 7) -> int:
    raw = os.getenv("OBSERVABILITY_LOG_RETENTION_DAYS", str(default)).strip()
    try:
        value = int(raw)
        return max(0, value)
    except Exception:
        return default


def maybe_cleanup_jsonl_logs(
    *,
    log_dir: Path,
    prefixes: Sequence[str],
    retention_days: Optional[int] = None,
    min_interval_sec: int = 600,
) -> None:
    """按日期文件名清理旧日志（默认保留 7 天），并节流执行频率。"""
    days = observability_retention_days() if retention_days is None else max(0, int(retention_days))
    if days <= 0:
        return
    key = str(log_dir.resolve())
    now = time.time()
    last = _CLEANUP_LAST_RUN.get(key, 0.0)
    if now - last < max(30, int(min_interval_sec)):
        return
    _CLEANUP_LAST_RUN[key] = now

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    cutoff_date = (datetime.now() - timedelta(days=days)).date()
    day_re = re.compile(r"(20\d{2}-\d{2}-\d{2})")
    allowed_prefixes = tuple(str(item or "").strip() for item in prefixes if str(item or "").strip())
    if not allowed_prefixes:
        return

    for path in log_dir.glob("*.jsonl"):
        name = path.name
        if not name.startswith(allowed_prefixes):
            continue
        try:
            m = day_re.search(name)
            if m:
                day_text = m.group(1)
                day = datetime.strptime(day_text, "%Y-%m-%d").date()
                if day < cutoff_date:
                    path.unlink(missing_ok=True)
                    continue
            # 兼容无日期老文件：按 mtime 清理
            modified = datetime.fromtimestamp(path.stat().st_mtime).date()
            if modified < cutoff_date:
                path.unlink(missing_ok=True)
        except Exception:
            continue
