from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HookResult:
    name: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def repo_root() -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True, stdout=subprocess.PIPE, text=True)
    return Path(result.stdout.strip())


def staged_files() -> list[str]:
    return _git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])


def staged_python_files() -> list[str]:
    return [path for path in staged_files() if path.endswith(".py") and Path(path).exists()]


def staged_added_lines() -> list[tuple[str, int | None, str]]:
    out: list[tuple[str, int | None, str]] = []
    current_file = ""
    new_line: int | None = None
    for line in _git_text(["diff", "--cached", "--unified=0"]).splitlines():
        if line.startswith("+++ b/"):
            current_file = line.removeprefix("+++ b/")
            new_line = None
            continue
        if line.startswith("@@ "):
            new_line = _parse_new_line_number(line)
            continue
        if not current_file or line.startswith(("---", "diff --git", "index ")):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.append((current_file, new_line, line[1:]))
            if new_line is not None:
                new_line += 1
        elif line and not line.startswith("-") and new_line is not None:
            new_line += 1
    return out


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def format_location(path: str, line_number: int | None) -> str:
    if line_number is None:
        return path
    return f"{path}:{line_number}"


def _git_lines(args: list[str]) -> list[str]:
    return [line.strip() for line in _git_text(args).splitlines() if line.strip()]


def _git_text(args: list[str]) -> str:
    result = subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, text=True)
    return result.stdout


def _parse_new_line_number(hunk_header: str) -> int | None:
    try:
        marker = hunk_header.split(" +", 1)[1].split(" ", 1)[0]
        number = marker.split(",", 1)[0]
        return int(number)
    except (IndexError, ValueError):
        return None
