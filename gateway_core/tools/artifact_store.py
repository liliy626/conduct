from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote
from urllib.parse import urlparse


_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_DEFAULT_ALLOWED_ARTIFACT_HOSTS = (
    "127.0.0.1",
    "api.openai.com",
    "cdn.example.test",
    "cdn.yili-edu.com",
    "localhost",
)


class ArtifactValidationError(ValueError):
    """Raised when an external artifact URL violates the gateway allowlist."""


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
    if base and urlparse(base).scheme != "https":
        return url_path
    return f"{base}{url_path}" if base else url_path


def validate_external_artifact_url(url: str) -> str:
    clean = str(url or "").strip()
    parsed = urlparse(clean)
    if clean.startswith("/v1/artifacts/"):
        return clean
    if parsed.scheme != "https" or not parsed.hostname:
        raise ArtifactValidationError("artifact URL must be an HTTPS URL or local artifact path")
    host = parsed.hostname.lower()
    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in _allowed_artifact_hosts()):
        raise ArtifactValidationError(f"artifact URL host `{host}` 不在允许域名白名单")
    return clean


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


def _allowed_artifact_hosts() -> tuple[str, ...]:
    configured = os.getenv("GATEWAY_ALLOWED_ARTIFACT_URL_HOSTS", "").strip()
    if not configured:
        return _DEFAULT_ALLOWED_ARTIFACT_HOSTS
    return tuple(
        host
        for item in configured.split(",")
        if (host := item.strip().lower())
    ) or _DEFAULT_ALLOWED_ARTIFACT_HOSTS


def _safe_segment(value: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("_", str(value or "").strip()).strip("._")
    return cleaned[:80] or "artifact"


def _random_id() -> str:
    return os.urandom(12).hex()
