"""Typed Zep SDK operations for Agent Skills and live Trace recording."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from zep_cloud import (
    AgentMemorySettings,
    AgentTraceCloseVerifier,
    AgentTraceEventContext,
    Zep,
)

FAMILY = "banking.synthetic-monthly-correction"
QUERY = "Investigate savings interest, apply qualifying product boosts, credit shortfalls and save reports"


class LedgerTransport(httpx.BaseTransport):
    def __init__(self, ledger: Path, transport: httpx.BaseTransport | None = None) -> None:
        self.ledger = ledger
        self.transport = transport or httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        record: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "status": None,
        }
        try:
            response = self.transport.handle_request(request)
            record["status"] = response.status_code
            return response
        finally:
            with self.ledger.open("a") as file:
                file.write(json.dumps(record) + "\n")

    def close(self) -> None:
        self.transport.close()


def readable_skills(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in response.get("items", [])
        if item.get("uuid")
        and isinstance(item.get("version"), int)
        and str(item.get("markdown") or "").strip()
    ]


class ZepSkills:
    def __init__(
        self,
        ledger: Path,
        framework: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        api_key = os.environ["ZEP_API_KEY"]
        base = os.getenv("ZEP_BASE_URL")
        if base is not None:
            if urlsplit(base).scheme != "https":
                raise ValueError("ZEP_BASE_URL must use HTTPS.")
            base = base.rstrip("/").removesuffix("/api/v4") + "/api/v4"
        self.ledger = ledger
        self.framework = framework
        self.http_client = httpx.Client(
            transport=LedgerTransport(ledger, transport),
            timeout=120,
            follow_redirects=False,
        )
        self.client = Zep(
            api_key=api_key,
            base_url=base,
            httpx_client=self.http_client,
            timeout=120,
            max_retries=0,
        )

    def close(self) -> None:
        self.http_client.close()

    def create_agent(self) -> dict[str, Any]:
        agent = self.client.agent.create(
            agent_id=f"frameworks-banking-{self.framework}-{uuid.uuid4().hex}",
            name="Synthetic monthly interest correction demo",
            security_domain="simple-banking-demo",
            memory_settings=AgentMemorySettings(
                compilation_mode="on_close",
                approval="auto",
                auto_approval_task_families=[FAMILY],
            ),
            idempotency_key=str(uuid.uuid4()),
        )
        return agent.model_dump(mode="json", by_alias=True, exclude_none=True)

    def search_skills(self, agent: str) -> tuple[str, list[dict[str, Any]]]:
        results = self.client.agent.skill.search(
            agent,
            page_size=20,
            query=QUERY,
            task_family=FAMILY,
            include_markdown=True,
            markdown_format="agent",
        )
        search_id = results.response.search_id
        if not search_id:
            raise RuntimeError("Zep did not return a Skill search UUID.")
        items = [
            skill.model_dump(mode="json", by_alias=True, exclude_none=True) for skill in results
        ]
        return search_id, readable_skills({"items": items})

    def wait_for_skill(self, agent: str, seconds: int = 900) -> dict[str, Any]:
        deadline = time.monotonic() + seconds
        last_pin: list[tuple[str, int, str]] | None = None
        stable = 0
        progress = self.ledger.parent / "learning-progress.jsonl"
        while True:
            learning = self.client.agent.learning.get(agent, task_family=FAMILY).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            runs_page = self.client.agent.learning.list_runs(agent, task_family=FAMILY)
            runs = runs_page.response.model_dump(mode="json", by_alias=True, exclude_none=True)
            with progress.open("a") as file:
                file.write(json.dumps({"learning": learning, "runs": runs}) + "\n")
            outcomes = runs.get("items", [])
            if outcomes and not learning.get("compilation_pending", True):
                latest = outcomes[0]
                if latest["decision"] != "produced":
                    raise RuntimeError(
                        f"The compiler did not produce a Skill: {latest['decision']}; "
                        f"reason: {latest.get('rejection_class', latest.get('reason_text', 'unknown'))}."
                    )
                _, skills = self.search_skills(agent)
                pin = sorted((item["uuid"], item["version"], item["markdown"]) for item in skills)
                if pin and pin == last_pin:
                    stable += 1
                else:
                    last_pin, stable = pin, 1
                if stable >= 3:
                    return {"learning": learning, "runs": runs, "skills": skills}
            if time.monotonic() >= deadline:
                raise TimeoutError("No complete admitted Skill is searchable.")
            time.sleep(5)

    def record_skill_use(
        self,
        agent: str,
        skill_uuid: str,
        search_id: str,
        trace_uuid: str,
        used: bool,
        passed: bool,
    ) -> dict[str, Any]:
        use = self.client.agent.skill.use.create(
            agent,
            skill_uuid,
            search_id=search_id,
            trace_uuid=trace_uuid,
            usage="used" if used else "ignored",
            idempotency_key=str(uuid.uuid4()),
        )
        use_uuid = use.model_dump(mode="json", by_alias=True, exclude_none=True).get("uuid")
        if not use_uuid:
            raise RuntimeError("Zep did not return a Skill use UUID.")
        outcome = self.client.agent.skill.use.add_outcome(
            agent,
            skill_uuid,
            use_uuid,
            outcome="succeeded" if passed else "failed",
            idempotency_key=str(uuid.uuid4()),
        )
        return {
            "uuid": use_uuid,
            "outcome": outcome.model_dump(mode="json", by_alias=True, exclude_none=True),
        }


class TraceRecorder:
    def __init__(self, zep: ZepSkills, agent: str) -> None:
        self.zep = zep
        self.agent = agent
        self.trace_uuid: str | None = None
        self.event_count = 0

    def start(self) -> str:
        if self.trace_uuid is not None:
            raise RuntimeError("This Trace recorder has already started.")
        trace = self.zep.client.agent.trace.create(
            self.agent,
            objective=QUERY,
            task_family=FAMILY,
            metadata={
                "source": "agent-skills-frameworks",
                "framework": self.zep.framework,
                "data": "invented",
            },
            idempotency_key=str(uuid.uuid4()),
        )
        self.trace_uuid = trace.model_dump(mode="json", by_alias=True, exclude_none=True).get(
            "uuid"
        )
        if not self.trace_uuid:
            raise RuntimeError("Zep did not return a Trace UUID.")
        return self.trace_uuid

    def append(
        self,
        event_type: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        if self.trace_uuid is None:
            raise RuntimeError("Start the Trace before appending events.")
        context = (
            AgentTraceEventContext.model_validate({"tools": [{"name": tool_name}]})
            if tool_name is not None
            else None
        )
        self.zep.client.agent.trace.append_event(
            self.agent,
            self.trace_uuid,
            event_type=event_type,
            content=content,
            tool_call_id=tool_call_id,
            context=context,
            idempotency_key=str(uuid.uuid4()),
        )
        self.event_count += 1

    def close(self, passed: bool) -> dict[str, Any]:
        if self.trace_uuid is None:
            raise RuntimeError("Start the Trace before closing it.")
        outcome = "succeeded" if passed else "failed"
        close = self.zep.client.agent.trace.close(
            self.agent,
            self.trace_uuid,
            outcome=outcome,
            verification="external",
            verifier=AgentTraceCloseVerifier(
                verifier_id="frameworks-banking-state",
                class_="harness",
                outcome=outcome,
                evidence_reference=self.trace_uuid,
            ),
            idempotency_key=str(uuid.uuid4()),
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        task_uuid = close.get("task", {}).get("uuid")
        if not task_uuid:
            raise RuntimeError("Zep did not return a Trace close Task UUID.")
        deadline = time.monotonic() + 900
        while True:
            task = self.zep.client.task.get(task_uuid).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            if task.get("status") == "succeeded":
                break
            if task.get("status") not in {"pending", "processing"}:
                raise RuntimeError(f"Trace close Task ended in {task.get('status')}.")
            if time.monotonic() >= deadline:
                raise TimeoutError("The Trace close Task is still running.")
            time.sleep(5)
        final_trace = self.zep.client.agent.trace.get(self.agent, self.trace_uuid).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        if final_trace.get("status") != "closed" or final_trace.get("verification") != "external":
            raise RuntimeError("Zep did not close the externally verified Trace.")
        return {
            "trace_uuid": self.trace_uuid,
            "close": close,
            "task": task,
            "final_trace": final_trace,
            "event_count": self.event_count,
        }
