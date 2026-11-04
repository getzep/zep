from __future__ import annotations

from pathlib import Path

import pytest

from appworld_replay import ReplayInputError, _messages_for_row


def test_ambiguous_transcripts_still_fail_closed(tmp_path: Path) -> None:
    row = {"task_id": "scenario_demo_2", "task_index": 0, "trial": 1}
    transcripts = {
        "scenario_demo_2": [
            {"trial": 2, "messages": []},
            {"trial": 3, "messages": []},
        ]
    }

    with pytest.raises(ReplayInputError, match="saved transcript.*ambiguous"):
        _messages_for_row(
            row,
            transcripts,
            tmp_path,
            task_has_multiple_trials=True,
        )
