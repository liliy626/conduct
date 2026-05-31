from __future__ import annotations

from typing import Any, Callable, Dict, List


def collect_request_header_probe(*, request: Any, clip_monitor_text: Callable[[Any], str]) -> Dict[str, Any]:
    x_headers: Dict[str, str] = {}
    x_header_keys: List[str] = []
    try:
        for raw_key, raw_value in request.headers.items():
            key = str(raw_key or "").strip().lower()
            if not key.startswith("x-"):
                continue
            x_header_keys.append(key)
            if any(token in key for token in ("token", "secret", "key", "auth")):
                x_headers[key] = "[REDACTED]"
            else:
                x_headers[key] = clip_monitor_text(raw_value)
    except Exception:
        pass

    authorization_present = False
    try:
        authorization_present = bool(request.headers.get("authorization"))
    except Exception:
        pass

    user_header_values: Dict[str, str] = {}
    for key in (
        "x-user-id",
        "x-openwebui-user-id",
        "x-user-role",
        "x-openwebui-user-role",
        "x-user-permissions",
        "x-openwebui-user-permissions",
        "x-school-scope",
        "x-openwebui-school-scope",
    ):
        value = x_headers.get(key)
        if value:
            user_header_values[key] = value

    return {
        "incoming_x_header_keys": sorted(set(x_header_keys)),
        "incoming_user_header_values": user_header_values,
        "authorization_present": authorization_present,
    }


def build_upstream_error_text(err: Exception) -> str:
    error_class_name = type(err).__name__
    if error_class_name == "APIStatusError":
        detail = str(err)
        if getattr(err, "status_code", None) == 402 or "Insufficient Balance" in detail:
            return "当前模型服务余额不足（402），暂时无法生成回答。请充值后重试。"
        code = getattr(err, "status_code", None)
        if code == 503 or "service_unavailable" in detail or "Service is too busy" in detail:
            return "当前上游模型服务繁忙（503），本次回答被中断。请稍后重试，或临时切换到 yili-qwen。"
        return f"上游模型服务异常（{code or 'unknown'}），请稍后重试。"
    if error_class_name == "AuthenticationError":
        return "上游模型鉴权失败，请检查 API Key 配置。"
    return "上游模型服务暂时不可用，请稍后重试。"
