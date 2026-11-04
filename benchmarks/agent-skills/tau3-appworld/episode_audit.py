from __future__ import annotations

from copy import deepcopy
from typing import Any


def reasoning_parts(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    parts = []
    for item in items:
        if item.get("type") == "reasoning":
            for field, kind in (("summary", "summary"), ("content", "reasoning_content")):
                content = item.get(field) or []
                if isinstance(content, str):
                    content = [{"text": content}]
                for part in content:
                    text = part.get("text") if isinstance(part, dict) else None
                    if text:
                        parts.append({"kind": kind, "text": str(text)})
        elif item.get("type") == "anthropic_content":
            for block in item.get("content") or []:
                if block.get("type") == "thinking" and block.get("thinking"):
                    parts.append({"kind": "thinking", "text": block["thinking"]})
        if item.get("reasoning_content"):
            parts.append({"kind": "reasoning_content", "text": item["reasoning_content"]})
    return parts


class EpisodeAudit:
    """Records model output separately from evaluator-visible messages.

    Turn and tool-call indices are one-based. The initial scripted greeting is not a turn.
    """

    def __init__(self) -> None:
        self.assistant_turns: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.request_count = 0

    def start_turn(self) -> int:
        index = len(self.assistant_turns) + 1
        self.assistant_turns.append({"assistant_turn_index": index, "requests": []})
        return index

    def start_request(self, turn: int, summary_mode: str) -> dict[str, Any]:
        self.request_count += 1
        request = {
            "provider_request_index": self.request_count,
            "summary_mode": summary_mode,
            "reasoning_status": "unavailable" if summary_mode == "unsupported" else "not_returned",
            "reasoning": [],
        }
        self.assistant_turns[turn - 1]["requests"].append(request)
        return request

    def record_response(
        self, turn: int, request: dict[str, Any], items: list[dict[str, Any]]
    ) -> None:
        request["reasoning"] = reasoning_parts(items)
        if request["reasoning"]:
            request["reasoning_status"] = "returned"
        for item in items:
            if item.get("type") == "function_call":
                self.tool_calls.append(
                    {
                        "assistant_turn_index": turn,
                        "provider_request_index": request["provider_request_index"],
                        "call_id": str(item.get("call_id") or item.get("id") or ""),
                        "tool": str(item.get("name") or ""),
                        "requestor": "assistant",
                    }
                )
                if item.get("name") == "read_skill":
                    self.tool_calls[-1]["skill_read"] = {
                        "name": None,
                        "found": None,
                        "repaired": False,
                    }

    def record_skill_read(self, call_id: str, name: str, details: dict[str, Any]) -> None:
        call = next(call for call in reversed(self.tool_calls) if call["call_id"] == call_id)
        call["skill_read"] = {"name": name, "found": None, "repaired": False, **details}

    def export(self, messages: list[Any] | None = None) -> dict[str, Any]:
        calls = deepcopy(self.tool_calls)
        if messages is not None:
            calls = []
            turn = 0
            for message in messages:
                if (
                    getattr(message, "role", "") == "assistant"
                    and getattr(message, "usage", None) is not None
                ):
                    turn += 1
                    calls.extend(
                        deepcopy(call)
                        for call in self.tool_calls
                        if call["assistant_turn_index"] == turn
                    )
                for call in getattr(message, "tool_calls", None) or []:
                    if getattr(call, "requestor", "") == "user":
                        calls.append(
                            {
                                "assistant_turn_index": None,
                                "call_id": call.id,
                                "tool": call.name,
                                "requestor": "user",
                            }
                        )
            calls.extend(
                deepcopy(call) for call in self.tool_calls if call["assistant_turn_index"] > turn
            )
        for index, call in enumerate(calls, 1):
            call["tool_call_index"] = index
        reads = []
        for index, call in enumerate(calls):
            if "skill_read" not in call:
                continue
            reads.append(
                {
                    **call["skill_read"],
                    "assistant_turn_index": call["assistant_turn_index"],
                    "provider_request_index": call["provider_request_index"],
                    "tool_call_index": call["tool_call_index"],
                    "tool_calls_before": [item["tool_call_index"] for item in calls[:index]],
                    "tool_calls_after": [item["tool_call_index"] for item in calls[index + 1 :]],
                    "previous_tool_call": calls[index - 1] if index else None,
                    "next_tool_call": calls[index + 1] if index + 1 < len(calls) else None,
                }
            )
        return {
            "assistant_turns": deepcopy(self.assistant_turns),
            "tool_call_sequence": calls,
            "skill_read_positions": reads,
        }
