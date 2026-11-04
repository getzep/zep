from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path
from typing import Any

ALLTOOLS = frozenset(
    {
        "transfer_to_human_agents",
        "get_current_time",
        "get_user_information_by_id",
        "get_user_information_by_name",
        "get_user_information_by_email",
        "change_user_email",
        "get_referrals_by_user",
        "get_credit_card_transactions_by_user",
        "get_credit_card_accounts_by_user",
        "log_verification",
        "give_discoverable_user_tool",
        "unlock_discoverable_agent_tool",
        "call_discoverable_agent_tool",
        "list_discoverable_agent_tools",
        "KB_search_bm25",
        "KB_search_dense",
        "shell",
    }
)


def trial_seeds(trials: int) -> list[int]:
    if trials < 1:
        raise ValueError("The number of trials must be positive")
    rng = random.Random(300)
    return [rng.randint(0, 1000000) for _ in range(trials)]


def user_args(args: Any) -> dict[str, Any]:
    supplied = getattr(args, "user_llm_args", None)
    return dict(supplied) if supplied is not None else {"temperature": args.user_temperature}


def json_object(value: str) -> dict[str, Any]:
    result = json.loads(value)
    if not isinstance(result, dict):
        raise TypeError("User LLM args must be a JSON object")
    if "reasoning_effort" in result and "temperature" in result:
        raise ValueError("Do not combine reasoning_effort and temperature in user LLM args")
    return result


def allowed_tools(skills: bool) -> list[str]:
    return sorted(ALLTOOLS | ({"read_skill"} if skills else set()))


def assert_tools(tools: list[dict[str, Any]], expected: list[str]) -> None:
    names = [tool.get("name") for tool in tools]
    if len(names) != len(expected) or set(names) != set(expected):
        raise ValueError(
            f"Executor tool gate rejected tool names: expected {expected}, got {names}"
        )


def validate_mode(args: Any) -> None:
    retrieval = getattr(args, "retrieval_config", "alltools")
    if retrieval in {"golden_retrieval", "full_kb"} or retrieval.endswith("_write"):
        raise ValueError(f"Unsafe retrieval configuration: {retrieval}")
    model = getattr(args, "agent_model", "")
    if model == "deepseek-v4.1-flash-1017" and args.reasoning_effort not in {
        "low",
        "high",
        "xhigh",
        "max",
    }:
        raise ValueError("DeepSeek V4.1 does not accept medium; use low, high, xhigh, or max")
    stage = args.stage
    if stage not in {"teacher", "compile", "publication-setup", "braintrust-source"}:
        arm = getattr(args, "arm", None)
        if stage == "student-skills" or arm not in {None, "none", "skills"}:
            raise ValueError("Executors support only the no-Skills arm or the frozen snapshot arm")
        if getattr(args, "skill_index_scope", "all") != "all":
            raise ValueError(
                "Executors require --skill-index-scope all; family filtering is forbidden"
            )
        if arm == "skills" and not getattr(args, "skill_snapshot", None):
            raise ValueError(
                "The Skills executor requires --skill-snapshot; live search is forbidden"
            )
        if getattr(args, "task_family_map", None) or getattr(args, "task_family_override", None):
            raise ValueError("Task type maps and family overrides are recording-only")
    if getattr(args, "task_family_map", None) and stage not in {"teacher", "compile"}:
        raise ValueError(
            "The task family map is available only for teacher recording and compilation"
        )


def recording_families(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    if (
        not isinstance(data, dict)
        or not data
        or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in data.items())
    ):
        raise ValueError(
            "The task family map must contain nonempty string families keyed by task ID"
        )
    return data


def git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, text=True
    ).strip()
