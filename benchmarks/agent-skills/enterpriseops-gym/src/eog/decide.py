"""The Skill picker of the `skills_decided` arm: the OpenAI Decisions API selects one Skill of the search hits."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DECISIONS_MODEL = "gpt-6-luna"
DECISION_QUESTION = "skill"
DECISION_INSTRUCTIONS = (
    "The input is a task for an agent that works with enterprise tools. Each choice is a skill "
    "that the agent can read before it starts the task. Select the skill whose "
    "steps complete this task."
)
RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6


def decisions_endpoint() -> str:
    """The Decisions URL: `OPENAI_BASE_URL` (the same base as the OpenAI chat route) plus `/decisions`."""
    base = os.environ.get("OPENAI_BASE_URL", "").strip() or DEFAULT_BASE_URL
    return base.rstrip("/") + "/decisions"


class DecisionError(RuntimeError):
    """The Decisions API gave no usable answer."""


class SkillDecider:
    """Pick one Skill of the candidates with the OpenAI Decisions API.

    A call returns the chosen candidate value and the probability of each candidate. The caller gives
    each candidate as a `(value, description)` pair; the value identifies the Skill and the description
    is the Skill frontmatter description.
    """

    def __init__(
        self,
        *,
        model: str = DECISIONS_MODEL,
        endpoint: str | None = None,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint or decisions_endpoint()
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise SystemExit("Set OPENAI_API_KEY to create the decided Skill selection.")
        self.client = client or httpx.Client(timeout=120.0)
        self.calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def body(self, task_text: str, candidates: list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "model": self.model,
            "input": task_text,
            "questions": [
                {
                    "name": DECISION_QUESTION,
                    "type": "choice",
                    "instructions": DECISION_INSTRUCTIONS,
                    "choices": [{"value": value, "description": description} for value, description in candidates],
                }
            ],
        }

    def decide(self, task_text: str, candidates: list[tuple[str, str]]) -> tuple[str | None, dict[str, float]]:
        """The chosen candidate value and the probability of each candidate; the value is None on a refusal."""
        if not candidates:
            raise DecisionError("The decided Skill selection has no candidates.")
        values = [value for value, _ in candidates]
        if len(set(values)) != len(values):
            raise DecisionError("The decided Skill selection has a repeated candidate value.")
        payload = self._post(self.body(task_text, candidates))
        return parse_decision(payload, values)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        delay = 2.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.post(self.endpoint, json=body, headers=headers)
            except httpx.HTTPError as error:
                if attempt == MAX_ATTEMPTS:
                    raise DecisionError(f"Decisions API transport error: {error}") from error
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            self.calls += 1
            if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            if response.status_code != 200:
                raise DecisionError(f"Decisions API HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            usage = payload.get("usage") or {}
            for key in self.usage:
                self.usage[key] += int(usage.get(key) or 0)
            return payload
        raise DecisionError("Decisions API gave no response.")


def parse_decision(payload: dict[str, Any], values: list[str]) -> tuple[str | None, dict[str, float]]:
    """The chosen value and the probability of each value from one Decisions API response.

    An answer of type `refusal` (the model selects no candidate) gives the value None and no probabilities.
    """
    answers = payload.get("answers")
    if not isinstance(answers, list) or not answers:
        raise DecisionError("Decisions API answer has no answers list.")
    answer = next((item for item in answers if item.get("name") == DECISION_QUESTION), answers[0])
    if answer.get("type") == "refusal":
        return None, {}
    probabilities: dict[str, float] = {}
    for item in answer.get("probabilities") or []:
        value = item.get("value")
        probability = item.get("probability")
        if value in values and probability is not None:
            probabilities[str(value)] = float(probability)
    choice = answer.get("choice")
    if choice is None and probabilities:
        choice = max(probabilities, key=probabilities.get)
    if choice not in values:
        raise DecisionError(
            f"Decisions API choice {choice!r} is not one of the candidates; answer: {json.dumps(answer)[:300]}"
        )
    return str(choice), probabilities
