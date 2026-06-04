from __future__ import annotations

import re

from scripts.hooks.common import HookResult, format_location, staged_added_lines, staged_files


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|access[_-]?token|password)\b\s*[:=]\s*['\"]?[^'\"\s]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def run() -> HookResult:
    result = HookResult("Safety Hook")
    for path in staged_files():
        if path == ".env" or path.endswith("/.env"):
            result.errors.append(f"{path}: 不允许提交真实 .env 文件，请提交 .env.example。")
        if path.endswith((".pem", ".key", ".p12", ".pfx")):
            result.errors.append(f"{path}: 不允许提交私钥或证书密钥文件。")

    for path, line_number, text in staged_added_lines():
        if path.endswith(".env.example"):
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            result.errors.append(f"{format_location(path, line_number)}: 疑似密钥/令牌被加入提交。")
    return result
