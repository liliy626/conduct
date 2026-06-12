from __future__ import annotations

import asyncio

from scripts.run_agent_worker import read_agent_job_messages
from scripts import run_combined_gateway
from scripts.run_sql_history_worker import read_sql_history_messages


RedisTimeoutError = type("TimeoutError", (Exception,), {"__module__": "redis.exceptions"})
RedisConnectionError = type("ConnectionError", (Exception,), {"__module__": "redis.exceptions"})


class TimeoutRedisClient:
    async def xreadgroup(self, *_args, **_kwargs):
        raise RedisTimeoutError("Timeout reading from yili-redis:6379")


class ConnectionErrorRedisClient:
    async def xreadgroup(self, *_args, **_kwargs):
        raise RedisConnectionError("Connection closed by server")


def test_agent_worker_read_timeout_returns_empty_batch() -> None:
    async def run() -> list[object]:
        return await read_agent_job_messages(
            TimeoutRedisClient(),
            group="gateway_workers",
            consumer="worker-1",
            queue_stream="agent_jobs:queue",
            capacity=20,
            block_ms=5000,
        )

    messages = asyncio.run(run())

    assert messages == []


def test_agent_worker_connection_error_returns_empty_batch() -> None:
    async def run() -> list[object]:
        return await read_agent_job_messages(
            ConnectionErrorRedisClient(),
            group="gateway_workers",
            consumer="worker-1",
            queue_stream="agent_jobs:queue",
            capacity=20,
            block_ms=5000,
        )

    messages = asyncio.run(run())

    assert messages == []


class StartedProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False
        self.wait_calls: list[float] = []

    def terminate(self) -> None:
        self.terminated = True

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None:
            self.wait_calls.append(timeout)
        return 0


def test_combined_gateway_cleans_started_children_when_spawn_fails(monkeypatch) -> None:
    started_process = StartedProcess()
    spawn_error = OSError("cannot spawn worker")
    calls = []

    def fake_popen(command):
        calls.append(command)
        if len(calls) == 1:
            return started_process
        raise spawn_error

    monkeypatch.setattr(run_combined_gateway.subprocess, "Popen", fake_popen)

    try:
        run_combined_gateway._start_processes()
    except OSError as exc:
        assert exc is spawn_error
    else:
        raise AssertionError("expected spawn failure")

    assert started_process.terminated is True
    assert started_process.killed is False
    assert started_process.wait_calls == [5]


class SqlHistoryRedisClient:
    def __init__(self) -> None:
        self.acked: list[tuple[str, str, str]] = []

    def xreadgroup(self, group: str, consumer: str, streams: dict[str, str], *, count: int, block: int):
        assert group == "sql_history_workers"
        assert consumer == "worker-1"
        assert streams == {"sql_history:write": ">"}
        assert count == 20
        assert block == 5000
        return [("sql_history:write", [("1-0", {"payload": "{}"}), ("2-0", {"payload": "{}"})])]


def test_sql_history_worker_reads_with_consumer_group() -> None:
    messages = read_sql_history_messages(
        SqlHistoryRedisClient(),
        stream="sql_history:write",
        group="sql_history_workers",
        consumer="worker-1",
        count=20,
        block_ms=5000,
    )

    assert messages == [("1-0", {"payload": "{}"}), ("2-0", {"payload": "{}"})]


class SqlHistoryTimeoutRedisClient:
    def xreadgroup(self, *_args, **_kwargs):
        raise RedisTimeoutError("Timeout reading from socket")


def test_sql_history_worker_read_timeout_returns_empty_batch() -> None:
    messages = read_sql_history_messages(
        SqlHistoryTimeoutRedisClient(),
        stream="sql_history:write",
        group="sql_history_workers",
        consumer="worker-1",
        count=20,
        block_ms=5000,
    )

    assert messages == []
