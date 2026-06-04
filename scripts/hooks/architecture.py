from __future__ import annotations

from scripts.hooks.common import HookResult, format_location, run_command, staged_added_lines


STRUCTURED_TOOL_ALLOWED_PREFIXES = (
    "gateway_core/tools/adapters/",
)


def run() -> HookResult:
    result = HookResult("Architecture Hook")
    size_check = run_command(["python3", "scripts/check_file_size.py", "--staged"])
    if size_check.returncode != 0:
        result.errors.append((size_check.stdout + size_check.stderr).strip() or "file size check failed")

    for path, line_number, text in staged_added_lines():
        if path.startswith("scripts/hooks/"):
            continue
        if (
            path.endswith(".py")
            and "StructuredTool.from_function" in text
            and not path.startswith(STRUCTURED_TOOL_ALLOWED_PREFIXES)
        ):
            result.errors.append(
                f"{format_location(path, line_number)}: 新增 LangChain 工具必须走 GatewayToolRegistry adapter。"
            )
        if path.endswith(".py") and ("policy_only" in text or "PolicyOnly" in text):
            result.errors.append(f"{format_location(path, line_number)}: Policy Only 已降级删除，不允许重新引入。")
    return result
