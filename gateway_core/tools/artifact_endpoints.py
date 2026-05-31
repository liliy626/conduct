from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .artifact_store import resolve_artifact_path


router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("/{artifact_path:path}")
def download_artifact(artifact_path: str) -> FileResponse:
    try:
        path = resolve_artifact_path(artifact_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    media_type = _media_type(path)
    if path.suffix.lower() in {".html", ".png", ".jpg", ".jpeg", ".svg"}:
        return FileResponse(path, media_type=media_type, content_disposition_type="inline")
    return FileResponse(path, filename=path.name, media_type=media_type)


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"
