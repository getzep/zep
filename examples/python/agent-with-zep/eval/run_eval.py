"""Run the gold questions against ablation configs and grade the answers.

Configs:
  A: naive tools, no orientation, no domain knowledge, no planning
  B: full tools + orientation
  C: B + domain knowledge
  D: C + planning

Usage: uv run python eval/run_eval.py [--configs A B] [--questions q01] \
         [--repeats 1] [--dry-run]

Writes results to eval/results-<timestamp>.jsonl and prints a summary table.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_ai import Agent

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_with_zep.agent import prepare_run, run_agent
from agent_with_zep.config import AS_OF_DATE, AgentConfig, Settings
from agent_with_zep.prompts import JUDGE_PROMPT

CONFIGS: dict[str, AgentConfig] = {
    "A": AgentConfig(tools="naive", orientation=False, domain_knowledge=False, planning=False),
    "B": AgentConfig(tools="full", orientation=True, domain_knowledge=False, planning=False),
    "C": AgentConfig(tools="full", orientation=True, domain_knowledge=True, planning=False),
    "D": AgentConfig(tools="full", orientation=True, domain_knowledge=True, planning=True),
}


class Grade(BaseModel):
    evidence_found: list[bool]
    answer_states: list[bool]
    must_not_violated: bool
    accuracy: Literal[0, 1, 2]
    plan_quality: Literal[0, 1, 2]
    rationale: str


def render_judge_prompt(question: dict, result) -> str:
    evidence = "\n\n".join(f"[{c['name']}] {c['result']}" for c in result.tool_calls)
    plan = "\n".join(p.model_dump_json() for p in result.plans)
    return JUDGE_PROMPT.format(
        as_of_date=AS_OF_DATE,
        question=question["question"],
        must_have="\n".join(f"- {f}" for f in question.get("must_have", [])),
        must_not="\n".join(f"- {f}" for f in question.get("must_not", [])),
        evidence=evidence or "(none)",
        answer=result.answer,
        plan=plan or "(none)",
    )


async def run_one(settings, question: dict, config_name: str, judge: Agent) -> dict:
    agent, deps, orientation = await prepare_run(settings, CONFIGS[config_name])
    result = await run_agent(agent, deps, question["question"], orientation=orientation)
    grade: Grade = (
        await judge.run(render_judge_prompt(question, result), output_type=Grade)
    ).output
    called = {c["name"] for c in result.tool_calls}
    tool_selection = int(bool(set(question.get("expected_tools", [])) & called))
    return {
        "question_id": question["id"],
        "config": config_name,
        "answer": result.answer,
        "plan": [p.model_dump() for p in result.plans],
        "tool_calls": result.tool_calls,
        "tool_call_count": len(result.tool_calls),
        "tool_selection": tool_selection,
        "latency_s": round(result.latency_s, 3),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "grade": grade.model_dump(),
        "context_completeness": (
            sum(grade.evidence_found) / len(grade.evidence_found) if grade.evidence_found else None
        ),
        "plan_quality": grade.plan_quality if result.plans else None,
    }


def summarize(records: list[dict]) -> list[dict]:
    rows = []
    for config_name in sorted({r["config"] for r in records}):
        rs = [r for r in records if r["config"] == config_name]

        def mean(key, rs=rs):
            vals = [r[key] for r in rs if r.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None

        rows.append(
            {
                "config": config_name,
                "n": len(rs),
                "context_completeness": mean("context_completeness"),
                "accuracy": mean("grade_accuracy"),
                "plan_quality": mean("plan_quality"),
                "tool_selection": mean("tool_selection"),
                "tool_calls": mean("tool_call_count"),
                "latency_s": mean("latency_s"),
                "input_tokens": mean("input_tokens"),
                "output_tokens": mean("output_tokens"),
            }
        )
    return rows


async def main_async(args) -> None:
    questions = yaml.safe_load((Path(__file__).parent / "gold_questions.yaml").read_text())
    if args.questions:
        questions = [q for q in questions if q["id"] in args.questions]
    config_names = args.configs or list(CONFIGS)
    total = len(questions) * len(config_names) * args.repeats
    if args.dry_run:
        print(
            f"dry run: {len(questions)} questions x {len(config_names)} configs "
            f"x {args.repeats} repeats = {total} runs"
        )
        return

    settings = Settings.from_env()
    judge = Agent(settings.judge_model, output_type=Grade)
    out_path = Path(__file__).parent / f"results-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    records: list[dict] = []
    with out_path.open("a") as out:
        for config_name in config_names:
            for question in questions:
                for rep in range(args.repeats):
                    record = await run_one(settings, question, config_name, judge)
                    record["repeat"] = rep
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    records.append(record)
                    print(
                        f"{config_name} {question['id']} rep{rep}: accuracy={record['grade']['accuracy']}"
                    )

    # flatten grade.accuracy for the summary
    for r in records:
        r["grade_accuracy"] = r["grade"]["accuracy"]
    rows = summarize(records)
    header = [
        "config",
        "n",
        "context_completeness",
        "accuracy",
        "plan_quality",
        "tool_selection",
        "tool_calls",
        "latency_s",
        "input_tokens",
        "output_tokens",
    ]
    print("\t".join(header))
    for row in rows:
        print("\t".join(str(row[h]) for h in header))
    print(f"results written to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", nargs="+", choices=list(CONFIGS))
    parser.add_argument("--questions", nargs="+")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
