"""Tests for the eval harness."""

from __future__ import annotations

from leve.app import run_evals

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
from langchain_core.messages import AIMessage
agent = define_agent(model=FakeChatModel(responses=[
    AIMessage(content="", tool_calls=[{"name":"echo","args":{"text":"hi"},"id":"c1"}]),
    "all done",
]))
"""

ECHO_TOOL = """\
from pydantic import BaseModel, Field
from leve.tools import define_tool

class In(BaseModel):
    text: str = Field(description="t")

@define_tool(description="Echo.", input_schema=In)
def echo(text: str) -> str:
    return f"echo:{text}"
"""

EVAL_FILE = """\
from leve.evals import define_eval
from leve.evals.expect import includes

@define_eval(description="passes")
async def good(t):
    await t.send("hi")
    t.completed()
    t.no_errors()
    t.called_tool("echo")
    t.check(t.reply, includes("done"))

@define_eval(description="fails")
async def bad(t):
    await t.send("hi")
    t.check(t.reply, includes("NOT PRESENT"))
"""


async def test_run_evals_reports_pass_and_fail(tmp_path, write_project):
    config = write_project(
        tmp_path,
        agent_py=AGENT_PY,
        tools={"echo": ECHO_TOOL},
        extra_files={"evals/suite.eval.py": EVAL_FILE},
    )
    results = {r.name: r for r in await run_evals(config)}

    assert results["good"].passed
    assert not results["bad"].passed
    assert results["bad"].error  # carries the assertion message


async def test_no_evals_returns_empty(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=AGENT_PY, tools={"echo": ECHO_TOOL})
    assert await run_evals(config) == []
