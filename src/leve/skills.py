"""Skills — ``agent/skills/<name>.md`` (SPEC §4.4).

A skill is markdown with YAML frontmatter (a ``description``). Leve exposes all
skills through a single synthetic ``load_skill(name)`` tool: only the skill
*descriptions* are given to the model up front (in the tool's description), and a
skill's full body enters context only when the model chooses to load it — keeping
the base prompt small while making domain knowledge available on demand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from leve.errors import LoaderError

# Matches a leading YAML frontmatter block delimited by --- lines.
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class SkillSpec:
    """A discovered skill: its name, one-line description, and full body."""

    name: str
    description: str
    body: str


def parse_skill(path: Path) -> SkillSpec:
    """Parse a skill markdown file into a :class:`SkillSpec`."""

    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        raise LoaderError(f"Skill {path} is missing YAML frontmatter (--- ... ---).")

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise LoaderError(f"Skill {path} has invalid frontmatter: {exc}") from exc

    if not isinstance(meta, dict):
        raise LoaderError(
            f"Skill {path} frontmatter must be a YAML mapping, got {type(meta).__name__}."
        )

    description = (meta.get("description") or "").strip()
    if not description:
        raise LoaderError(f"Skill {path} frontmatter must include a 'description'.")

    return SkillSpec(
        name=path.stem, description=description, body=match.group(2).strip()
    )


def make_load_skill_tool(skills: tuple[SkillSpec, ...]) -> StructuredTool:
    """Build the synthetic ``load_skill`` tool exposing the skill catalog."""

    by_name = {skill.name: skill for skill in skills}
    catalog = "\n".join(f"- {s.name}: {s.description}" for s in skills)

    class LoadSkillInput(BaseModel):
        name: str = Field(description="The name of the skill to load.")

    def load_skill(name: str) -> str:
        skill = by_name.get(name)
        if skill is None:
            return f"Unknown skill '{name}'. Available: {', '.join(by_name) or 'none'}."
        return skill.body

    return StructuredTool.from_function(
        func=load_skill,
        name="load_skill",
        description=(
            "Load the full content of a skill by name. Load a skill before acting "
            "on the knowledge it covers. Available skills:\n" + catalog
        ),
        args_schema=LoadSkillInput,
    )
