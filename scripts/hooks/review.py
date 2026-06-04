from __future__ import annotations

from scripts.hooks.common import HookResult, staged_files


CORE_PREFIXES = ("gateway_core/", "app.py")
DOC_PREFIXES = (".qoder/", "README.md", "scripts/README.md")
TEST_PREFIXES = ("tests/",)


def run() -> HookResult:
    result = HookResult("Review Hook")
    files = staged_files()
    touches_core = any(path == "app.py" or path.startswith(CORE_PREFIXES) for path in files)
    touches_tests = any(path.startswith(TEST_PREFIXES) for path in files)
    touches_docs = any(path.startswith(DOC_PREFIXES) or path in DOC_PREFIXES for path in files)

    if touches_core and not touches_tests:
        result.warnings.append("本次提交修改了核心代码，但没有 staged 测试文件；请确认已有测试覆盖。")
    if touches_core and not touches_docs:
        result.warnings.append("本次提交修改了核心代码，但没有 staged 文档；架构/契约变化建议同步 .qoder 文档。")
    return result
