from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools import BankingTools


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


@dataclass
class EpisodeResult:
    final_answer: str
    tokens: int
    tools: BankingTools
