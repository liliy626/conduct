#!/usr/bin/env python3
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.hooks import architecture, quality, review, safety
from scripts.hooks.common import HookResult


HOOKS: tuple[Callable[[], HookResult], ...] = (
    safety.run,
    quality.run,
    architecture.run,
    review.run,
)


def main() -> int:
    results = [hook() for hook in HOOKS]
    for result in results:
        _print_result(result)
    if any(not result.ok for result in results):
        print("\npre-commit blocked: 请先处理 Safety / Quality / Architecture 失败项。", file=sys.stderr)
        return 1
    return 0


def _print_result(result: HookResult) -> None:
    if not result.errors and not result.warnings:
        return
    print(f"\n[{result.name}]", file=sys.stderr)
    for item in result.errors:
        print(f"ERROR: {item}", file=sys.stderr)
    for item in result.warnings:
        print(f"WARN: {item}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
