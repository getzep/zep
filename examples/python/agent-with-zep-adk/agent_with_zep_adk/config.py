"""Runtime settings and agent configuration toggles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "agent-with-zep" / "data"
CACHE_DIR = BASE_DIR / ".cache"
ENV_FILE = BASE_DIR / ".env"

AS_OF_DATE = "2026-09-15"

# Budgets (see the tool rules in the system prompt).
MAX_TOOL_CALLS = 12
MAX_PLANS = 2
MAX_RESULT_CHARS = 4_000
SEARCH_LIMIT_DEFAULT = 10
# The v4 search APIs return at most 50 results per page.
SEARCH_LIMIT_MAX = 50
# The v4 list APIs return at most 100 results per page. The tools request one
# extra result to detect truncation, so they can report at most 99.
LIST_LIMIT_MAX = 99
NEIGHBOR_LIMIT_MAX = 99
EPISODE_TEXT_MAX_CHARS = 3_000
ORIENTATION_SAMPLE_SIZE = 30

# Valid values for the unified `thinking` model setting (MODEL_THINKING).
THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh")


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings shared by the CLI, server, ingest, and eval."""

    zep_api_key: str
    zep_base_url: str | None
    graph_uuid: str
    agent_model: str
    judge_model: str
    model_thinking: str = "low"

    def __post_init__(self) -> None:
        if self.model_thinking not in THINKING_LEVELS:
            raise ValueError(
                f"MODEL_THINKING must be one of {', '.join(THINKING_LEVELS)}; got {self.model_thinking!r}."
            )

    def require_graph_uuid(self) -> str:
        if not self.graph_uuid:
            raise RuntimeError("GRAPH_UUID is empty. Run ingest first to create a graph.")
        return self.graph_uuid

    @classmethod
    def from_env(cls, require_zep_key: bool = True) -> Settings:
        load_dotenv(ENV_FILE)
        zep_api_key = os.environ.get("ZEP_API_KEY", "")
        if require_zep_key and not zep_api_key:
            raise RuntimeError("Set ZEP_API_KEY in the environment or in .env")
        return cls(
            zep_api_key=zep_api_key,
            zep_base_url=os.environ.get("ZEP_BASE_URL") or None,
            graph_uuid=os.environ.get("GRAPH_UUID", "").strip(),
            agent_model=os.environ.get("AGENT_MODEL", "openai/gpt-6-luna"),
            judge_model=os.environ.get("JUDGE_MODEL", "openai/gpt-6-luna"),
            model_thinking=os.environ.get("MODEL_THINKING", "low"),
        )


@dataclass(frozen=True)
class AgentConfig:
    """Ablation toggles for the reference agent."""

    tools: str = "full"  # "naive" = search_context only, "full" = all six tools
    orientation: bool = True
    domain_knowledge: bool = True
    planning: bool = True
