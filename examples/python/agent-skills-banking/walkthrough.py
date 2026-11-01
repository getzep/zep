"""Run the live public learning loop or a frozen repeated check."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import re
import tarfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from banking import DOCUMENTS, expected_database, make_case
from hosted import Hosted, readable_skills
from runner import (
    DEFAULT_STUDENT_MODEL,
    episode,
    source_hashes,
    trace_events,
    write_json,
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def skill_pin(skills: list[dict]) -> list[dict]:
    return sorted(
        [
            {
                "uuid": item["uuid"],
                "version": item["version"],
                "markdown_sha256": hashlib.sha256(item["markdown"].encode()).hexdigest(),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
            }
            for item in skills
        ],
        key=lambda item: item["uuid"],
    )


def require_sources(expected: dict) -> None:
    if source_hashes() != expected:
        raise RuntimeError("The package source changed. Keep this attempt and use a new directory.")


def installed_versions() -> dict[str, str]:
    return {
        name: importlib.metadata.version(name)
        for name in ("pydantic", "pydantic-ai-slim", "anthropic", "openai", "httpx", "zep-cloud")
    }


def money_patterns(value: Decimal) -> list[str]:
    text = f"{value:.2f}"
    variants = {text, f"{value:,.2f}"}
    if text.endswith(".00"):
        variants |= {text[:-3], f"{value:,.2f}"[:-3]}
    elif text.endswith("0"):
        variants |= {text[:-1], f"{value:,.2f}"[:-1]}
    return [r"(?<![\d.])" + re.escape(item) + r"0*(?!\d|\.\d)" for item in variants]


def allocation_signature(seed: int) -> tuple:
    case = make_case(seed)
    checking = {account.account_id: account.product for account in case.checking}
    cards = {card.card_id: card.product for card in case.cards}
    products = {account.account_id: account.product for account in case.savings}
    return tuple(
        (
            products[report.account_id],
            checking[report.checking_account_id] if report.checking_account_id else None,
            cards[report.card_id] if report.card_id else None,
        )
        for report in expected_database(case).reports
    )


def check_leakage(skills: list[dict], seeds: list[int]) -> dict:
    markdown = "\n".join(item["markdown"] for item in skills)
    hits = []
    for seed in seeds:
        case = make_case(seed)
        gold = expected_database(case)
        identifiers = [case.case_id] + [a.account_id for a in case.savings]
        identifiers += [a.account_id for a in case.checking] + [c.card_id for c in case.cards]
        for value in identifiers:
            if value in markdown:
                hits.append({"case_id": case.case_id, "value": value})
        amounts = [a.statement_balance for a in case.savings]
        amounts += [c.amount for c in gold.credits]
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


def snapshot(output: Path) -> None:
    source = Path(__file__).resolve().parent
    with tarfile.open(output / "source-before-run.tar.gz", "w:gz") as archive:
        for path in source.iterdir():
            if path.is_file() and (path.suffix in {".py", ".toml", ".lock", ".md"}):
                archive.add(path, arcname=path.name)


def live_skills(hosted: Hosted, agent: str, pin: list[dict], path: Path) -> list[dict]:
    response = hosted.search(agent)
    write_json(path, response)
    skills = readable_skills(response)
    if skill_pin(skills) != pin:
        raise RuntimeError("The live Skill catalog changed. This attempt is not valid.")
    return skills


async def stage(
    output: Path,
    name: str,
    seed: int,
    model: str,
    effort: str,
    source: dict,
    skills: list[dict] | None = None,
    teaching: bool = False,
) -> dict:
    require_sources(source)
    path = output / name
    result = path / "result.json"
    if result.exists():
        row = json.loads(result.read_text())
        if row.get("finish_reason") != "stop":
            raise RuntimeError(f"The saved {name} response did not finish normally.")
        return row
    if path.exists():
        raise RuntimeError(f"The {name} stage is incomplete. It will not be overwritten.")
    row = await episode(path, make_case(seed), model, effort, skills, teaching=teaching)
    require_sources(source)
    return row


async def run_demo(args: argparse.Namespace) -> None:
    output = Path(args.output).resolve()
    if output.exists() and not args.resume:
        raise FileExistsError("Use a new output directory, or --resume for completed stages.")
    output.mkdir(parents=True, exist_ok=True)
    config_file = output / "config.json"
    config = {
        "student": args.student,
        "effort": args.effort,
        "teacher": args.teacher,
        "teacher_effort": args.teacher_effort,
        "teacher_teaching_output": True,
        "student_temperature": None,
        "seed": args.seed,
        "teacher_seed": args.teacher_seed,
        "screening_seeds": [args.seed],
        "source": source_hashes(),
        "installed_versions": installed_versions(),
    }
    if args.seed == args.teacher_seed:
        raise ValueError("Use different teacher and walkthrough cases.")
    if allocation_signature(args.seed) == allocation_signature(args.teacher_seed):
        raise ValueError(
            "Use teacher and student cases with different optimal product allocations."
        )
    if config_file.exists():
        if json.loads(config_file.read_text()) != config:
            raise RuntimeError("The saved configuration differs from this command.")
    else:
        write_json(config_file, config)
        write_json(
            output / "cases.json",
            {
                "teacher": make_case(args.teacher_seed).model_dump(mode="json"),
                "student": make_case(args.seed).model_dump(mode="json"),
                "documents": DOCUMENTS,
            },
        )
        snapshot(output)
    with Hosted(output / "zep-calls.jsonl") as hosted:
        baseline = await stage(
            output, "without-skill", args.seed, args.student, args.effort, config["source"]
        )
        teacher = await stage(
            output,
            "teacher",
            args.teacher_seed,
            args.teacher,
            args.teacher_effort,
            config["source"],
            teaching=True,
        )
        if (
            not teacher["passed"]
            or teacher["termination"] != "final_answer"
            or teacher.get("finish_reason") != "stop"
        ):
            raise RuntimeError("The teacher did not pass. No successful Trace will be uploaded.")
        agent_file = output / "agent.json"
        if not agent_file.exists():
            write_json(agent_file, hosted.create_agent())
        agent = json.loads(agent_file.read_text())["uuid"]
        upload_file = output / "upload.json"
        if not upload_file.exists():
            marker = output / "upload-start.json"
            if marker.exists():
                write_json(upload_file, hosted.complete_upload(agent, marker, args.compile_timeout))
            else:
                events = trace_events(output / "teacher")
                write_json(output / "teacher-events.json", events)
                write_json(
                    marker,
                    {"stage": "starting", "agent_uuid": agent, "teacher_evaluation": "passed"},
                )
                write_json(upload_file, hosted.upload(agent, events, marker, args.compile_timeout))
        skill_file = output / "compiled-skills.json"
        if not skill_file.exists():
            print("The teacher passed. Zep is compiling its verified Trace.", flush=True)
            write_json(skill_file, hosted.wait(agent, args.compile_timeout))
        skills = readable_skills(json.loads(skill_file.read_text()))
        pin = skill_pin(skills)
        if not skills:
            raise RuntimeError("No complete Skill was returned.")
        write_json(output / "skill-pin.json", pin)
        for skill in skills:
            (output / f"skill-{skill['uuid']}-v{skill['version']}.md").write_text(skill["markdown"])
            print(
                f"Skill {skill['uuid']}, version {skill['version']}; "
                f"SHA-256 {hashlib.sha256(skill['markdown'].encode()).hexdigest()}:\n{skill['markdown']}",
                flush=True,
            )
        leakage = check_leakage(skills, [args.seed])
        write_json(output / "leakage-audit.json", leakage)
        if leakage["exact_heldout_identifiers_balances_or_corrections"]:
            raise RuntimeError("The Skill contains a held-out identifier, balance, or correction.")
        if leakage["source_id_suffixes"]:
            raise RuntimeError("The Skill contains case-specific checking or card ID suffixes.")
        if args.stop_after_skill:
            print(
                "The Skill is saved. Review its procedure before resuming the student run.",
                flush=True,
            )
            return
        skills = live_skills(hosted, agent, pin, output / "live-search-before-student.json")
        treatment = await stage(
            output, "with-skill", args.seed, args.student, args.effort, config["source"], skills
        )
        rows = {"without_skill": baseline, "teacher": teacher, "with_skill": treatment}
        write_json(output / "summary.json", rows)
        print("\nOne synthetic demonstration episode per configuration:", flush=True)
        for name, row in rows.items():
            print(
                f"{name}: {'PASS' if row['passed'] else 'FAIL'}; "
                f"{row['tokens']:,} tokens; {row['seconds']:.1f} seconds; "
                f"{row['failed_provider_calls']} failed provider calls",
                flush=True,
            )
        print(
            "This is not a tau3 result. This single run does not establish reliability.", flush=True
        )


async def verify(args: argparse.Namespace) -> None:
    if args.count < 1 or args.first_run < 0:
        raise ValueError("Use a positive count and a non-negative first run index.")
    learned = Path(args.learned_run).resolve()
    config = json.loads((learned / "config.json").read_text())
    require_sources(config["source"])
    if installed_versions() != config["installed_versions"]:
        raise RuntimeError("The installed dependency versions changed.")
    agent = json.loads((learned / "agent.json").read_text())["uuid"]
    pin = json.loads((learned / "skill-pin.json").read_text())
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with Hosted(output / "zep-calls.jsonl") as hosted:
        skills = live_skills(hosted, agent, pin, output / "initial-live-search.json")
        seeds = (
            [config["seed"]] * args.count
            if args.repeat_case
            else list(range(args.first_seed, args.first_seed + args.count))
        )
        run_indices = list(range(args.first_run, args.first_run + args.count))
        excluded = set(config["screening_seeds"] + [config["teacher_seed"], config["seed"]])
        if not args.repeat_case and excluded.intersection(seeds):
            raise ValueError("The check must use fresh cases, not the teacher or walkthrough case.")
        leakage = check_leakage(skills, seeds)
        write_json(output / "leakage-audit.json", leakage)
        if leakage["exact_heldout_identifiers_balances_or_corrections"]:
            raise RuntimeError("The Skill contains final-case values. Do not run the check.")
        if leakage["source_id_suffixes"]:
            raise RuntimeError("The Skill contains case-specific checking or card ID suffixes.")
        manifest = {
            "configuration": config,
            "agent_uuid": agent,
            "skill_pin": pin,
            "count": args.count,
            "seeds": seeds,
            "repeat_case": args.repeat_case,
            "run_indices": run_indices,
            "distinct_cases": len(set(seeds)),
            "concurrency": 1,
            "cases": [make_case(seed).model_dump(mode="json") for seed in seeds],
            "documents": DOCUMENTS,
            "acceptance": "Every without-Skill episode fails; every with-Skill episode passes.",
            "order": [
                (["without-skill", "with-skill"] if i % 2 == 0 else ["with-skill", "without-skill"])
                for i in run_indices
            ],
        }
        write_json(output / "frozen-manifest.json", manifest)
        (output / "frozen-manifest.sha256").write_text(digest(manifest) + "\n")
        register_attempt(
            learned, output, seeds, digest(manifest), run_indices if args.repeat_case else None
        )
        snapshot(output)
        pairs = []
        for index, seed in enumerate(seeds):
            run_index = run_indices[index]
            pair: dict[str, Any] = {"seed": seed, "run_index": run_index, "pattern": seed % 3}
            for arm in manifest["order"][index]:
                name = f"{arm}-seed{seed}-run{run_index}"
                current = live_skills(hosted, agent, pin, output / f"search-{name}.json")
                row = await stage(
                    output,
                    name,
                    seed,
                    config["student"],
                    config["effort"],
                    config["source"],
                    current if arm == "with-skill" else [],
                )
                for label, file in (
                    ("tool_sequence", "tool-calls.json"),
                    ("database", "database.json"),
                ):
                    row[f"{label}_sha256"] = digest(json.loads((output / name / file).read_text()))
                pair[arm] = row
            pair["flip"] = not pair["without-skill"]["passed"] and pair["with-skill"]["passed"]
            pairs.append(pair)
            with (output / "paired-results.jsonl").open("a") as file:
                file.write(json.dumps(pair) + "\n")
            print(
                f"Check progress: {index + 1}/{args.count} pairs; "
                f"{sum(p['flip'] for p in pairs)} flips; zero failed provider calls.",
                flush=True,
            )
        write_json(
            output / "summary.json",
            {
                "pairs": args.count,
                "without_skill_passes": sum(p["without-skill"]["passed"] for p in pairs),
                "with_skill_passes": sum(p["with-skill"]["passed"] for p in pairs),
                "flips": sum(p["flip"] for p in pairs),
                "accepted": all(p["flip"] for p in pairs),
                "distinct_cases": len(set(seeds)),
                "scope": (
                    "Repeated invocations of one selected synthetic case; not independent tasks."
                    if args.repeat_case
                    else "A selected synthetic task generator, not independent benchmark tasks."
                ),
                "distinct_tool_sequences": {
                    arm: len({p[arm]["tool_sequence_sha256"] for p in pairs})
                    for arm in ("without-skill", "with-skill")
                },
                "distinct_final_databases": {
                    arm: len({p[arm]["database_sha256"] for p in pairs})
                    for arm in ("without-skill", "with-skill")
                },
                "by_pattern": {
                    str(pattern): {
                        "pairs": sum(p["pattern"] == pattern for p in pairs),
                        "flips": sum(p["pattern"] == pattern and p["flip"] for p in pairs),
                    }
                    for pattern in [0, 1, 2]
                },
            },
        )


def register_attempt(
    learned: Path,
    output: Path,
    seeds: list[int],
    manifest_hash: str,
    run_indices: list[int] | None = None,
) -> None:
    import fcntl

    ledger = "repeated-attempts.jsonl" if run_indices is not None else "verification-attempts.jsonl"
    with (learned / ledger).open("a+") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.seek(0)
        for line in file:
            prior = json.loads(line)
            if run_indices is not None:
                if set(run_indices).intersection(prior["run_indices"]):
                    raise ValueError("The repeated run indices overlap an earlier attempt.")
            elif set(seeds).intersection(prior["seeds"]):
                raise ValueError("The final cases overlap an earlier verification attempt.")
        file.write(
            json.dumps(
                {
                    "time_ns": time.time_ns(),
                    "output": str(output),
                    "seeds": seeds,
                    "run_indices": run_indices,
                    "manifest_sha256": manifest_hash,
                }
            )
            + "\n"
        )
        file.flush()
        fcntl.flock(file, fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--output", required=True)
    run.add_argument("--student", default=DEFAULT_STUDENT_MODEL)
    run.add_argument("--effort", default="none")
    run.add_argument("--teacher", default="claude-opus-5-5")
    run.add_argument("--teacher-effort", default="max")
    run.add_argument("--seed", type=int, default=10134)
    run.add_argument("--teacher-seed", type=int, default=22)
    run.add_argument("--compile-timeout", type=int, default=900)
    run.add_argument("--resume", action="store_true")
    run.add_argument(
        "--stop-after-skill",
        action="store_true",
        help="Save and display the compiled Skill before the student run.",
    )
    check = commands.add_parser("verify")
    check.add_argument("--learned-run", required=True)
    check.add_argument("--output", required=True)
    check.add_argument("--first-seed", type=int, default=10000)
    check.add_argument("--count", type=int, default=29)
    check.add_argument("--repeat-case", action="store_true")
    check.add_argument("--first-run", type=int, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(run_demo(args) if args.command == "run" else verify(args))
    except BaseException as error:
        output = Path(args.output)
        if output.exists():
            write_json(
                output / f"run-failure-{time.time_ns()}.json",
                {"type": type(error).__name__, "message": str(error)},
            )
        raise


if __name__ == "__main__":
    main()
