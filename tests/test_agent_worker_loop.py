import asyncio

from scripts.run_agent_worker import read_worker_messages_once


class TimeoutError(Exception):
    pass


TimeoutError.__module__ = "redis.exceptions"


class TimeoutRedisClient:
    async def xreadgroup(self, *_args, **_kwargs):
        raise TimeoutError("Timeout reading from yili-redis:6379")


def test_worker_queue_read_timeout_is_treated_as_empty_poll():
    result = asyncio.run(
        read_worker_messages_once(
            TimeoutRedisClient(),
            group="gateway_workers",
            consumer="test-worker",
            queue_stream="agent_jobs:queue",
            capacity=1,
        )
    )

    assert result == []
