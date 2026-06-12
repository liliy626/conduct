#!/usr/bin/env python3
from __future__ import annotations

import signal
import subprocess
import sys
import time


PROCESS_SPECS = (
    ("gateway", ("uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8008")),
    ("agent-worker", (sys.executable, "scripts/run_agent_worker.py")),
    ("sql-history-worker", (sys.executable, "scripts/run_sql_history_worker.py")),
)


def _start_processes() -> dict[str, subprocess.Popen[bytes]]:
    children: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, command in PROCESS_SPECS:
            print(f"starting {name}: {' '.join(command)}", flush=True)
            children[name] = subprocess.Popen(command)
    except Exception:
        _cleanup_started_children(children)
        raise
    return children


def _cleanup_started_children(children: dict[str, subprocess.Popen[bytes]]) -> None:
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    for child in children.values():
        if child.poll() is None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def _terminate_processes(children: dict[str, subprocess.Popen[bytes]]) -> None:
    for child in children.values():
        if child.poll() is None:
            child.terminate()
    deadline = time.time() + 10
    while time.time() < deadline:
        if all(child.poll() is not None for child in children.values()):
            return
        time.sleep(0.2)
    for child in children.values():
        if child.poll() is None:
            child.kill()


def main() -> int:
    children = _start_processes()
    stopping = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        print(f"received signal {signum}, stopping child processes", flush=True)
        _terminate_processes(children)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stopping:
        for name, child in children.items():
            return_code = child.poll()
            if return_code is not None:
                print(f"{name} exited with code {return_code}; stopping combined container", flush=True)
                _terminate_processes(children)
                return return_code or 1
        time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
