"""ALFWorld episodes for the Agent Skills transfer benchmark.

Arms: `none` (the RSD paper prompt only) and `skills` (the same prompt plus Agent
Skills that the compiler made from train-split Traces). The `teacher` stage runs
train games and records each episode as a Zep Trace for the compiler.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
SKILLS_HARNESS = HERE.parent / "tau3-appworld"
DATA = Path(os.environ.get("ALFWORLD_DATA", Path.home() / "alfworld/data"))
MODEL = os.getenv("ALFWORLD_MODEL", "qwen3-8b")
MAX_TURNS = 30

SYSTEM = (
    "You are an embodied agent solving a household task in a text-based simulator (AlfWorld). "
    "You receive an Observation describing what you can see and a list of Admissible commands "
    "you may execute. On every turn you must reply with exactly two lines:\n"
    "Thought: <one short sentence of reasoning>\n"
    "Action: <one command, copied verbatim from the Admissible list when possible>\n"
    "Plan briefly, then issue the next action. Only the line beginning with 'Action:' is used "
    "to step the environment. Do not add any other text after the Action line."
)

TASK_TYPES = {
    "pick_and_place_simple": "put",
    "look_at_obj_in_light": "examine",
    "pick_clean_then_place_in_recep": "clean",
    "pick_heat_then_place_in_recep": "heat",
    "pick_cool_then_place_in_recep": "cool",
    "pick_two_obj_and_place": "puttwo",
}
ACTION_RE = re.compile(r"^\s*Action\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def game_files(split: str) -> list[Path]:
    games = []
    for game in sorted((DATA / "json_2.1.1" / split).glob("*/*/game.tw-pddl")):
        root = str(game.parent)
        if "movable" in root or "Sliced" in root:
            continue
        if json.loads(game.read_text()).get("solvable"):
            games.append(game)
    return games


def task_type(game: Path) -> str:
    prefix = game.parent.parent.name.split("-")[0]
    if prefix not in TASK_TYPES:
        raise ValueError(f"unknown task type for {game}")
    return prefix


def game_id(game: Path) -> str:
    return f"{game.parent.parent.parent.name}/{game.parent.parent.name}/{game.parent.name}"


# The ReAct demonstrations use the ALFWorld 0.2 command "put X in/on Y". ALFWorld 0.4
# accepts only "move X to Y" and answers "Nothing happens" to "put".
_PUT_ACTION = re.compile(r"> put (.+?) in/on (.+)")
_PUT_RESULT = re.compile(r"You put the (.+?) in/on the (.+?)\.")


def demo_mode() -> str:
    return "fixed" if os.environ.get("ALFWORLD_DEMO") == "fixed" else "typed"


def demonstration(kind: str) -> str:
    prompts = json.loads((Path.home() / "alfworld/alfworld_3prompts.json").read_text())
    family = "put" if os.environ.get("ALFWORLD_DEMO") == "fixed" else TASK_TYPES[kind]
    text = prompts[f"react_{family}_1"].strip()
    text = _PUT_ACTION.sub(r"> move \1 to \2", text)
    return _PUT_RESULT.sub(r"You move the \1 to the \2.", text)


def admissible(commands: list[str]) -> str:
    shown = [c for c in commands if c not in {"look", "inventory", "help"}] or commands
    return "; ".join(shown)


def user_turn(goal: str, observation: str, commands: list[str], first: bool) -> str:
    head = f"Task: {goal}\n" if first else ""
    return f"{head}Observation: {observation}\nAdmissible: {admissible(commands)}"


def split_intro(text: str) -> tuple[str, str]:
    body = text.split("-= Welcome to TextWorld, ALFRED! =-", 1)[-1].strip()
    scene, _, goal = body.partition("Your task is to: ")
    return goal.strip().rstrip("."), scene.strip()


def chat(
    client: httpx.Client, messages: list[dict[str, str]], temperature: float, seed: int
) -> dict[str, Any]:
    base_url = os.getenv("ALFWORLD_MODEL_BASE_URL", "").rstrip("/")
    token = os.getenv("ALFWORLD_BEARER_TOKEN")
    if not base_url or not token:
        raise RuntimeError("Set ALFWORLD_MODEL_BASE_URL and ALFWORLD_BEARER_TOKEN.")
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "top_p": 0.8,
        "top_k": 20,
        "max_tokens": 512,
        "seed": seed,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last: Exception | None = None
    for attempt in range(6):
        started = time.monotonic()
        try:
            response = client.post(
                base_url + "/v1/chat/completions",
                json=body,
                headers={"Authorization": "Bearer " + token},
                timeout=600,
            )
            if response.status_code in {401, 403, 429, 502, 503, 504}:
                raise httpx.HTTPStatusError(
                    str(response.status_code),
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            data = response.json()
            return {
                "text": data["choices"][0]["message"]["content"] or "",
                "finish_reason": data["choices"][0].get("finish_reason"),
                "usage": data.get("usage") or {},
                "latency_s": time.monotonic() - started,
                "attempts": attempt + 1,
            }
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            last = exc
            time.sleep(min(60, 5 * 2**attempt))
    raise RuntimeError(f"model call failed after retries: {type(last).__name__}: {last}")


def skill_block(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    parts = [str(h.get("markdown") or "").strip() for h in hits if h.get("markdown")]
    return (
        "Skills from earlier tasks of this kind. Use them when they apply:\n\n"
        + "\n\n---\n\n".join(parts)
    )


def run_episode(
    game: Path,
    split: str,
    arm: str,
    seed: int,
    temperature: float,
    skills: dict[str, list[dict[str, Any]]],
    zep: dict[str, Any] | None,
) -> dict[str, Any]:
    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    kind = task_type(game)
    infos = textworld.EnvInfos(won=True, admissible_commands=True, extras=["gamefile"])
    env_id = textworld.gym.register_games(
        [str(game)],
        infos,
        batch_size=1,
        asynchronous=False,
        max_episode_steps=MAX_TURNS + 5,
        wrappers=[AlfredDemangler(shuffle=False), AlfredInfos],
    )
    env = textworld.gym.make(env_id)
    obs, info = env.reset()
    goal, scene = split_intro(obs[0])
    hits: list[dict[str, Any]] = []
    if arm == "skills":
        try:
            hits = zep["search"](goal, kind) if zep and "search" in zep else skills.get(kind, [])
        except Exception:
            env.close()
            raise
    system = SYSTEM
    block = skill_block(hits)
    if block:
        system += "\n\n" + block
    demo = demonstration(kind)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Here is a demonstration of a solved task:\n" + demo,
        },
        {
            "role": "assistant",
            "content": "Understood. I will reply with a Thought line and an Action line.",
        },
    ]
    row: dict[str, Any] = {
        "game": game_id(game),
        "split": split,
        "task_type": kind,
        "arm": arm,
        "demo": demo_mode(),
        "seed": seed,
        "goal": goal,
        "skill_ids": [str(h.get("uuid") or h.get("skill_id") or "") for h in hits],
        "won": False,
        "turns": 0,
        "invalid_format": 0,
        "calls": [],
        "steps": [],
    }
    recorder = None
    if zep and "recorder" in zep:
        recorder = zep["recorder"](goal=goal, kind=kind, game=row["game"], seed=seed)
        recorder.__enter__()
        row["trajectory_uuid"] = recorder.trajectory_uuid
        recorder.record_message("user", f"Task: {goal}\nObservation: {scene}")
    error: BaseException | None = None
    try:
        with httpx.Client() as client:
            observation, commands = scene, info["admissible_commands"][0]
            for turn in range(MAX_TURNS):
                messages.append(
                    {
                        "role": "user",
                        "content": user_turn(goal, observation, commands, turn == 0),
                    }
                )
                reply = chat(client, messages, temperature, seed * 1000 + turn)
                row["calls"].append(
                    {k: reply[k] for k in ("usage", "latency_s", "attempts", "finish_reason")}
                )
                text = reply["text"].strip()
                messages.append({"role": "assistant", "content": text})
                match = ACTION_RE.search(text)
                action = match.group(1).strip() if match else ""
                if not match:
                    row["invalid_format"] += 1
                obs, _, done, info = env.step([action or "look"])
                observation, commands = obs[0].strip(), info["admissible_commands"][0]
                row["turns"] = turn + 1
                row["steps"].append({"action": action, "observation": observation[:300]})
                if recorder:
                    recorder.record_tool_call("act", {"command": action}, call_id=f"t{turn}")
                    recorder.record_tool_result("act", observation, call_id=f"t{turn}")
                if info["won"][0]:
                    row["won"] = True
                    break
                if done[0]:
                    break
    except BaseException as exc:  # noqa: BLE001
        error = exc
        row["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        env.close()
        if recorder:
            recorder.record_message(
                "assistant", "Task completed." if row["won"] else "Task not completed."
            )
            recorder.set_result(row["won"], "error" not in row)
            recorder.__exit__(None, None, None)
    usage = [c["usage"] for c in row["calls"]]
    row["prompt_tokens"] = sum(u.get("prompt_tokens", 0) for u in usage)
    row["completion_tokens"] = sum(u.get("completion_tokens", 0) for u in usage)
    row["cached_tokens"] = sum(
        (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0 for u in usage
    )
    row["latency_s"] = sum(c["latency_s"] for c in row["calls"])
    row["retried_calls"] = sum(1 for c in row["calls"] if c["attempts"] > 1)
    if error and not isinstance(error, Exception):
        raise error
    return row


def _zep_recorder_factory(args: argparse.Namespace):
    sys.path.insert(0, str(SKILLS_HARNESS))
    from config import zep_client
    from redaction import Redactor
    from zep_capture import TrajectoryRecorder

    client = zep_client(os.environ["ZEP_API_KEY"], os.getenv("ZEP_BASE_URL"))
    redactor = Redactor()
    counter = iter(range(10**9))

    def make(goal: str, kind: str, game: str, seed: int):
        return TrajectoryRecorder(
            client,
            args.agent_uuid,
            kind,
            goal,
            "alfworld",
            {"harness": "alfworld-skills", "stage": args.stage, "game": game},
            redactor=redactor,
            stage=args.stage,
            run_id=args.run_id,
            domain="alfworld",
            task_index=next(counter),
            trial=seed,
            verifier_id="alfworld-won",
        )

    return make


def _zep_search_factory(args: argparse.Namespace):
    sys.path.insert(0, str(SKILLS_HARNESS))
    from config import zep_client
    from zep_capture import model_dict, skill_uuid

    client = zep_client(os.environ["ZEP_API_KEY"], os.getenv("ZEP_BASE_URL"))

    # With a snapshot file, search only ranks: the hits keep the frozen snapshot versions,
    # so that compilation that continues on the Agent does not change the arm.
    snapshot = {
        s["uuid"]: s
        for rows in (
            json.loads(Path(args.skills_file).read_text()) if args.skills_file else {}
        ).values()
        for s in rows
    }

    def search(goal: str, kind: str) -> list[dict[str, Any]]:
        response = client.agent.skill.search(
            args.agent_uuid,
            query=goal,
            task_family=kind,
            include_markdown=True,
            page_size=20 if snapshot else args.top_k,
        )
        hits = [model_dict(hit) for hit in response.items or []]
        if snapshot:
            hits = [snapshot[u] for u in (str(skill_uuid(h)) for h in hits) if u in snapshot]
        return hits[: args.top_k]

    return search


def _episode_job(payload: dict[str, Any]) -> dict[str, Any]:
    args = argparse.Namespace(**payload["args"])
    zep: dict[str, Any] | None = None
    if args.stage == "teacher":
        zep = {"recorder": _zep_recorder_factory(args)}
    elif args.arm == "skills" and args.agent_uuid:
        zep = {"search": _zep_search_factory(args)}
    try:
        return run_episode(
            Path(payload["game"]),
            payload["split"],
            args.arm,
            payload["seed"],
            args.temperature,
            payload["skills"],
            zep,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "game": game_id(Path(payload["game"])),
            "split": payload["split"],
            "task_type": Path(payload["game"]).parent.parent.name.split("-")[0],
            "arm": args.arm,
            "demo": demo_mode(),
            "seed": payload["seed"],
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["teacher", "eval"], required=True)
    parser.add_argument("--arm", choices=["none", "skills"], default="none")
    parser.add_argument("--splits", default="valid_seen,valid_unseen")
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--limit", type=int, default=0, help="games per split; 0 = all")
    parser.add_argument("--per-type", type=int, default=0, help="teacher: games per task type")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--skills-file", default="")
    parser.add_argument(
        "--agent-uuid",
        default="",
        help="teacher: capture Agent; eval skills: search Agent",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-failed", type=int, default=3)
    args = parser.parse_args()
    if not os.getenv("ALFWORLD_MODEL_BASE_URL") or not os.getenv("ALFWORLD_BEARER_TOKEN"):
        parser.error("Set ALFWORLD_MODEL_BASE_URL and ALFWORLD_BEARER_TOKEN.")

    skills = json.loads(Path(args.skills_file).read_text()) if args.skills_file else {}
    if args.arm == "skills" and not (skills or args.agent_uuid):
        parser.error("--arm skills needs --skills-file or --agent-uuid")
    skills = {kind: rows[: args.top_k] for kind, rows in skills.items()}
    jobs = []
    for split in args.splits.split(","):
        games = game_files(split)
        if args.per_type:
            rng = random.Random(0)
            rng.shuffle(games)
            picked: dict[str, list[Path]] = {}
            for g in games:
                picked.setdefault(task_type(g), [])
                if len(picked[task_type(g)]) < args.per_type:
                    picked[task_type(g)].append(g)
            games = sorted(g for group in picked.values() for g in group)
        elif args.limit:
            games = random.Random(0).sample(games, args.limit)
        for seed in [int(s) for s in args.seeds.split(",")]:
            jobs.extend({"game": str(g), "split": split, "seed": seed} for g in games)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            if (r.get("arm"), r.get("demo", demo_mode())) != (args.arm, demo_mode()):
                parser.error(f"{out} has rows of another arm or demonstration")
            if "error" not in r:
                done.add((r["game"], r["split"], r["seed"]))
    jobs = [j for j in jobs if (game_id(Path(j["game"])), j["split"], j["seed"]) not in done]
    print(f"{len(jobs)} episodes to run ({len(done)} already done)", flush=True)
    failed = 0
    with out.open("a") as sink, concurrent.futures.ProcessPoolExecutor(args.concurrency) as pool:
        futures = [
            pool.submit(_episode_job, {**j, "args": vars(args), "skills": skills}) for j in jobs
        ]
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            row = future.result()
            sink.write(json.dumps(row) + "\n")
            sink.flush()
            if "error" in row:
                failed += 1
                print(f"FAILED {row['game']}: {row['error']}", flush=True)
                if failed >= args.max_failed:
                    for f in futures:
                        f.cancel()
                    raise SystemExit(f"stop rule: {failed} failed episodes")
            if n % 10 == 0:
                print(f"{n}/{len(jobs)} done", flush=True)


if __name__ == "__main__":
    main()
