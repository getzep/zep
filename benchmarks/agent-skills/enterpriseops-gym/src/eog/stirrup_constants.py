"""Stirrup constants pinned to commit 247f24d56b2108235880ed2a2baea5d35b5a67ee."""

from stirrup.constants import CONTEXT_SUMMARIZATION_CUTOFF, TURNS_REMAINING_WARNING_THRESHOLD
from stirrup.prompts import (
    BASE_SYSTEM_PROMPT_TEMPLATE,
    MESSAGE_SUMMARIZER,
    MESSAGE_SUMMARIZER_BRIDGE_TEMPLATE,
    MESSAGE_SUMMARIZER_TEXT_ONLY,
)
from stirrup.tools.finish import SIMPLE_FINISH_TOOL

__all__ = (
    "BASE_SYSTEM_PROMPT_TEMPLATE",
    "CONTEXT_SUMMARIZATION_CUTOFF",
    "FINISH_TOOL_DESCRIPTION",
    "FINISH_TOOL_NAME",
    "FINISH_TOOL_SCHEMA",
    "LAST_TURN_WARNING",
    "MAX_TURNS",
    "MESSAGE_SUMMARIZER",
    "MESSAGE_SUMMARIZER_BRIDGE_TEMPLATE",
    "MESSAGE_SUMMARIZER_TEXT_ONLY",
    "STIRRUP_COMMIT",
    "TURNS_REMAINING_WARNING_THRESHOLD",
    "turns_remaining_warning",
)

STIRRUP_COMMIT = "247f24d56b2108235880ed2a2baea5d35b5a67ee"
MAX_TURNS = 100
FINISH_TOOL_NAME = SIMPLE_FINISH_TOOL.name
FINISH_TOOL_DESCRIPTION = SIMPLE_FINISH_TOOL.description
FINISH_TOOL_SCHEMA = SIMPLE_FINISH_TOOL.parameters.model_json_schema()
LAST_TURN_WARNING = "This is the last turn. Please finish the task by calling a finish tool."
NO_USER_INPUT_GUIDANCE = " You are not able to interact with the user during the task."
TASK_INSTRUCTIONS_HEADER = "\n\nFollow these instructions from the User:\n"


def turns_remaining_warning(remaining: int) -> str:
    if remaining == 1:
        return LAST_TURN_WARNING
    return (
        f"You have {remaining} turns remaining to complete the task. Please continue. "
        "Remember you will need a separate turn to call a finish tool."
    )
