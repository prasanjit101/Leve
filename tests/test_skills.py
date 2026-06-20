"""Tests for skills parsing and the load_skill tool."""

from __future__ import annotations

import pytest

from leve.errors import LoaderError
from leve.loader import load_project
from leve.skills import SkillSpec, make_load_skill_tool, parse_skill

SKILL = "---\ndescription: How this team defines revenue.\n---\nRevenue is net of refunds.\n"

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""


def test_parse_skill(tmp_path):
    path = tmp_path / "revenue.md"
    path.write_text(SKILL)
    skill = parse_skill(path)
    assert skill.name == "revenue"
    assert skill.description == "How this team defines revenue."
    assert "net of refunds" in skill.body


def test_parse_skill_requires_frontmatter(tmp_path):
    path = tmp_path / "x.md"
    path.write_text("just a body, no frontmatter")
    with pytest.raises(LoaderError):
        parse_skill(path)


def test_parse_skill_requires_description(tmp_path):
    path = tmp_path / "x.md"
    path.write_text("---\nfoo: bar\n---\nbody")
    with pytest.raises(LoaderError):
        parse_skill(path)


def test_parse_skill_rejects_non_dict_frontmatter(tmp_path):
    path = tmp_path / "x.md"
    path.write_text("---\n- a\n- b\n---\nbody")  # a YAML list, not a mapping
    with pytest.raises(LoaderError, match="mapping"):
        parse_skill(path)


def test_load_skill_tool():
    tool = make_load_skill_tool((SkillSpec("revenue", "Revenue rules.", "Net of refunds."),))
    assert tool.name == "load_skill"
    assert "revenue" in tool.description  # catalog exposed up front
    assert tool.invoke({"name": "revenue"}) == "Net of refunds."
    assert "Unknown" in tool.invoke({"name": "missing"})


def test_loader_discovers_skills(tmp_path, write_project):
    config = write_project(
        tmp_path, agent_py=AGENT_PY, extra_files={"agent/skills/revenue.md": SKILL}
    )
    loaded = load_project(config)
    assert [s.name for s in loaded.skills] == ["revenue"]
