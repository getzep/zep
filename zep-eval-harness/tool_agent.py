"""
Tool-Calling Agent Loop

Runs the response model as an agent that retrieves its own context from Zep
via tool calls before answering. The tools themselves are defined in
config/evaluation_config/tools.py — this module is only the machinery that
declares them to the model, executes what the model asks for, enforces the
call/iteration budgets, and records timings.

Everything the tools returned is kept in the result so the evaluation can grade
context completeness on the union of all tool outputs, while grading answer
accuracy on the final answer.
"""

import asyncio
import json
from dataclasses import dataclass, field
from time import time
from typing import Any, Awaitable, Callable

from openai import BadRequestError

from retry import retry_with_backoff

BUDGET_EXHAUSTED_MESSAGE = (
    "Tool call budget exhausted — this call was not executed. "
    "Answer the question now using the information you already retrieved."
)

# Said in the prompt as well as enforced with tool_choice="none": a provider that
# ignores or rejects that parameter still needs to know retrieval is over.
FINAL_ANSWER_INSTRUCTION = (
    "You have no tool calls left. Answer the question now, using only the "
    "information you already retrieved. Do not request any more tools."
)

# How many times to ask for the final answer when the model keeps requesting
# tools instead of answering.
FINAL_ANSWER_ATTEMPTS = 2

# Used when the model answers the first turn without retrieving even though
# tool_choice="required" asked it to — some providers don't enforce the parameter.
RETRIEVE_FIRST_INSTRUCTION = (
    "You have not retrieved anything yet, and you have no knowledge of this user "
    "beyond what your tools return. Call a retrieval tool now, before answering."
)

# outcome values on ToolCallRecord
OK = "ok"  # executor ran and succeeded
ERROR = "error"  # executor ran and raised
INVALID = "invalid"  # never dispatched: unknown tool or unparseable arguments
REFUSED = "refused"  # never dispatched: call budget was spent


# ============================================================================
# Tool definitions
# ============================================================================


@dataclass
class ToolContext:
    """Everything a tool executor may need, passed as its first argument."""

    zep_client: Any
    user_id: str
    doc_graph_id: str | None = None
    user_summary: str | None = None


@dataclass
class ToolSpec:
    """
    One tool exposed to the response model.

    Args:
        name: Tool name the model calls.
        description: What the tool does — the model relies on this to choose.
        parameters: JSON Schema for the tool's arguments. Leave empty for a
            no-argument tool; the schema is then omitted entirely, since some
            providers reject an object schema with no properties.
        executor: Async callable invoked as executor(ctx, **arguments), returning
            the text handed back to the model.
        enabled: Set False to keep a tool defined but out of the run.
        requires_doc_graph: Only register when the run includes a document graph.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    executor: Callable[..., Awaitable[str]]
    enabled: bool = True
    requires_doc_graph: bool = False

    def schema(self) -> dict[str, Any]:
        """Return the OpenAI-format tool schema for this spec."""
        function: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.parameters.get("properties"):
            function["parameters"] = self.parameters
        return {"type": "function", "function": function}

    def allowed_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Drop arguments the schema doesn't declare (models sometimes invent them)."""
        properties = self.parameters.get("properties") or {}
        return {k: v for k, v in arguments.items() if k in properties}


@dataclass
class ToolRegistry:
    """The set of tools active for a run, plus their schemas."""

    specs: dict[str, ToolSpec] = field(default_factory=dict)
    schemas: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def build(
        cls, specs: list[ToolSpec], doc_graph_id: str | None = None
    ) -> "ToolRegistry":
        """Build a registry from tool specs, skipping disabled/inapplicable ones."""
        active = [
            spec
            for spec in specs
            if spec.enabled and not (spec.requires_doc_graph and not doc_graph_id)
        ]
        return cls(
            specs={spec.name: spec for spec in active},
            schemas=[spec.schema() for spec in active],
        )

    def names(self) -> list[str]:
        return list(self.specs.keys())

    def __bool__(self) -> bool:
        return bool(self.specs)


# ============================================================================
# Loop results
# ============================================================================


@dataclass
class ToolCallRecord:
    """One tool call the model requested, executed or not."""

    iteration: int
    name: str
    arguments: Any
    output: str
    outcome: str = OK
    duration_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == OK

    @property
    def executed(self) -> bool:
        """Whether an executor actually ran (vs. rejected before dispatch)."""
        return self.outcome in (OK, ERROR)

    def summary(self) -> dict[str, Any]:
        """Metadata-only view for results.json (full output lives in the context)."""
        return {
            "iteration": self.iteration,
            "name": self.name,
            "arguments": self.arguments,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
            "executed": self.executed,
            "error": self.error,
            "output_chars": len(self.output),
        }


@dataclass
class AgentLoopResult:
    """Outcome of one agent run: the answer plus everything it retrieved."""

    answer: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0
    llm_turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    turn_prompt_tokens: list[int] = field(default_factory=list)
    tool_wall_ms: float = 0.0  # Wall-clock spent executing tools (parallel-aware)
    llm_ms: float = 0.0  # Wall-clock spent in LLM turns
    total_ms: float = 0.0
    hit_call_cap: bool = False
    hit_iteration_cap: bool = False
    # Turns where the provider rejected the requested tool_choice with a 400 and
    # the turn was retried with "auto". Only counted when that retry succeeded —
    # a 400 from any other cause fails the retry too and propagates — so it means
    # "dropping the constraint is what made the call work".
    tool_choice_downgrades: int = 0
    # require_tool_call was asked for but not applied: either the provider
    # rejected tool_choice="required", or it accepted it and answered without
    # calling a tool anyway.
    require_tool_call_unenforced: bool = False
    # True whenever the published answer did not come from a turn that answered
    # cleanly — i.e. the agent kept requesting tools, and the text being graded is
    # a preamble it produced alongside those calls. Published anyway, because a
    # model that answers and calls a tool in the same turn shouldn't lose its
    # answer, but flagged so the run's diagnostics don't imply a clean answer.
    forced_answer_failed: bool = False

    @property
    def tool_call_ms(self) -> float:
        """Sum of individual call durations (exceeds wall clock when parallel)."""
        return sum(call.duration_ms for call in self.calls)

    @property
    def executed_calls(self) -> list[ToolCallRecord]:
        return [call for call in self.calls if call.executed]

    @property
    def retrieved_calls(self) -> list[ToolCallRecord]:
        """Calls whose output is real retrieved context (not an error message)."""
        return [call for call in self.calls if call.ok]

    @property
    def answer_empty(self) -> bool:
        return not self.answer

    def count(self, outcome: str) -> int:
        """How many calls ended in the given outcome (OK / ERROR / INVALID / REFUSED)."""
        return sum(1 for call in self.calls if call.outcome == outcome)

    def call_counts(self) -> dict[str, int]:
        """Executed call count per tool name."""
        counts: dict[str, int] = {}
        for call in self.executed_calls:
            counts[call.name] = counts.get(call.name, 0) + 1
        return counts


# ============================================================================
# LLM turn helper
# ============================================================================


async def _chat(
    llm_client,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str,
    description: str,
) -> tuple[Any, bool]:
    """
    Run one chat completion turn.

    `tools` stays declared even when tool calls are forbidden (tool_choice=
    "none"), because a message history containing tool results is only valid
    alongside the tool declarations that produced it.

    A provider that doesn't support the requested tool_choice rejects it with a
    400, which retry_with_backoff raises immediately (it never retries a 4xx);
    the turn is then retried once with "auto". Transient failures (429, 5xx,
    timeouts) retry normally and never silently relax the constraint the run
    asked for. Not every 400 is a tool_choice problem — a too-long history or a
    malformed message lands here too — so the retry is best-effort and the
    original error propagates if it fails again.

    Returns:
        Tuple of (response, tool_choice_was_downgraded).
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "tools": tools,
        "tool_choice": tool_choice,
    }

    try:
        response = await retry_with_backoff(
            llm_client.chat.completions.create,
            description=description,
            **kwargs,
        )
        return response, False
    except BadRequestError as e:
        if tool_choice == "auto":
            raise
        print(f"  ⚠ 400 on a turn with tool_choice='{tool_choice}': {e}")
        print(f"    retrying once with tool_choice='auto'")
        kwargs["tool_choice"] = "auto"
        response = await retry_with_backoff(
            llm_client.chat.completions.create,
            description=f"{description} (tool_choice=auto)",
            **kwargs,
        )
        return response, True


def _call_id(tool_call, iteration: int, index: int) -> str:
    """
    The id used to pair a tool result with its call.

    Providers are supposed to send one, but the SDK parses responses leniently
    and will hand back None if it's missing — which would serialize to an
    assistant message with no id and a tool message with a null tool_call_id,
    an unpairable combination the API rejects. Synthesizing one keeps the pair
    consistent.
    """
    return getattr(tool_call, "id", None) or f"call_{iteration}_{index}"


def _tool_call_name(tool_call) -> str | None:
    """Function name of a tool call, or None if it isn't a function tool call."""
    function = getattr(tool_call, "function", None)
    return getattr(function, "name", None)


def _assistant_message(message, iteration: int) -> dict[str, Any]:
    """
    Convert an SDK response message into a request message dict.

    Tool calls are round-tripped whole so provider extras survive — Gemini 3
    returns a `thought_signature` on each tool call and rejects the next turn if
    it isn't echoed back — while response-only fields on the message itself
    (refusal, annotations, audio) are dropped. Empty content is omitted when
    there are tool calls, which some providers require.
    """
    payload: dict[str, Any] = {"role": "assistant"}
    if message.tool_calls:
        payload["tool_calls"] = [
            {**tc.model_dump(exclude_none=True), "id": _call_id(tc, iteration, i)}
            for i, tc in enumerate(message.tool_calls)
        ]
        if message.content:
            payload["content"] = message.content
    else:
        payload["content"] = message.content or ""
    return payload


# ============================================================================
# Tool execution
# ============================================================================


async def _execute_tool_call(
    tool_call, registry: ToolRegistry, tool_context: ToolContext, iteration: int
) -> ToolCallRecord:
    """Execute one requested tool call, converting any failure into model-readable text."""
    name = _tool_call_name(tool_call)
    if name is None:
        # Only function tools are declared, so anything else (e.g. a custom tool
        # call) is a provider quirk. Reported rather than raised: an exception
        # here would abort every remaining test case for the user.
        error = f"unsupported tool call type '{getattr(tool_call, 'type', 'unknown')}'"
        return ToolCallRecord(
            iteration=iteration,
            name=str(getattr(tool_call, "type", "unknown")),
            arguments=None,
            output=f"Tool call failed: {error}. Call one of: {', '.join(registry.names())}.",
            outcome=INVALID,
            error=error,
        )

    raw_arguments = tool_call.function.arguments or "{}"

    try:
        arguments = json.loads(raw_arguments) if raw_arguments.strip() else {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        error = f"could not parse arguments as JSON: {e}"
        return ToolCallRecord(
            iteration=iteration,
            name=name,
            arguments=raw_arguments,
            output=f"Tool call failed: {error}. Reissue the call with valid JSON arguments.",
            outcome=INVALID,
            error=error,
        )

    spec = registry.specs.get(name)
    if spec is None:
        error = f"unknown tool '{name}'"
        return ToolCallRecord(
            iteration=iteration,
            name=name,
            arguments=arguments,
            output=f"Tool call failed: {error}. Available tools: {', '.join(registry.names())}.",
            outcome=INVALID,
            error=error,
        )

    start = time()
    try:
        output = await spec.executor(tool_context, **spec.allowed_arguments(arguments))
        return ToolCallRecord(
            iteration=iteration,
            name=name,
            arguments=arguments,
            output=output,
            duration_ms=(time() - start) * 1000,
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        print(f"  ⚠ tool '{name}' failed: {error}")
        return ToolCallRecord(
            iteration=iteration,
            name=name,
            arguments=arguments,
            output=f"Tool '{name}' failed: {error}",
            outcome=ERROR,
            duration_ms=(time() - start) * 1000,
            error=error,
        )


# ============================================================================
# The loop
# ============================================================================


async def run_tool_agent(
    llm_client,
    model: str,
    system_prompt: str,
    question: str,
    registry: ToolRegistry,
    tool_context: ToolContext,
    max_iterations: int,
    max_tool_calls: int,
    require_tool_call: bool = True,
) -> AgentLoopResult:
    """
    Let the model retrieve context via tools, then answer.

    Each iteration is one LLM turn; every tool call the model requests in that
    turn runs in parallel and counts individually against `max_tool_calls`.
    Once either budget is spent, one final turn is made with tool calls
    forbidden so the model must answer from what it gathered.

    Args:
        llm_client: AsyncOpenAI client instance
        model: Model used for the agent's turns
        system_prompt: System prompt for the agent
        question: The question to answer
        registry: Tools available to the agent
        tool_context: Passed as the first argument to every tool executor
        max_iterations: Max LLM turns that may request tools
        max_tool_calls: Max individual tool calls across all iterations
        require_tool_call: Force a tool call on the first turn, so the model
            cannot answer before retrieving anything

    Returns:
        AgentLoopResult with the final answer, every tool call and its output,
        and the latency/token breakdown.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    result = AgentLoopResult()
    start = time()
    answer: str | None = None
    cut_off = False  # True when a budget stopped the agent before it answered
    retrieval_demanded = False  # Whether the first-turn tool call was re-demanded
    unanswered_text = ""  # Best text from a turn that didn't answer cleanly
    label = f"'{question[:60]}'"

    async def _turn(tool_choice: str, description: str):
        response, downgraded = await _chat(
            llm_client, model, messages, registry.schemas, tool_choice, description
        )
        result.llm_turns += 1
        result.tool_choice_downgrades += int(downgraded)
        # Attributed, not just counted: a downgrade on the first turn means the
        # run's require_tool_call was not applied, while one on the forced-answer
        # turn means the model was merely allowed to ask for tools again.
        if downgraded and tool_choice == "required":
            result.require_tool_call_unenforced = True
        usage = getattr(response, "usage", None)
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
            result.prompt_tokens += prompt_tokens
            result.turn_prompt_tokens.append(prompt_tokens)
            result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
        return response.choices[0].message if response.choices else None

    # Counted rather than a range(), because the retrieval nudge below gives the
    # round back: it exists to compensate for a provider ignoring tool_choice, and
    # shouldn't come out of the retrieval budget the run asked for.
    rounds_used = 0
    while rounds_used < max_iterations:
        budget_left = max_tool_calls - len(result.calls)
        if budget_left <= 0:
            cut_off = True
            break

        first_round = rounds_used == 0 and not retrieval_demanded
        rounds_used += 1
        iteration = rounds_used

        llm_start = time()
        message = await _turn(
            "required" if (first_round and require_tool_call) else "auto",
            f"agent turn {iteration} for {label}",
        )
        result.llm_ms += (time() - llm_start) * 1000

        tool_calls = list(getattr(message, "tool_calls", None) or []) if message else []
        text = (getattr(message, "content", "") or "").strip()

        if not tool_calls:
            # `message is None` when the provider returned no choices at all:
            # there is no assistant turn to replay, and nothing to argue with, so
            # that falls through to the forced-answer turn instead of nudging.
            if message is not None and first_round and require_tool_call:
                # tool_choice="required" was asked for and the provider answered
                # anyway (it may not support the parameter). Say it in the prompt
                # and give the model another turn rather than accepting an answer
                # the run asked to be grounded in retrieval. Once only, so a model
                # that simply refuses can still finish, and the round is refunded
                # so the nudge doesn't cost the agent a retrieval opportunity.
                print(f"  ⚠ no tool call on the first turn despite tool_choice=required for {label}")
                retrieval_demanded = True
                result.require_tool_call_unenforced = True
                rounds_used -= 1
                messages.append(_assistant_message(message, iteration))
                messages.append({"role": "user", "content": RETRIEVE_FIRST_INSTRUCTION})
                unanswered_text = unanswered_text or text
                continue
            # Empty content is not an answer — fall through to the final turn.
            answer = text or None
            break

        messages.append(_assistant_message(message, iteration))
        result.iterations = iteration
        # Text alongside tool calls is a preamble, not an answer, but it is the
        # best fallback available if the forced-answer turns all come back empty.
        unanswered_text = unanswered_text or text

        # Calls beyond the remaining budget are refused, but each still needs a
        # tool response message so the conversation stays valid.
        allowed, refused = tool_calls[:budget_left], tool_calls[budget_left:]

        tools_start = time()
        records = list(
            await asyncio.gather(
                *[
                    _execute_tool_call(tc, registry, tool_context, iteration)
                    for tc in allowed
                ]
            )
        )
        result.tool_wall_ms += (time() - tools_start) * 1000

        for tc in refused:
            records.append(
                ToolCallRecord(
                    iteration=iteration,
                    name=_tool_call_name(tc) or "unknown",
                    arguments=getattr(getattr(tc, "function", None), "arguments", None),
                    output=BUDGET_EXHAUSTED_MESSAGE,
                    outcome=REFUSED,
                    error="tool call budget exhausted",
                )
            )

        for i, (tc, record) in enumerate(zip(allowed + refused, records)):
            result.calls.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": _call_id(tc, iteration, i),
                    "content": record.output,
                }
            )

        if refused:
            cut_off = True
            break
    else:
        # Every iteration ended in tool calls — the model never got to answer.
        cut_off = True

    if cut_off:
        # Measured against rounds actually offered to the model, not
        # result.iterations (which counts only rounds that requested tools): a
        # round the model spent answering still consumed the budget.
        result.hit_call_cap = len(result.calls) >= max_tool_calls
        result.hit_iteration_cap = rounds_used >= max_iterations

    if answer is None:
        llm_start = time()

        # A provider that rejected tool_choice="none" (and was retried with
        # "auto") can come back with tool calls — on their own, or alongside a
        # "let me search for that..." preamble. Either way the turn didn't
        # answer, so tool calls take precedence over any text, exactly as they do
        # in the loop above: every requested call is refused on the record and the
        # model is asked again. Bounded, because a provider that ignores the
        # constraint once will often ignore it twice.
        # Seeded with any text from a turn that requested tools: it isn't an
        # answer, but if every forced attempt comes back empty it beats publishing
        # nothing — and the flag below records that no turn answered cleanly.
        preamble = unanswered_text
        for attempt in range(1, FINAL_ANSWER_ATTEMPTS + 1):
            # Skipped when the previous attempt produced nothing to respond to, so
            # the history doesn't accumulate the same instruction twice in a row.
            if messages[-1].get("content") != FINAL_ANSWER_INSTRUCTION:
                messages.append({"role": "user", "content": FINAL_ANSWER_INSTRUCTION})
            message = await _turn(
                "none",
                f"final answer for {label}"
                if attempt == 1
                else f"final answer attempt {attempt} for {label}",
            )

            if message is None:
                # No choices at all. There is nothing to accept and nothing to
                # refuse, so spend a remaining attempt rather than publishing an
                # empty answer — the same rule the retrieval loop applies.
                print(f"  ⚠ no response on forced-answer attempt {attempt} for {label}")
                continue

            text = (getattr(message, "content", "") or "").strip()
            ignored = list(getattr(message, "tool_calls", None) or [])

            if not ignored:
                answer = text
                if not answer and preamble:
                    # This turn answered cleanly but said nothing, so the only text
                    # the agent ever produced came from a turn that was still
                    # asking for tools. Publishing it beats discarding a genuine
                    # answer from a model that answered and called a tool at once,
                    # but it is not a clean answer and is flagged as such.
                    answer = preamble
                    result.forced_answer_failed = True
                break

            preamble = preamble or text
            round_number = result.iterations + attempt
            messages.append(_assistant_message(message, round_number))
            for i, tc in enumerate(ignored):
                record = ToolCallRecord(
                    iteration=round_number,
                    name=_tool_call_name(tc) or "unknown",
                    arguments=getattr(getattr(tc, "function", None), "arguments", None),
                    output=BUDGET_EXHAUSTED_MESSAGE,
                    outcome=REFUSED,
                    error="requested after the budget was spent",
                )
                result.calls.append(record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": _call_id(tc, round_number, i),
                        "content": record.output,
                    }
                )
        else:
            # No attempt answered cleanly — the model kept requesting tools, or the
            # provider never returned a message. Flagged rather than blanked: any
            # text is kept so a model that answers *and* calls a tool doesn't lose
            # a real answer, and the flag records that no clean answer was
            # produced (with answer_empty covering the case where there was none).
            print(
                f"  ⚠ no clean answer after {FINAL_ANSWER_ATTEMPTS} "
                f"forced-answer attempts for {label}"
            )
            result.forced_answer_failed = True
            answer = preamble

        result.llm_ms += (time() - llm_start) * 1000

    result.answer = answer
    result.total_ms = (time() - start) * 1000

    if result.answer_empty:
        print(f"  ⚠ agent produced no answer for {label}")

    return result
