"""Progressive disclosure of a frozen Skill snapshot: an index in the prompt and a read_skill tool.

The design comes from the EnterpriseOps-Gym harness. The prompt lists the
name and the description of each Skill. The agent reads a Skill body with
read_skill. The snapshot is the file that `skill_snapshot.py` writes, so each
executor run reads the same Skills.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from skill_snapshot import skill_frontmatter

LOGGER = logging.getLogger(__name__)

READ_SKILL_TOOL_NAME = "read_skill"
MAX_DESCRIPTION_CHARS = 300
READ_SKILL_NAME_DISTANCE = 3
READ_SKILL_NONE = "none"
SKILLS_HEADER = (
    "## Reference procedures\n"
    "The list below names procedures that other runs used. Before your first tool call, use "
    "the read_skill tool to read each procedure whose description matches a part of the "
    "request. A request can need more than one procedure, and it can need none. Adapt the "
    "steps to the request; a procedure is a guide, not the request. Follow the policy when a "
    "procedure conflicts with it. Get every identifier and value from the user and the tools.\n\n"
)


def read_skill_tool(names: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": READ_SKILL_TOOL_NAME,
        "description": (
            "Read one reference procedure from the Reference procedures list. "
            "The result is the procedure text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The name of the procedure, as the Reference procedures list gives it, "
                        f'or "{READ_SKILL_NONE}" when no procedure matches the request.'
                    ),
                    "enum": [*names, READ_SKILL_NONE],
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "strict": True,
    }


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > READ_SKILL_NAME_DISTANCE:
        return READ_SKILL_NAME_DISTANCE + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        previous = current
    return previous[-1]


def load_snapshot_skills(path: Path, task_family: str | None = None) -> list[dict[str, Any]]:
    """Read the snapshot Skills, check each hash, and return them in snapshot order."""
    snapshot = json.loads(path.read_text())
    skills = []
    for item in snapshot["skills"]:
        if task_family is not None and item["task_family"] != task_family:
            continue
        if "path" in item:
            root = path.parent.resolve()
            skill_path = (root / item["path"]).resolve()
            if not skill_path.is_relative_to(root):
                raise ValueError(f"Skill path is outside the snapshot: {item['skill_id']}")
            markdown = skill_path.read_text()
        else:
            markdown = str(item.get("full_markdown") or item.get("markdown") or "")
        if hashlib.sha256(markdown.encode()).hexdigest() != item["sha256"]:
            raise ValueError(f"Skill snapshot hash mismatch: {item['skill_id']}")
        fields = skill_frontmatter(markdown)
        name = fields.get("name", "").strip()
        if not name:
            raise ValueError(f"Skill {item['skill_id']} has no name in its frontmatter")
        skills.append(
            {
                "uuid": str(item["skill_id"]),
                "skill_id": str(item["skill_id"]),
                "task_family": item["task_family"],
                "name": name,
                "description": " ".join(fields.get("description", "").split())[
                    :MAX_DESCRIPTION_CHARS
                ],
                "markdown": markdown,
            }
        )
    names = [skill["name"] for skill in skills]
    if len(set(names)) != len(names):
        raise ValueError("Two Skills in the snapshot have the same name")
    return skills


class SnapshotSkillToolkit:
    """Answers read_skill calls from a frozen snapshot. It has the interface of SkillToolkit."""

    tool_name = READ_SKILL_TOOL_NAME
    argument = "name"

    def __init__(
        self,
        skills: list[dict[str, Any]],
        *,
        on_event: Any = None,
    ) -> None:
        self.skills = skills
        self.on_event = on_event
        self.last_search: dict[str, Any] | None = None
        self.findings: list[str] = []
        self.loaded: list[dict[str, Any]] = []
        self.invalid_loads = 0
        self.read_calls: list[dict[str, Any]] = []

    @property
    def tool(self) -> dict[str, Any]:
        return read_skill_tool([skill["name"] for skill in self.skills])

    def search_skills(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        self.last_search = {"search_id": None, "hits": list(self.skills)}
        self._emit("skill_index", {"names": [skill["name"] for skill in self.skills]})
        return list(self.skills)

    @property
    def hits(self) -> list[dict[str, Any]]:
        return list(self.last_search["hits"]) if self.last_search else []

    def catalog(self) -> str:
        """Return the index for the system prompt.

        The descriptions come from a hash-checked snapshot of Zep teacher output, the same
        text that the live Skills arm shows. SKILLS_HEADER tells the model that the policy wins
        over a procedure, and each description is one line of at most MAX_DESCRIPTION_CHARS.
        """
        if not self.hits:
            return ""
        return SKILLS_HEADER + "\n".join(
            f"- {skill['name']}: {skill['description']}" for skill in self.hits
        )

    def resolve(self, name: str) -> tuple[dict[str, Any] | None, bool]:
        wanted = name.strip()
        exact = next((skill for skill in self.skills if skill["name"] == wanted), None)
        if exact is not None:
            return exact, False
        best, nearest = READ_SKILL_NAME_DISTANCE + 1, None
        for skill in self.skills:
            distance = _edit_distance(wanted.casefold(), skill["name"].casefold())
            if distance < best:
                best, nearest = distance, skill
        return nearest, nearest is not None

    def load_skill(self, name: str) -> str:
        if name.strip().lower() == READ_SKILL_NONE:
            self.read_calls.append({"name": READ_SKILL_NONE, "found": True})
            return "No procedure selected. Continue the request with the tools."
        skill, repaired = self.resolve(name)
        self.read_calls.append({"name": name, "found": skill is not None, "repaired": repaired})
        if skill is None:
            self.invalid_loads += 1
            self._emit("skill_load", {"skill_id": name, "name": None, "found": False})
            lines = "\n".join(f"- {item['name']}" for item in self.skills)
            return f'No procedure is named "{name}". The Reference procedures are:\n{lines}'
        if all(item["uuid"] != skill["uuid"] for item in self.loaded):
            self.loaded.append(skill)
        self._emit("skill_load", {"skill_id": skill["uuid"], "name": skill["name"], "found": True})
        return str(skill["markdown"])

    def record_use(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(
            "A snapshot Skill has no search ID, so the harness cannot record its use"
        )

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(kind, data)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Event sink failed for %s: %s", kind, type(exc).__name__)
