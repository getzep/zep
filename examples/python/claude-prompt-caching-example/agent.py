"""A memory-backed chat agent with two prompt-caching strategies.

Retrieved context is user data in both modes. ``system-prompt`` appends it
to the current user turn. ``system-message`` adds it as a trailing user-data
message. The mode names describe the original benchmark, not the current
context role.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import Message

import pricing
from scenario import STATIC_SYSTEM_PROMPT, TOOL_DEFINITIONS

MODE_SYSTEM_PROMPT = "system-prompt"  # context in the current user turn
MODE_SYSTEM_MESSAGE = "system-message"  # context in a trailing user-data message
MODES = (MODE_SYSTEM_PROMPT, MODE_SYSTEM_MESSAGE)

_CONTEXT_HEADER = (
    "Long-term memory retrieved for the current turn (from the user's "
    "knowledge graph; facts may carry validity dates):\n\n"
)


@dataclass
class TurnMetrics:
    """Token usage, latency, and cost for one model call."""

    turn: int
    mode: str
    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int
    ttft_s: float
    total_s: float
    context_chars: int

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def cost(self) -> pricing.TurnCost:
        return pricing.cost_for_usage(
            self.input_tokens,
            self.cache_creation_input_tokens,
            self.cache_read_input_tokens,
            self.output_tokens,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "mode": self.mode,
            "input_tokens": self.input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "output_tokens": self.output_tokens,
            "ttft_s": round(self.ttft_s, 3),
            "total_s": round(self.total_s, 3),
            "context_chars": self.context_chars,
            "input_cost": round(self.cost.input_total, 6),
            "total_cost": round(self.cost.total, 6),
        }


class ZepMemory:
    """Live memory backend: persists messages to a Zep thread and retrieves
    the context block in the same call as the user-message write."""

    def __init__(self, zep: Zep, thread_id: str, user_name: str = "User"):
        self.zep = zep
        self.thread_id = thread_id
        self.user_name = user_name

    def on_user_message(self, text: str) -> str | None:
        """Persist the user message and return the freshest context block.

        ``return_context=True`` makes Zep run retrieval in the same request,
        so this is one round trip, not two.
        """
        response = self.zep.thread.add_messages(
            thread_uuid=self.thread_id,
            messages=[Message(role="user", name=self.user_name, content=text)],
        )
        if response.task and response.task.uuid_:
            deadline = time.monotonic() + 180
            while True:
                task = self.zep.task.get(response.task.uuid_)
                if task.status == "succeeded":
                    break
                if task.status in {"partial", "failed"}:
                    raise RuntimeError(f"Message task ended with {task.status}: {task.error}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for message processing")
                time.sleep(2)
        return self.zep.thread.get_context(self.thread_id).context

    def on_assistant_message(self, text: str) -> None:
        self.zep.thread.add_messages(
            thread_uuid=self.thread_id,
            messages=[Message(role="assistant", name="Assistant", content=text)],
        )


class ReplayMemory:
    """Benchmark backend: serves pre-captured context blocks in order and
    persists nothing. Lets two replay runs see byte-identical context."""

    def __init__(self, contexts: list[str | None]):
        self._contexts = list(contexts)
        self._i = 0

    def on_user_message(self, text: str) -> str | None:
        context = self._contexts[self._i]
        self._i += 1
        return context

    def on_assistant_message(self, text: str) -> None:
        pass


class ZepMemoryAgent:
    """Chat agent: Zep memory in, Claude Opus 4.8 out, with the context
    block placed according to ``mode``."""

    def __init__(
        self,
        anthropic_client: Any,
        memory: ZepMemory | ReplayMemory,
        mode: str,
        model: str = pricing.MODEL,
        max_tokens: int = 1024,
        system_salt: str | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.client = anthropic_client
        self.memory = memory
        self.mode = mode
        self.model = model
        self.max_tokens = max_tokens
        self.tools = TOOL_DEFINITIONS
        # The static prefix is assembled once and never mutated — byte
        # stability is what makes it cacheable. The optional salt gives each
        # benchmark run a unique prefix so runs can't hit each other's cache.
        self._static_system = STATIC_SYSTEM_PROMPT
        if system_salt:
            self._static_system += f"\n\n[session-id: {system_salt}]"
        self.history: list[dict[str, Any]] = []
        self.turn = 0
        self.last_context: str | None = None

    # -- request construction -------------------------------------------------

    @staticmethod
    def _block(text: str, cached: bool = False) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "text", "text": text}
        if cached:
            block["cache_control"] = {"type": "ephemeral"}
        return block

    def _build_request(self, user_text: str, context: str | None) -> tuple[list, list]:
        """Return the (system, messages) pair for this turn.

        Retrieved context is user data in both modes. The modes only control
        whether that data is part of the current user turn or a trailing user
        message.
        """
        if self.mode == MODE_SYSTEM_PROMPT:
            system = [self._block(self._static_system, cached=True)]
            text = f"{user_text}\n\n{_CONTEXT_HEADER}{context}" if context else user_text
            user_message = {"role": "user", "content": [self._block(text)]}
            return system, [*self.history, user_message]

        system = [self._block(self._static_system, cached=True)]
        user_message = {"role": "user", "content": [self._block(user_text, cached=True)]}
        messages = [*self.history, user_message]
        if context:
            messages.append({"role": "user", "content": [self._block(_CONTEXT_HEADER + context)]})
        return system, messages

    # -- one conversation turn -------------------------------------------------

    def send(self, user_text: str, scripted_reply: str | None = None) -> tuple[str, TurnMetrics]:
        """Run one turn: persist + retrieve memory, call the model, update
        history.

        If ``scripted_reply`` is given (benchmark mode), the model is still
        called and measured, but the scripted reply is what enters history —
        keeping the token stream identical across modes and runs.
        """
        self.turn += 1
        context = self.memory.on_user_message(user_text)
        self.last_context = context
        system, messages = self._build_request(user_text, context)

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if self.tools:
            # Tool schemas occupy the front of the prompt exactly as in
            # production (tools are hashed before system and messages), but
            # tool_choice "none" keeps every turn a plain text response so
            # runs stay comparable.
            request_kwargs["tools"] = self.tools
            request_kwargs["tool_choice"] = {"type": "none"}

        chunks: list[str] = []
        ttft: float | None = None
        t0 = time.perf_counter()
        with self.client.messages.stream(**request_kwargs) as stream:
            for text in stream.text_stream:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                chunks.append(text)
            final = stream.get_final_message()
        total = time.perf_counter() - t0

        usage = final.usage
        metrics = TurnMetrics(
            turn=self.turn,
            mode=self.mode,
            input_tokens=usage.input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            ttft_s=ttft if ttft is not None else total,
            total_s=total,
            context_chars=len(context or ""),
        )

        generated_reply = "".join(chunks)
        reply_for_history = scripted_reply if scripted_reply is not None else generated_reply

        # History holds only the user/assistant turns, stored without
        # cache_control markers — the breakpoint is added transiently at
        # request-build time and moves forward each turn. The context block
        # never enters history in either mode: it lives in the system field
        # (system-prompt mode) or in the trailing, never-cached user-data
        # message (system-message mode), and is replaced with a fresh block
        # on the next turn either way.
        self.history.append({"role": "user", "content": [self._block(user_text)]})
        self.history.append({"role": "assistant", "content": [self._block(reply_for_history)]})

        self.memory.on_assistant_message(reply_for_history)
        return generated_reply, metrics


# -- Zep helpers shared by chat.py and benchmark.py ----------------------------


def wait_for_zep_processing(
    zep: Zep,
    user_id: str,
    timeout_s: float = 300.0,
    poll_interval_s: float = 4.0,
    quiet: bool = False,
) -> bool:
    """Poll until every episode in the user's graph is processed.

    Zep ingestion is asynchronous: messages land in an episode first, then
    entities and facts are extracted in the background. Polling between
    turns mimics a real-paced conversation, where extraction keeps up and
    each turn's retrieval sees the facts from the turns before it.

    Returns True if everything processed before the timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        episodes = zep.graph.episode.list(user_id, page_size=100).items or []
        pending = sum(1 for e in episodes if not e.processed)
        if pending == 0:
            if not quiet:
                print(" " * 60, end="\r")
            return True
        if not quiet:
            print(f"  ... waiting on {pending} Zep episode(s)", end="\r", flush=True)
        time.sleep(poll_interval_s)
    if not quiet:
        print(f"\n  warning: Zep still processing after {timeout_s:.0f}s — continuing anyway")
    return False
