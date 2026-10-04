"""Build and run the reference agent.

`build_agent` composes the system prompt, the toolset, and (optionally) the
planning gate from an AgentConfig. `run_agent` sends the graph sample and the
question as the first user turn and returns the answer, the plan, the tool
call log, and token usage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.toolsets import PreparedToolset
from zep_cloud import AsyncZep

from .config import AS_OF_DATE, DATA_DIR, AgentConfig
from .handles import HandleRegistry
from .orientation import load_orientation
from .planner import make_prepare
from .prompts import ROLE, render_graph_data, render_system_prompt
from .tools import AgentDeps, build_toolset


@dataclass
class RunResult:
    answer: str
    plans: list
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int
    latency_s: float


def _read_domain_knowledge(path: Path | None = None) -> str:
    path = path or (DATA_DIR / "domain_knowledge.md")
    return path.read_text()


def build_agent(config: AgentConfig, model: Model | str, deps: AgentDeps) -> Agent:
    """Create the Pydantic AI agent for a configuration.

    - tools="naive" exposes only search_context; "full" exposes all six tools.
    - orientation controls the ontology section of the system prompt.
    - domain_knowledge controls the domain knowledge section.
    - planning gates the retrieval tools behind submit_plan (max 2 plans).
    """
    prompt = render_system_prompt(
        role=ROLE,
        as_of_date=AS_OF_DATE,
        domain_knowledge=_read_domain_knowledge(),
        orientation=config.orientation,
        planning=config.planning,
        include_domain_knowledge=config.domain_knowledge,
    )
    toolset = build_toolset()
    if config.tools == "naive":
        toolset = toolset.filtered(
            lambda ctx, tool_def: tool_def.name in {"search_context", "submit_plan"}
        )
    prepared = PreparedToolset(wrapped=toolset, prepare_func=make_prepare(config.planning))
    return Agent(
        model,
        instructions=prompt,
        deps_type=AgentDeps,
        toolsets=[prepared],
    )


async def run_agent(
    agent: Agent,
    deps: AgentDeps,
    question: str,
    *,
    orientation: dict | None = None,
) -> RunResult:
    """Run one question and collect the answer, plan, tool calls, and usage."""
    sample_lines = ""
    if orientation:
        for node in orientation.get("nodes", []):
            handle = deps.registry.register(node["uuid"], "n")
            deps.registry.mark_seen(handle)
            sample_lines += f"- {handle} {node['name']} [{','.join(node['labels'])}]\n"
    prompt_parts: list = []
    if sample_lines:
        prompt_parts.append(render_graph_data(sample_lines.rstrip()))
    prompt_parts.append(question)

    start = time.monotonic()
    result = await agent.run(prompt_parts, deps=deps)
    latency = time.monotonic() - start
    usage = result.usage
    if callable(usage):
        usage = usage()
    return RunResult(
        answer=str(result.output),
        plans=list(deps.plans),
        tool_calls=list(deps.call_log),
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        latency_s=latency,
    )


async def prepare_run(
    settings,
    config: AgentConfig,
    *,
    model: Model | str | None = None,
    zep: AsyncZep | None = None,
) -> tuple[Agent, AgentDeps, dict | None]:
    """Build the agent, deps, and orientation for one run."""
    zep = zep or AsyncZep(api_key=settings.zep_api_key)
    deps = AgentDeps(zep=zep, graph_id=settings.graph_id)
    orientation = await load_orientation(zep, settings.graph_id) if config.orientation else None
    agent = build_agent(config, model or settings.agent_model, deps)
    return agent, deps, orientation


def new_deps(zep: AsyncZep, graph_id: str) -> AgentDeps:
    return AgentDeps(zep=zep, graph_id=graph_id, registry=HandleRegistry())
