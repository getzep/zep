import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import alfworld_run as run


def _set_prompts(monkeypatch, tmp_path):
    prompt_dir = tmp_path / "alfworld"
    prompt_dir.mkdir()
    prompts = {
        f"react_{family}_1": "> put apple in/on box.\nYou put the apple in/on the box."
        for family in {*run.TASK_TYPES.values(), "put"}
    }
    (prompt_dir / "alfworld_3prompts.json").write_text(json.dumps(prompts))
    monkeypatch.setattr(run.Path, "home", classmethod(lambda cls: tmp_path))


def test_action_line_is_parsed():
    text = "Thought: The mug can be in the cabinet.\nAction: go to cabinet 1"
    assert run.ACTION_RE.search(text).group(1) == "go to cabinet 1"


def test_admissible_hides_meta_commands_and_keeps_all_others():
    commands = ["look", "inventory", "help"] + [f"go to drawer {i}" for i in range(40)]
    shown = run.admissible(commands).split("; ")
    assert "look" not in shown
    assert len(shown) == 40


def test_skill_block_is_empty_without_markdown():
    assert run.skill_block([]) == ""
    assert "Use them" in run.skill_block([{"markdown": "# Heat\nUse the microwave."}])


def test_fixed_demonstration_ignores_task_type(monkeypatch, tmp_path):
    _set_prompts(monkeypatch, tmp_path)
    monkeypatch.setenv("ALFWORLD_DEMO", "fixed")
    assert run.demonstration("pick_heat_then_place_in_recep") == run.demonstration(
        "pick_and_place_simple"
    )


def test_demonstration_uses_move_command(monkeypatch, tmp_path):
    _set_prompts(monkeypatch, tmp_path)
    for kind in run.TASK_TYPES:
        text = run.demonstration(kind)
        assert "> put " not in text
        assert "You put " not in text
    assert "> move " in run.demonstration("pick_and_place_simple")
