"""Tests for the convention-based loader."""

from __future__ import annotations

import pytest

from leve.errors import LoaderError
from leve.loader import load_project
from leve.testing import FakeChatModel

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel

agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""

ECHO_TOOL = """\
from pydantic import BaseModel, Field
from leve.tools import define_tool

class In(BaseModel):
    text: str = Field(description="text")

@define_tool(description="Echo.", input_schema=In)
def echo(text: str) -> str:
    return text
"""


def test_loads_agent_with_tools(tmp_path, write_project):
    config = write_project(
        tmp_path,
        agent_py=AGENT_PY,
        instructions="You are helpful.",
        tools={"echo": ECHO_TOOL},
    )
    loaded = load_project(config)

    assert loaded.name == "agent"
    assert isinstance(loaded.spec.model, FakeChatModel)
    assert loaded.instructions.strip() == "You are helpful."
    assert [t.name for t in loaded.tools] == ["echo"]


def test_missing_agent_py_raises(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=AGENT_PY)
    (tmp_path / "agent" / "agent.py").unlink()
    with pytest.raises(LoaderError):
        load_project(config)


def test_duplicate_tool_name_raises(tmp_path, write_project):
    config = write_project(
        tmp_path,
        agent_py=AGENT_PY,
        tools={"echo": ECHO_TOOL, "echo2": ECHO_TOOL},  # both define tool 'echo'
    )
    with pytest.raises(LoaderError, match="Duplicate tool name"):
        load_project(config)


def test_relative_imports_inside_agent_tree(tmp_path, write_project):
    """A tool importing from the agent package (from ..lib import …) must work."""

    tool = """\
        from pydantic import BaseModel
        from leve.tools import define_tool
        from ..lib.util import GREETING

        class In(BaseModel):
            pass

        @define_tool(description="Greet.", input_schema=In)
        def greet() -> str:
            return GREETING
    """
    config = write_project(
        tmp_path,
        agent_py=AGENT_PY,
        tools={"greet": tool},
        extra_files={"agent/lib/util.py": "GREETING = 'hello'\n"},
    )
    loaded = load_project(config)
    assert loaded.tools[0].build().invoke({}) == "hello"


def test_no_agent_spec_raises(tmp_path, write_project):
    config = write_project(tmp_path, agent_py="x = 1\n")
    with pytest.raises(LoaderError):
        load_project(config)
