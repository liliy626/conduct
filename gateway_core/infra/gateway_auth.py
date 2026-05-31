from typing import List, Optional

from fastapi import HTTPException


def get_gateway_keys(raw_keys: str) -> List[str]:
    """中文注释：实现get_gateway_keys的核心业务处理流程。"""
    raw = (raw_keys or "").strip()
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def extract_bearer_token(authorization: Optional[str]) -> str:
    """中文注释：实现extract_bearer_token的核心业务处理流程。"""
    if not authorization:
        return ""
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def require_gateway_auth(authorization: Optional[str], keys: List[str]) -> str:
    """中文注释：实现require_gateway_auth的核心业务处理流程。"""
    if not keys:
        raise HTTPException(status_code=500, detail="gateway auth not configured: set GATEWAY_API_KEY in .env")

    token = extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if token not in keys:
        raise HTTPException(status_code=401, detail="invalid gateway api key")
    return token
