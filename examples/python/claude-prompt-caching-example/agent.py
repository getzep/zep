"""A memory-backed chat agent that injects Zep context one of two ways.

Modes (`--context-mode`):

- ``system-prompt`` — the baseline: context in the system prompt, with
  best-effort caching. The static prefix (persona + tools) is its own
  system block with a cache breakpoint, so it is read from cache after
  turn 1. But the Zep context block is appended as a second system block
  that changes every turn, and because caching hashes the request prefix
  in order (tools → system → messages), everything after that changing
  block — the entire conversation history — can never be cached in this
  mode. This is the best a developer can do without moving the context
  out of the system prompt.

- ``system-message`` — the pattern made possible by Claude Opus 4.8's
  mid-conversation system messages, with full prompt caching. The static
  system prompt never changes; each turn's Zep context block is appended as
  a ``{"role": "system"}`` message immediately after the latest user
  message, at the very end of the request — *after* the moving cache
  breakpoint, so it is never cached. On the next turn the prior context
  message is simply not re-sent (replaced by a fresh one), which is
  cache-safe precisely because it was never part of any cache entry.
  Everything before it — the static prefix and the entire user/assistant
  history — stays byte-identical and is read from cache every turn. This is
  the trailing "context message" pattern Zep has long recommended for
  cache efficiency, now with proper system-level authority instead of a
  faked user or tool turn.

The agent itself is identical in both modes — same memory retrieval, same
model parameters, same history. The difference is where the context block
is placed, which determines how much of the prompt caching can protect.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from zep_cloud.client import Zep
from zep_cloud.types import Message

import pricing
from scenario import STATIC_SYSTEM_PROMPT, TOOL_DEFINITIONS

MODE_SYSTEM_PROMPT = "system-prompt"  # context in system prompt (prefix-only caching)
MODE_SYSTEM_MESSAGE = "system-message"  # mid-conversation system messages (full caching)
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
            thread_id=self.thread_id,
            messages=[Message(role="user", name=self.user_name, content=text)],
            return_context=True,
        )
        return response.context

    def on_assistant_message(self, text: str) -> None:
        self.zep.thread.add_messages(
            thread_id=self.thread_id,
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

        This method is the entire difference between the two modes.

        In ``system-prompt`` mode there is one cache breakpoint, on the
        static system block — the most a developer can usefully cache with
        this placement. A breakpoint in the messages would never match,
        because the changing context block upstream invalidates everything
        after it.

        In ``system-message`` mode there are two explicit breakpoints: one
        at the end of the (fully static) system field and one on the latest
        *user* message — never on the trailing context message, which is
        the one part of the prompt that is new every turn. Because the
        context message sits *after* the last breakpoint, it is never
        cached, and dropping it next turn invalidates nothing. Moving the
        message breakpoint forward each turn is the standard
        incremental-conversation pattern: the previous breakpoint's cache
        entry still matches the (unchanged) earlier prefix, so it is read,
        and only the tokens after it (last turn's reply + the new user
        message) are written as the new entry.
        """
        if self.mode == MODE_SYSTEM_PROMPT:
            # Baseline: static prefix cached (own block + breakpoint), but
            # the context block rides in a second system block that changes
            # every turn — so the conversation history after it is re-read
            # at full price on every single turn.
            system = [self._block(self._static_system, cached=True)]
            if context:
                system.append(self._block(_CONTEXT_HEADER + context))
            user_message = {"role": "user", "content": [self._block(user_text)]}
            return system, [*self.history, user_message]

        # Opus 4.8: the system field is static, and the context block rides
        # in a mid-conversation system message placed immediately after the
        # user turn — the one spot that adds operator-level context without
        # touching anything earlier in the prompt. It sits after the moving
        # breakpoint, so it is never cached and is replaced wholesale by the
        # next turn's fresh block.
        system = [self._block(self._static_system, cached=True)]
        user_message = {"role": "user", "content": [self._block(user_text, cached=True)]}
        messages = [*self.history, user_message]
        if context:
            messages.append({"role": "system", "content": [self._block(_CONTEXT_HEADER + context)]})
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
        # (system-prompt mode) or in the trailing, never-cached system
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
        episodes = zep.graph.episode.get_by_user_id(user_id=user_id, lastn=100).episodes or []
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
