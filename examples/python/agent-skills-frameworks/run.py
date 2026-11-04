"""Run one teacher-to-student Agent Skills episode."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from banking import Case, expected_database, make_case, score
from langgraph_agent import EpisodeResult, run_episode
from zep_skills import FAMILY, TraceRecorder, ZepSkills


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def money_patterns(value: Decimal) -> list[str]:
    text = f"{value:.2f}"
    variants = {text, f"{value:,.2f}"}
    if text.endswith(".00"):
        variants |= {text[:-3], f"{value:,.2f}"[:-3]}
    elif text.endswith("0"):
        variants |= {text[:-1], f"{value:,.2f}"[:-1]}
    return [r"(?<![\d.])" + re.escape(item) + r"0*(?!\d|\.\d)" for item in variants]


def check_leakage(skills: list[dict[str, Any]], seeds: list[int]) -> dict[str, Any]:
    markdown = "\n".join(item["markdown"] for item in skills)
    hits = []
    for seed in seeds:
        case = make_case(seed)
        gold = expected_database(case)
        identifiers = [case.case_id] + [account.account_id for account in case.savings]
        identifiers += [account.account_id for account in case.checking]
        identifiers += [card.card_id for card in case.cards]
        for value in identifiers:
            if value in markdown:
                hits.append({"case_id": case.case_id, "value": value})
        amounts = [account.statement_balance for account in case.savings]
        amounts += [credit.amount for credit in gold.credits]
        for report in gold.reports:
            amounts += [report.expected_interest, report.net_interest_before_run]
        for amount in sorted(set(amounts)):
            if any(re.search(pattern, markdown) for pattern in money_patterns(amount)):
                hits.append({"case_id": case.case_id, "value": f"{amount:.2f}"})
    return {
        "exact_heldout_identifiers_balances_or_corrections": hits,
        "source_id_suffixes": sorted(
            set(re.findall(r"(?<![\w-])[ck][1-3](?!\w)", markdown, re.IGNORECASE))
        ),
        "note": "This conservative exact-match check is not a semantic leakage review.",
    }


def write_stage_start(
    path: Path,
    case: Case,
    model: str,
    effort: str,
    teaching: bool,
    skills: list[dict[str, Any]],
) -> None:
    write_json(
        path / "start.json",
        {
            "case": case.model_dump(mode="json"),
            "model": model,
            "effort": effort,
            "teaching": teaching,
            "skills": [
                {
                    "uuid": item["uuid"],
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                    "version": item["version"],
                }
                for item in skills
            ],
        },
    )


async def run_stage(
    path: Path,
    zep: ZepSkills,
    agent: str,
    case: Case,
    model: str,
    effort: str,
    skills: list[dict[str, Any]],
    teaching: bool,
) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=False)
    write_stage_start(path, case, model, effort, teaching, skills)
    recorder = TraceRecorder(zep, agent)
    started = time.monotonic()
    trace_closed = False
    try:
        trace_uuid = recorder.start()
        write_json(path / "trace-start.json", {"trace_uuid": trace_uuid, "event_count": 0})
        result: EpisodeResult = await run_episode(
            case,
            model,
            effort,
            skills,
            recorder,
            teaching,
        )
        evaluation = score(case, result.tools.database)
        elapsed = time.monotonic() - started
        row = {
            "case_id": case.case_id,
            "model": model,
            "effort": effort,
            "passed": evaluation["passed"],
            "tokens": result.tokens,
            "seconds": elapsed,
            "final_answer": result.final_answer,
        }
        write_json(path / "tool-calls.json", result.tools.calls)
        write_json(path / "database.json", result.tools.database.model_dump(mode="json"))
        write_json(path / "evaluation.json", evaluation)
        write_json(path / "result.json", row)
        trace_result = recorder.close(evaluation["passed"])
        trace_closed = True
        write_json(path / "trace.json", trace_result)
        print(
            f"{path.name}: {'PASS' if row['passed'] else 'FAIL'}; "
            f"{row['tokens']:,} tokens; {elapsed:.1f}s",
            flush=True,
        )
        return {**row, "trace": trace_result}
    except BaseException as error:
        if recorder.trace_uuid is not None and not trace_closed:
            try:
                trace_result = recorder.close(False)
                write_json(path / "trace.json", trace_result)
                trace_closed = True
            except BaseException as close_error:  # noqa: BLE001
                write_json(
                    path / "trace-close-failure.json",
                    {"type": type(close_error).__name__, "error": str(close_error)},
                )
        write_json(
            path / "failure.json",
            {
                "type": type(error).__name__,
                "error": str(error),
                "trace_uuid": recorder.trace_uuid,
                "event_count": recorder.event_count,
            },
        )
        raise


async def run_command(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "config.json",
        {
            "framework": args.framework,
            "teacher": args.teacher,
            "teacher_effort": args.teacher_effort,
            "student": args.student,
            "effort": args.effort,
            "teacher_seed": args.teacher_seed,
            "seed": args.seed,
            "compile_timeout": args.compile_timeout,
        },
    )
    zep = ZepSkills(output / "zep-calls.jsonl", args.framework)
    try:
        agent_data = zep.create_agent()
        agent = agent_data["uuid"]
        write_json(output / "agent.json", agent_data)
        teacher = await run_stage(
            output / "teacher",
            zep,
            agent,
            make_case(args.teacher_seed),
            args.teacher,
            args.teacher_effort,
            [],
            True,
        )
        if not teacher["passed"]:
            raise RuntimeError("The teacher did not pass the local banking score.")

        compiled = zep.wait_for_skill(agent, seconds=args.compile_timeout)
        write_json(output / "learning-result.json", compiled["runs"])
        skills = compiled["skills"]
        skill_dir = output / "skills"
        skill_dir.mkdir(exist_ok=False)
        catalog = []
        for index, skill in enumerate(skills, start=1):
            filename = f"skill-{index:02d}-{re.sub(r'[^A-Za-z0-9_-]', '_', skill['uuid'])}.md"
            (skill_dir / filename).write_text(skill["markdown"])
            catalog.append(
                {
                    "uuid": skill["uuid"],
                    "name": skill.get("name", ""),
                    "description": skill.get("description", ""),
                    "version": skill["version"],
                    "markdown_file": f"skills/{filename}",
                }
            )
        write_json(output / "skill-catalog.json", catalog)
        search_id, student_skills = zep.search_skills(agent)
        write_json(
            output / "student-search.json",
            {
                "search_id": search_id,
                "skills": [
                    {
                        "uuid": skill["uuid"],
                        "name": skill.get("name", ""),
                        "description": skill.get("description", ""),
                        "version": skill["version"],
                    }
                    for skill in student_skills
                ],
            },
        )
        leakage = check_leakage(student_skills, [args.seed])
        write_json(output / "leakage-check.json", leakage)
        if leakage["exact_heldout_identifiers_balances_or_corrections"]:
            raise RuntimeError("The Skill catalog contains held-out case data.")
        if leakage["source_id_suffixes"]:
            raise RuntimeError("The Skill contains case-specific checking or card ID suffixes.")

        student = await run_stage(
            output / "student",
            zep,
            agent,
            make_case(args.seed),
            args.student,
            args.effort,
            student_skills,
            False,
        )
        used_skills = {
            call["arguments"].get("skill_uuid")
            for call in json.loads((output / "student/tool-calls.json").read_text())
            if call["name"] == "read_skill"
        }
        uses = []
        for skill in student_skills:
            uses.append(
                {
                    "skill_uuid": skill["uuid"],
                    **zep.record_skill_use(
                        agent,
                        skill["uuid"],
                        search_id,
                        student["trace"]["trace_uuid"],
                        skill["uuid"] in used_skills,
                        student["passed"],
                    ),
                }
            )
        write_json(output / "skill-uses.json", uses)
        summary = {
            "framework": args.framework,
            "agent_uuid": agent,
            "family": FAMILY,
            "teacher": {
                "passed": teacher["passed"],
                "tokens": teacher["tokens"],
                "seconds": teacher["seconds"],
                "trace_uuid": teacher["trace"]["trace_uuid"],
                "event_count": teacher["trace"]["event_count"],
                "verification": teacher["trace"]["final_trace"]["verification"],
            },
            "learning_run_decision": compiled["runs"]["items"][0]["decision"],
            "skills": catalog,
            "student": {
                "passed": student["passed"],
                "tokens": student["tokens"],
                "seconds": student["seconds"],
                "trace_uuid": student["trace"]["trace_uuid"],
                "event_count": student["trace"]["event_count"],
                "verification": student["trace"]["final_trace"]["verification"],
            },
            "skill_uses": uses,
        }
        write_json(output / "summary.json", summary)
        print(
            f"Student: {'PASS' if student['passed'] else 'FAIL'}; "
            f"{student['tokens']:,} tokens; {student['seconds']:.1f}s",
            flush=True,
        )
        return 0 if student["passed"] else 1
    except BaseException as error:
        write_json(
            output / "failure.json",
            {"type": type(error).__name__, "error": str(error)},
        )
        raise
    finally:
        zep.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Agent Skills framework example.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a teacher and a Skill-assisted student.")
    run.add_argument("--framework", choices=["langgraph"], default="langgraph")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--teacher", default="claude-opus-5-5")
    run.add_argument("--teacher-effort", default="max")
    run.add_argument("--student", default="claude-haiku-5-5")
    run.add_argument("--effort", default="none")
    run.add_argument("--teacher-seed", type=int, default=22)
    run.add_argument("--seed", type=int, default=10134)
    run.add_argument("--compile-timeout", type=int, default=900)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "run":
        try:
            return asyncio.run(run_command(args))
        except Exception as error:  # noqa: BLE001
            print(f"Run failed: {error}", file=sys.stderr)
            return 1
    parser.error("Unknown command.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
