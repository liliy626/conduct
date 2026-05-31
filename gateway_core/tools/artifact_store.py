from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote


_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def artifact_root() -> Path:
    root = os.getenv("GATEWAY_ARTIFACT_DIR", "artifacts/gateway").strip() or "artifacts/gateway"
    path = Path(root)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_artifact_path(*, tenant_id: str, tool_name: str, suffix: str) -> Path:
    tenant = _safe_segment(tenant_id or "default")
    tool = _safe_segment(tool_name or "tool")
    directory = artifact_root() / tenant / tool
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_segment(_random_id())}{suffix}"
    return directory / filename


def artifact_download_url(path: str | Path) -> str:
    """Build a browser-downloadable URL for a generated artifact."""
    rel = artifact_relative_path(path)
    base = os.getenv("GATEWAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    url_path = f"/v1/artifacts/{quote(rel, safe='/')}"
    return f"{base}{url_path}" if base else url_path


def artifact_relative_path(path: str | Path) -> str:
    root = artifact_root().resolve()
    target = Path(path).resolve()
    try:
        return target.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("artifact path is outside artifact root") from exc


def resolve_artifact_path(relative_path: str) -> Path:
    root = artifact_root().resolve()
    target = (root / str(relative_path or "")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path is outside artifact root") from exc
    return target


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    return cleaned[:80] or "artifact"


def _random_id() -> str:
    return os.urandom(12).hex()
