"""Tests for project assembly (inspect_project) and scaffolding."""

from __future__ import annotations

import pytest

from leve.app import inspect_project
from leve.errors import LeveError
from leve.scaffold import scaffold_project

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel

agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""

ECHO_TOOL = """\
from pydantic import BaseModel, Field
from leve.tools import define_tool

class In(BaseModel):
    text: str = Field(description="t")

@define_tool(description="Echo.", input_schema=In)
def echo(text: str) -> str:
    return text
"""


def test_inspect_project_summary(tmp_path, write_project):
    config = write_project(
        tmp_path, agent_py=AGENT_PY, instructions="Hi", tools={"echo": ECHO_TOOL}
    )
    summary = inspect_project(config)
    assert summary["agent"] == "agent"
    assert summary["tools"] == ["echo"]
    assert summary["instructions"] is True


def test_scaffold_creates_runnable_layout(tmp_path):
    created = scaffold_project(
        tmp_path / "myagent", name="myagent", model="anthropic:x"
    )
    names = {p.name for p in created}
    assert {
        "leve.toml",
        ".env.example",
        "agent.py",
        "instructions.md",
        "current_time.py",
    } <= names
    assert (tmp_path / "myagent" / "agent" / "agent.py").is_file()
    assert (tmp_path / "myagent" / ".env.example").is_file()


def test_scaffold_refuses_nonempty_dir(tmp_path):
    target = tmp_path / "x"
    target.mkdir()
    (target / "f.txt").write_text("hi")
    with pytest.raises(LeveError):
        scaffold_project(target, name="x", model="anthropic:x")
