"""Command-line entry point for the Google ADK reference agent.

Usage: uv run python -m agent_with_zep_adk.cli "Which products are cleared in the EU?"
"""

from __future__ import annotations

import argparse
import asyncio
import json

from .agent import prepare_run, run_agent
from .config import AgentConfig, Settings


async def _run(question: str, config: AgentConfig) -> None:
    settings = Settings.from_env()
    agent, deps, orientation = await prepare_run(settings, config)
    result = await run_agent(agent, deps, question, orientation=orientation)

    if result.plans:
        print("== plan ==")
        for i, plan in enumerate(result.plans, 1):
            print(f"plan {i}: {plan.model_dump_json(indent=2)}")
    if result.tool_calls:
        print("== tool calls ==")
        for call in result.tool_calls:
            print(f"[{call['ms']} ms] {call['name']}({json.dumps(call['args'])})")
            print(f"    -> {call['result'][:200]}")
    print("== answer ==")
    print(result.answer)
    print(
        f"({result.latency_s:.1f}s, {result.input_tokens} input tokens, "
        f"{result.output_tokens} output tokens)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", help="the question to ask the agent")
    parser.add_argument("--tools", choices=["naive", "full"], default="full")
    parser.add_argument("--no-orientation", action="store_true")
    parser.add_argument("--no-domain-knowledge", action="store_true")
    parser.add_argument("--no-planning", action="store_true")
    args = parser.parse_args()
    config = AgentConfig(
        tools=args.tools,
        orientation=not args.no_orientation,
        domain_knowledge=not args.no_domain_knowledge,
        planning=not args.no_planning,
    )
    asyncio.run(_run(args.question, config))


if __name__ == "__main__":
    main()
