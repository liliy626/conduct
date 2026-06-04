from __future__ import annotations

from scripts.hooks.common import HookResult, run_command, staged_python_files


def run() -> HookResult:
    result = HookResult("Quality Hook")
    diff_check = run_command(["git", "diff", "--cached", "--check"])
    if diff_check.returncode != 0:
        result.errors.append((diff_check.stdout + diff_check.stderr).strip() or "git diff --check failed")

    python_files = staged_python_files()
    if python_files:
        compile_result = run_command(["python3", "-m", "py_compile", *python_files])
        if compile_result.returncode != 0:
            result.errors.append((compile_result.stdout + compile_result.stderr).strip() or "python compile failed")
    return result
