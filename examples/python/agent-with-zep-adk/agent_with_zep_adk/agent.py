"""Build and run the Google ADK reference agent."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from google.adk.agents import LlmAgent, RunConfig
from google.adk.events import Event
from google.adk.models import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from zep_cloud import AsyncZep

from .config import AS_OF_DATE, DATA_DIR, AgentConfig, Settings
from .handles import HandleRegistry
from .models import resolve_model
from .orientation import load_orientation
from .planner import make_before_model_callback, make_before_tool_callback, make_submit_plan
from .prompts import ROLE, render_graph_data, render_system_prompt
from .tools import AgentDeps, build_tools


@dataclass
class RunResult:
    answer: str
    plans: list
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    latency_s: float


def _read_domain_knowledge(path: Path | None = None) -> str:
    return (path or (DATA_DIR / "domain_knowledge.md")).read_text()


def build_agent(
    config: AgentConfig,
    deps: AgentDeps,
    *,
    model: str | BaseLlm,
    generate_content_config: types.GenerateContentConfig | None = None,
) -> LlmAgent:
    """Build an ADK agent with the same prompt and retrieval behavior."""
    instruction = render_system_prompt(
        role=ROLE,
        as_of_date=AS_OF_DATE,
        domain_knowledge=_read_domain_knowledge(),
        orientation=config.orientation,
        planning=config.planning,
        include_domain_knowledge=config.domain_knowledge,
    )
    tools = build_tools(deps) + [make_submit_plan(deps)]
    if config.tools == "naive":
        tools = [tool for tool in tools if tool.__name__ in {"search_context", "submit_plan"}]
    return LlmAgent(
        name="zep_analyst",
        model=model,
        instruction=instruction,
        tools=tools,
        generate_content_config=generate_content_config,
        before_model_callback=make_before_model_callback(deps, config.planning),
        before_tool_callback=make_before_tool_callback(deps, config.planning),
    )


def require_retrieval(deps: AgentDeps) -> str | None:
    """Return a retry notice when the agent answered without retrieval."""
    if not deps.call_log and deps.calls_left > 0:
        return "You have not called a retrieval tool. Run the retrieval steps and then answer from the evidence."
    return None


def graph_sample_prompt(deps: AgentDeps, orientation: dict | None) -> str | None:
    """Register orientation nodes and render them as untrusted graph data."""
    if not orientation:
        return None
    lines = []
    for node in orientation.get("nodes", []):
        handle = deps.registry.register(node["uuid"], "n")
        deps.registry.mark_seen(handle)
        lines.append(f"- {handle} {node['name']} [{','.join(node['labels'])}]")
    if not lines:
        return None
    return render_graph_data("\n".join(lines))


async def run_events(
    runner: Runner,
    *,
    user_id: str,
    session_id: str,
    new_message: types.Content,
    deps: AgentDeps,
    run_config: RunConfig | None = None,
    max_retries: int = 2,
) -> AsyncIterator[Event]:
    """Yield events and retry in the same session when retrieval is missing."""
    message = new_message
    for attempt in range(max_retries + 1):
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=message,
            run_config=run_config,
        ):
            yield event
        notice = require_retrieval(deps)
        if notice is None or attempt >= max_retries:
            return
        message = types.Content(role="user", parts=[types.Part(text=notice)])


async def run_agent(
    agent: LlmAgent,
    deps: AgentDeps,
    question: str,
    *,
    orientation: dict | None = None,
) -> RunResult:
    """Run a question and collect the answer, plan, calls, and token usage."""
    service = InMemorySessionService()
    runner = Runner(app_name="agent_with_zep_adk", agent=agent, session_service=service)
    session = await service.create_session(app_name="agent_with_zep_adk", user_id="reference")
    parts = []
    sample = graph_sample_prompt(deps, orientation)
    if sample:
        parts.append(types.Part(text=sample))
    parts.append(types.Part(text=question))
    message = types.Content(role="user", parts=parts)

    start = time.monotonic()
    events = []
    async for event in run_events(
        runner,
        user_id=session.user_id,
        session_id=session.id,
        new_message=message,
        deps=deps,
    ):
        events.append(event)
    answer = ""
    for event in events:
        if event.is_final_response():
            answer = "".join(
                part.text
                for part in (event.content.parts if event.content else [])
                if part.text and not getattr(part, "thought", False)
            )
    input_tokens = 0
    output_tokens = 0
    for event in events:
        if event.partial or not event.usage_metadata:
            continue
        usage = event.usage_metadata
        input_tokens += usage.prompt_token_count or 0
        output_tokens += (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
    return RunResult(
        answer=answer,
        plans=list(deps.plans),
        tool_calls=list(deps.call_log),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_s=time.monotonic() - start,
    )


async def prepare_run(
    settings: Settings,
    config: AgentConfig,
    *,
    model: str | BaseLlm | None = None,
    zep: AsyncZep | None = None,
) -> tuple[LlmAgent, AgentDeps, dict | None]:
    """Build the ADK agent, its dependencies, and optional graph orientation."""
    zep = zep or AsyncZep(
        api_key=settings.zep_api_key,
        **({"base_url": settings.zep_base_url} if settings.zep_base_url else {}),
    )
    deps = AgentDeps(zep=zep, graph_id=settings.graph_id, registry=HandleRegistry())
    orientation = await load_orientation(zep, settings.graph_id) if config.orientation else None
    resolved_model, generate_content_config = resolve_model(
        model or settings.agent_model, settings.model_thinking
    )
    agent = build_agent(
        config,
        deps,
        model=resolved_model,
        generate_content_config=generate_content_config,
    )
    return agent, deps, orientation
