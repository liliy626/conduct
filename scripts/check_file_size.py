#!/usr/bin/env python3
"""Pre-commit file-size guard for modular, incremental gateway refactors."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MIN_LINES = 200
DEFAULT_MAX_LINES = 400
CHECKED_SUFFIXES = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
}
SKIPPED_PARTS = {
    ".git",
    ".pytest_cache",
    ".codegraph",
    "__pycache__",
    "artifacts",
    "logs",
}


@dataclass(frozen=True)
class FileSizeFinding:
    path: str
    lines: int
    base_lines: int | None
    severity: str
    message: str


def main() -> int:
    args = _parse_args()
    root = _repo_root()
    paths = _target_paths(args, root)
    findings = [
        finding
        for path in paths
        if (finding := _check_path(path, root=root, max_lines=args.max_lines, mode=args.mode)) is not None
    ]

    failures = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    if warnings:
        _print_group("File size warnings", warnings)
    if failures:
        _print_group("File size failures", failures)
        print(
            "\n建议：把职责拆到更小的模块，保持单文件约 "
            f"{args.min_lines}-{args.max_lines} 行；不要继续增大已有超大文件。",
            file=sys.stderr,
        )
        return 1
    if args.verbose:
        print(f"file size check passed: {len(paths)} file(s)")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check staged or explicit files against the project file-size policy.")
    parser.add_argument("files", nargs="*", help="Files to check. Defaults to tracked source files unless --staged is set.")
    parser.add_argument("--staged", action="store_true", help="Check files staged for commit.")
    parser.add_argument(
        "--mode",
        choices=("growth", "strict"),
        default=os.getenv("FILE_SIZE_CHECK_MODE", "growth"),
        help="growth blocks new growth past max; strict blocks any checked file past max.",
    )
    parser.add_argument(
        "--min-lines",
        type=int,
        default=_int_env("FILE_SIZE_MIN_LINES", DEFAULT_MIN_LINES),
        help="Preferred lower bound used in messages only.",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=_int_env("FILE_SIZE_MAX_LINES", DEFAULT_MAX_LINES),
        help="Maximum preferred line count.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print a success summary.")
    return parser.parse_args()


def _target_paths(args: argparse.Namespace, root: Path) -> list[Path]:
    if args.files:
        return _dedupe_paths([root / item for item in args.files])
    if args.staged:
        return _dedupe_paths([root / item for item in _git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMR"])])
    return _dedupe_paths([root / item for item in _git_lines(["ls-files"])])


def _check_path(path: Path, *, root: Path, max_lines: int, mode: str) -> FileSizeFinding | None:
    if not _should_check(path, root):
        return None
    rel = _relative(path, root)
    lines = _line_count(path)
    if lines <= max_lines:
        return None

    base_lines = _base_line_count(rel)
    if mode == "strict":
        return FileSizeFinding(rel, lines, base_lines, "error", f"{lines} lines exceeds {max_lines}")

    if base_lines is None:
        return FileSizeFinding(rel, lines, base_lines, "error", f"new file has {lines} lines, exceeds {max_lines}")
    if base_lines <= max_lines:
        return FileSizeFinding(rel, lines, base_lines, "error", f"grew past {max_lines}: {base_lines} -> {lines}")
    if lines > base_lines:
        return FileSizeFinding(rel, lines, base_lines, "error", f"oversized file grew: {base_lines} -> {lines}")
    return FileSizeFinding(rel, lines, base_lines, "warning", f"existing oversized file not grown: {base_lines} -> {lines}")


def _should_check(path: Path, root: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    if any(part in SKIPPED_PARTS for part in rel_parts):
        return False
    return path.suffix.lower() in CHECKED_SUFFIXES


def _base_line_count(rel_path: str) -> int | None:
    result = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    return len(result.stdout.splitlines())


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return sum(1 for _ in handle)


def _repo_root() -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], check=True, stdout=subprocess.PIPE, text=True)
    return Path(result.stdout.strip())


def _git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _print_group(title: str, findings: list[FileSizeFinding]) -> None:
    print(f"\n{title}:", file=sys.stderr)
    for finding in findings:
        base = "new" if finding.base_lines is None else str(finding.base_lines)
        print(f"- {finding.path}: {finding.message} (base={base})", file=sys.stderr)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
