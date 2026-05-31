from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Iterable, Iterator

from gateway_core.agents.jobs.models import AgentJobEvent
from gateway_core.agents.jobs.service import AgentJobService


def iter_openai_stream_events(lines: Iterable[str]) -> Iterator[tuple[str, dict]]:
    in_thinking = False
    reasoning_thinking = False
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            if in_thinking:
                yield "thinking_done", {}
                in_thinking = False
            yield "upstream_done", {}
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        sources = payload.get("sources") or payload.get("citations")
        if sources:
            yield "sources", {"sources": sources}
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content:
            if not in_thinking:
                yield "thinking_start", {}
                in_thinking = True
                reasoning_thinking = True
            yield "thinking_delta", {"delta": str(reasoning_content)}
            continue
        content = delta.get("content")
        if content:
            if in_thinking and reasoning_thinking:
                yield "thinking_done", {}
                in_thinking = False
                reasoning_thinking = False
            for event in _split_thinking_delta(str(content), in_thinking):
                event_type, payload, in_thinking = event
                yield event_type, payload


def _split_thinking_delta(text: str, in_thinking: bool) -> Iterator[tuple[str, dict, bool]]:
    remaining = text
    thinking = in_thinking
    while remaining:
        if thinking:
            end_idx = remaining.find("</think>")
            if end_idx == -1:
                if remaining:
                    yield "thinking_delta", {"delta": remaining}, True
                return
            inside = remaining[:end_idx]
            if inside:
                yield "thinking_delta", {"delta": inside}, True
            yield "thinking_done", {}, False
            remaining = remaining[end_idx + len("</think>") :]
            thinking = False
            continue
        start_idx = remaining.find("<think>")
        if start_idx == -1:
            if remaining:
                yield "answer_delta", {"delta": remaining}, False
            return
        before = remaining[:start_idx]
        if before:
            yield "answer_delta", {"delta": before}, False
        yield "thinking_start", {}, True
        remaining = remaining[start_idx + len("<think>") :]
        thinking = True


class AgentJobWorker:
    def __init__(self, *, service: AgentJobService, gateway_base_url: str) -> None:
        self.service = service
        self.gateway_base_url = gateway_base_url.rstrip("/")

    async def run_job(self, *, job_id: str, authorization_token: str, school_scope: str = "") -> None:
        raw_job = self.service.store.get_job(job_id)
        if not raw_job:
            return
        self.service.store.update_job_status(job_id, "running", started_at=time.time())
        await self.service.emit(job_id, "job_started", {})
        chunks: list[str] = []
        try:
            for event_type, payload in self._call_gateway_stream(
                raw_job["request_payload"],
                authorization_token=authorization_token,
                school_scope=school_scope,
            ):
                if event_type == "answer_delta":
                    chunks.append(payload.get("delta", ""))
                await self.service.emit(job_id, event_type, payload)
            result_text = "".join(chunks)
            self.service.store.update_job_status(
                job_id,
                "succeeded",
                finished_at=time.time(),
                result_text=result_text,
            )
            await self.service.emit(job_id, "job_succeeded", {"text_length": len(result_text)})
        except Exception as exc:
            message = str(exc)
            self.service.store.update_job_status(job_id, "failed", finished_at=time.time(), error=message)
            await self.service.emit(job_id, "job_failed", {"error": message})

    def _call_gateway_stream(
        self,
        payload: dict,
        *,
        authorization_token: str,
        school_scope: str = "",
    ) -> Iterator[tuple[str, dict]]:
        request_payload = dict(payload)
        request_payload["stream"] = True
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.gateway_base_url}/v1/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {authorization_token}",
                "X-School-Scope": school_scope,
                "X-Agent-Stream-Process": "1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                decoded_lines = (line.decode("utf-8", errors="ignore") for line in resp)
                yield from iter_openai_stream_events(decoded_lines)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"gateway stream failed: HTTP {exc.code} {detail}") from exc
