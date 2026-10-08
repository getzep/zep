"""Run the gold questions against ablation configs and grade the answers.

Configs:
  A: naive tools, no orientation, no domain knowledge, no planning
  B: full tools + orientation
  C: B + domain knowledge
  D: C + planning

Usage: uv run python eval/run_eval.py [--configs A B] [--questions q01] \\
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
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_with_zep_adk.agent import prepare_run, run_agent
from agent_with_zep_adk.config import AS_OF_DATE, AgentConfig, Settings
from agent_with_zep_adk.models import resolve_model
from agent_with_zep_adk.prompts import JUDGE_PROMPT

GOLD_QUESTIONS = (
    Path(__file__).resolve().parent.parent.parent
    / "agent-with-zep"
    / "eval"
    / "gold_questions.yaml"
)

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


def validate_grade(grade: Grade, n_must_have: int) -> None:
    """Raise a value error when a grade has the wrong shape or a stub rationale."""
    if len(grade.evidence_found) != n_must_have:
        raise ValueError(
            f"evidence_found must have exactly {n_must_have} entries, one per must_have fact; got {len(grade.evidence_found)}."
        )
    if len(grade.answer_states) != n_must_have:
        raise ValueError(
            f"answer_states must have exactly {n_must_have} entries, one per must_have fact; got {len(grade.answer_states)}."
        )
    rationale = grade.rationale.strip()
    if not rationale or rationale.lower() == "placeholder":
        raise ValueError("rationale must be a non-empty explanation, not empty or 'placeholder'.")


def render_judge_prompt(question: dict, result) -> str:
    evidence = "\n\n".join(f"[{call['name']}] {call['result']}" for call in result.tool_calls)
    plan = "\n".join(item.model_dump_json() for item in result.plans)
    return JUDGE_PROMPT.format(
        as_of_date=AS_OF_DATE,
        question=question["question"],
        must_have="\n".join(f"- {fact}" for fact in question.get("must_have", [])),
        must_not="\n".join(f"- {fact}" for fact in question.get("must_not", [])),
        evidence=evidence or "(none)",
        answer=result.answer,
        plan=plan or "(none)",
    )


def _error_record(
    question: dict,
    config_name: str,
    result,
    *,
    run_error: str | None = None,
    grade_error: str | None = None,
) -> dict:
    """Build the record for a run that failed before or during grading."""
    tool_calls = getattr(result, "tool_calls", None) or []
    plans = getattr(result, "plans", None) or []
    return {
        "question_id": question["id"],
        "config": config_name,
        "answer": getattr(result, "answer", None),
        "plan": [plan.model_dump() for plan in plans],
        "tool_calls": tool_calls,
        "tool_call_count": len(tool_calls),
        "tool_selection": int(
            bool(set(question.get("expected_tools", [])) & {call["name"] for call in tool_calls})
        ),
        "latency_s": round(getattr(result, "latency_s", 0.0), 3),
        "input_tokens": getattr(result, "input_tokens", 0),
        "output_tokens": getattr(result, "output_tokens", 0),
        "grade": None,
        "run_error": run_error,
        "grade_error": grade_error,
        "context_completeness": None,
        "plan_quality": None,
    }


def build_judge(settings: Settings) -> LlmAgent:
    """Build the judge agent with the requested output schema."""
    model, generate_content_config = resolve_model(settings.judge_model, settings.model_thinking)
    return LlmAgent(
        name="judge",
        model=model,
        instruction="",
        output_schema=Grade,
        generate_content_config=generate_content_config,
    )


async def grade_answer(judge: LlmAgent, prompt: str, n_must_have: int) -> Grade:
    """Run the judge and validate its JSON result, with at most three retries."""
    session_service = InMemorySessionService()
    runner = Runner(
        app_name="agent_with_zep_adk_judge", agent=judge, session_service=session_service
    )
    session = await session_service.create_session(
        app_name="agent_with_zep_adk_judge",
        user_id="evaluation",
    )
    message_text = prompt
    for attempt in range(4):
        final_text = ""
        async for event in runner.run_async(
            user_id=session.user_id,
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text=message_text)]),
        ):
            if not event.is_final_response() or not event.content:
                continue
            final_text = "".join(
                part.text
                for part in event.content.parts
                if part.text and not getattr(part, "thought", False)
            )
        try:
            grade = Grade.model_validate_json(final_text)
            validate_grade(grade, n_must_have)
            return grade
        except (ValueError, ValidationError) as exc:
            if attempt == 3:
                raise
            message_text = str(exc)
    raise RuntimeError("The judge did not return a grade.")


async def run_one(settings, question: dict, config_name: str, judge: LlmAgent) -> dict:
    agent, deps, orientation = await prepare_run(settings, CONFIGS[config_name])
    try:
        result = await run_agent(agent, deps, question["question"], orientation=orientation)
    except Exception as exc:  # noqa: BLE001 - record and continue the sweep
        return _error_record(question, config_name, None, run_error=str(exc))
    try:
        grade = await grade_answer(
            judge,
            render_judge_prompt(question, result),
            len(question.get("must_have", [])),
        )
    except Exception as exc:  # noqa: BLE001 - record any judge failure
        return _error_record(question, config_name, result, grade_error=str(exc))
    called = {call["name"] for call in result.tool_calls}
    return {
        "question_id": question["id"],
        "config": config_name,
        "answer": result.answer,
        "plan": [plan.model_dump() for plan in result.plans],
        "tool_calls": result.tool_calls,
        "tool_call_count": len(result.tool_calls),
        "tool_selection": int(bool(set(question.get("expected_tools", [])) & called)),
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
    for config_name in sorted({record["config"] for record in records}):
        config_records = [record for record in records if record["config"] == config_name]

        def mean(key, selected=config_records):
            values = [record[key] for record in selected if record.get(key) is not None]
            return round(sum(values) / len(values), 3) if values else None

        ok = [record for record in config_records if not record.get("run_error")]
        rows.append(
            {
                "config": config_name,
                "n": len(config_records),
                "context_completeness": mean("context_completeness"),
                "accuracy": mean("grade_accuracy"),
                "plan_quality": mean("plan_quality"),
                "tool_selection": mean("tool_selection", ok),
                "tool_calls": mean("tool_call_count", ok),
                "latency_s": mean("latency_s", ok),
                "input_tokens": mean("input_tokens", ok),
                "output_tokens": mean("output_tokens", ok),
                "run_errors": sum(1 for record in config_records if record.get("run_error")),
                "grade_errors": sum(1 for record in config_records if record.get("grade_error")),
            }
        )
    return rows


async def main_async(args) -> None:
    questions = yaml.safe_load(GOLD_QUESTIONS.read_text())
    if args.questions:
        questions = [question for question in questions if question["id"] in args.questions]
    config_names = args.configs or list(CONFIGS)
    total = len(questions) * len(config_names) * args.repeats
    if args.dry_run:
        print(
            f"dry run: {len(questions)} questions x {len(config_names)} configs "
            f"x {args.repeats} repeats = {total} runs"
        )
        return

    settings = Settings.from_env()
    judge = build_judge(settings)

    out_path = Path(__file__).parent / f"results-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    records: list[dict] = []
    with out_path.open("a") as out:
        for config_name in config_names:
            for question in questions:
                for repeat in range(args.repeats):
                    record = await run_one(settings, question, config_name, judge)
                    record["repeat"] = repeat
                    out.write(json.dumps(record) + "\n")
                    out.flush()
                    records.append(record)
                    print(
                        f"{config_name} {question['id']} rep{repeat}: accuracy="
                        f"{(record['grade'] or {}).get('accuracy', 'grade error')}"
                    )

    grade_errors = 0
    run_errors = 0
    for record in records:
        if record.get("grade") is not None:
            record["grade_accuracy"] = record["grade"]["accuracy"]
        else:
            record["grade_accuracy"] = None
            grade_errors += 1
        if record.get("run_error"):
            run_errors += 1
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
        "run_errors",
        "grade_errors",
    ]
    print("\t".join(header))
    for row in rows:
        print("\t".join(str(row[key]) for key in header))
    print(f"grade errors: {grade_errors}")
    print(f"run errors: {run_errors}")
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
