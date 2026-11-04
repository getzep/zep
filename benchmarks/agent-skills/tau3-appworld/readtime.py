"""Read-time curation arms for the held-out student stage.

The curator reads up to three source Trajectories and writes one briefing for
the current task. The executor receives only the briefing. Nothing that this
module creates is stored in Zep.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from tracing import traced_responses_create
from zep_capture import SkillToolkit

MAX_SOURCES = 3
MAX_ITEM_CHARS = 1_500
MAX_TRANSCRIPT_CHARS = 12_000
TOKEN = re.compile(r"[^\W_]+", flags=re.UNICODE)

CURATOR_INSTRUCTIONS = """You prepare a short briefing for a customer-service agent.
You receive the first message of a new {domain} request and records of earlier
successful runs on similar requests. {skill_note}

Write a briefing with these sections:
1. Similar cases: which earlier cases match this request and how.
2. Resolution plan: the ordered steps that worked, with the lookup tools, the
   user confirmations, the policy conditions, and the state-changing tool call.
3. Check before: the preconditions to verify before each state-changing call.
4. Avoid: the errors or detours to avoid.
5. Guidance for this request.

Rules:
- Do not copy identifiers, names, dates, amounts, or other values from the
  earlier runs. Refer to them as parameters, such as <reservation_id>.
- Do not recommend an action that the domain policy does not permit. The agent
  must follow its policy when the briefing and the policy disagree.
- If no earlier case is relevant, write "No relevant earlier case." and stop.
- Keep the briefing under 400 words."""


def tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN.findall(text)]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


def format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    names: dict[str, str] = {}
    for message in messages:
        role = message.get("role")
        if role == "user":
            for call in message.get("tool_calls") or []:
                names[str(call.get("id"))] = f"user device {call.get('name')}"
                lines.append(f"User runs {call.get('name')} on the user device")
            if message.get("content"):
                lines.append(f"User: {_clip(str(message['content']), MAX_ITEM_CHARS)}")
        elif role == "assistant":
            for call in message.get("tool_calls") or []:
                names[str(call.get("id"))] = str(call.get("name"))
                arguments = json.dumps(call.get("arguments") or {}, sort_keys=True)
                lines.append(f"Agent calls {call.get('name')}({_clip(arguments, MAX_ITEM_CHARS)})")
            if message.get("content"):
                lines.append(f"Agent: {_clip(str(message['content']), MAX_ITEM_CHARS)}")
        elif role == "tool":
            name = names.get(str(message.get("id")), "tool")
            lines.append(
                f"Result of {name}: {_clip(str(message.get('content') or ''), MAX_ITEM_CHARS)}"
            )
    return _clip("\n".join(lines), MAX_TRANSCRIPT_CHARS)


def format_events(events: list[Any]) -> str:
    lines = []
    for event in sorted(events, key=lambda item: int(getattr(item, "sequence", 0) or 0)):
        content = str(getattr(event, "content", "") or "")
        if content:
            lines.append(f"[{getattr(event, 'event_type', '')}] {_clip(content, MAX_ITEM_CHARS)}")
    return _clip("\n".join(lines), MAX_TRANSCRIPT_CHARS)


def first_user_message(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user" and message.get("content"):
            return str(message["content"])
    return ""


class BM25:
    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = [Counter(tokens(document)) for document in documents]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.average = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        frequency: Counter[str] = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        count = len(self.docs)
        self.idf = {
            term: math.log(1 + (count - seen + 0.5) / (seen + 0.5))
            for term, seen in frequency.items()
        }
        self.k1 = k1
        self.b = b

    def top(self, query: str, limit: int) -> list[int]:
        terms = tokens(query)
        scores = []
        for index, doc in enumerate(self.docs):
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if tf:
                    norm = 1 - self.b + self.b * self.lengths[index] / (self.average or 1.0)
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + self.k1 * norm)
            scores.append((score, index))
        ranked = sorted(scores, key=lambda item: (-item[0], item[1]))
        return [index for score, index in ranked[:limit] if score > 0]


@dataclass
class RawCorpus:
    keys: list[str]
    queries: list[str]
    transcripts: list[str]
    index: BM25

    @classmethod
    def load(cls, results_root: Path, source_run_id: str, domain: str) -> RawCorpus:
        source = results_root / source_run_id
        keys, queries, transcripts = [], [], []
        rows: dict[tuple[int, int], dict[str, Any]] = {}
        for line in (source / "teacher.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row.get("domain") == domain:
                rows[(int(row["task_index"]), int(row["trial"]))] = row
        for (task_index, trial), row in sorted(rows.items()):
            if not row.get("succeeded") or row.get("error"):
                continue
            path = source / "simulations" / f"teacher-{domain}-{task_index}-{trial}.json"
            if not path.is_file():
                continue
            messages = json.loads(path.read_text()).get("messages") or []
            keys.append(f"{domain}:{task_index}:{trial}")
            queries.append(first_user_message(messages))
            transcripts.append(format_messages(messages))
        return cls(keys, queries, transcripts, BM25(queries))

    def retrieve(self, query: str, limit: int = MAX_SOURCES) -> list[tuple[str, str]]:
        return [
            (self.keys[index], self.transcripts[index]) for index in self.index.top(query, limit)
        ]


@dataclass
class ReadTimeCurator:
    arm: str
    domain: str
    openai_client: Any
    model: str
    reasoning_effort: str
    client: Any = None
    memory_agent_uuid: str | None = None
    task_family: str | None = None
    corpus: RawCorpus | None = None
    record: dict[str, Any] = field(default_factory=dict)

    def _skill_sources(self, query: str) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        toolkit = SkillToolkit(self.client, str(self.memory_agent_uuid), str(self.task_family))
        hits = toolkit.search_skills(query, limit=MAX_SOURCES)
        sources: list[tuple[str, str]] = []
        seen: set[str] = set()
        for hit in hits:
            skill_uuid = str(hit.get("uuid") or hit.get("uuid_") or "")
            if not skill_uuid:
                continue
            evidence = self.client.agent.skill.evidence.list(
                str(self.memory_agent_uuid), skill_uuid, page_size=20
            )
            for item in (evidence.items or [])[:20]:
                trajectory_uuid = str(getattr(item, "trace_uuid", "") or "")
                if not trajectory_uuid or trajectory_uuid in seen or len(sources) >= MAX_SOURCES:
                    continue
                seen.add(trajectory_uuid)
                events = list(
                    islice(
                        self.client.agent.trace.list_events(
                            str(self.memory_agent_uuid), trajectory_uuid, page_size=100
                        ),
                        100,
                    )
                )
                sources.append((trajectory_uuid, format_events(events)))
        return hits, sources

    def brief(self, query: str) -> str:
        started = time.monotonic()
        hits: list[dict[str, Any]] = []
        if self.arm == "r-skill":
            hits, sources = self._skill_sources(query)
        elif self.arm == "r-raw" and self.corpus is not None:
            sources = self.corpus.retrieve(query)
        else:
            raise ValueError(f"Unsupported read-time arm: {self.arm}")
        retrieval_s = time.monotonic() - started
        self.record = {
            "arm": self.arm,
            "source_keys": [key for key, _ in sources],
            "skill_uuids": [str(hit.get("uuid") or hit.get("uuid_") or "") for hit in hits],
            "retrieval_s": retrieval_s,
            "curator_input_tokens": 0,
            "curator_output_tokens": 0,
            "curator_s": 0.0,
            "briefing": "",
        }
        if not sources and not hits:
            return ""
        skill_note = (
            "You also receive the admitted Skills that indexed these runs."
            if hits
            else "You do not receive any Skill."
        )
        parts = [f"New request:\n{query}"]
        for hit in hits:
            parts.append(f"Admitted Skill:\n{_clip(str(hit.get('markdown') or ''), 6_000)}")
        for number, (_, transcript) in enumerate(sources, start=1):
            parts.append(f"Earlier successful run {number}:\n{transcript}")
        curator_started = time.monotonic()
        response = traced_responses_create(
            self.openai_client,
            "tau2-curator",
            {
                "model": self.model,
                "instructions": CURATOR_INSTRUCTIONS.format(
                    domain=self.domain, skill_note=skill_note
                ),
                "input": [{"role": "user", "content": "\n\n".join(parts)}],
                "store": False,
                "reasoning": {"effort": self.reasoning_effort},
            },
        )
        usage = getattr(response, "usage", None)
        briefing = str(getattr(response, "output_text", "") or "").strip()
        self.record.update(
            {
                "curator_input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "curator_output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "curator_s": time.monotonic() - curator_started,
                "briefing": briefing,
                "briefing_sha256": hashlib.sha256(briefing.encode()).hexdigest(),
            }
        )
        return briefing
