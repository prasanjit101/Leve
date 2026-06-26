"""Tests for subagent delegation."""

from __future__ import annotations

from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from leve.config import LeveConfig, SandboxConfig
from leve.core.agent import define_agent
from leve.core.graph import build_graph
from leve.errors import LoaderError
from leve.loader import LoadedAgent
from leve.serving.app import _resolve_extra_tools
from leve.testing import FakeChatModel
from leve.tools import define_tool
from tests.conftest import collect, runtime_for


def _subagent(
    name: str, response: str, *, description: str = "Does research."
) -> LoadedAgent:
    return LoadedAgent(
        name=name,
        path=Path("."),
        spec=define_agent(
            model=FakeChatModel(responses=[response]), description=description
        ),
    )


async def test_parent_delegates_and_gets_final_message():
    sub = _subagent("researcher", "research result")
    parent_model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delegate_to_researcher",
                        "args": {"task": "dig in"},
                        "id": "c1",
                    }
                ],
            ),
            "parent done",
        ]
    )
    parent = LoadedAgent(
        name="analyst",
        path=Path("."),
        spec=define_agent(model=parent_model),
        subagents=(sub,),
    )

    async with runtime_for(parent) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))

        results = [e for e in events if e["type"] == "tool.result"]
        assert results and results[0]["tool"] == "delegate_to_researcher"
        assert results[0]["output"] == "research result"  # only the final message
        assert (await rt.get_state(sid))["messages"][-1]["content"] == "parent done"


def test_subagent_without_description_raises():
    sub = _subagent("researcher", "x", description="")
    parent = LoadedAgent(
        name="analyst",
        path=Path("."),
        spec=define_agent(model=FakeChatModel(responses=["x"])),
        subagents=(sub,),
    )
    with pytest.raises(LoaderError, match="needs a description"):
        build_graph(parent)


async def test_subagent_sandbox_tools_are_resolved():
    """A subagent declaring sandbox=True must receive run_shell (not just the root)."""

    from pathlib import Path as _Path

    sub = LoadedAgent(
        name="runner",
        path=_Path("."),
        spec=define_agent(
            sandbox=True,
            description="Runs shell.",
            model=FakeChatModel(responses=["x"]),
        ),
    )
    parent = LoadedAgent(
        name="root",
        path=_Path("."),
        spec=define_agent(model=FakeChatModel(responses=["x"])),
        subagents=(sub,),
    )
    config = LeveConfig(
        project_dir=_Path("."), sandbox=SandboxConfig(adapter="subprocess")
    )
    async with AsyncExitStack() as stack:
        mapping = await _resolve_extra_tools(parent, config, stack)
        assert "run_shell" in [t.name for t in mapping[id(sub)]]
        assert mapping[id(parent)] == []  # root opted into nothing


async def test_subagent_interrupt_is_reported_not_swallowed():
    """A subagent that pauses on approval reports the pause instead of garbage."""

    @define_tool(description="act", needs_approval=lambda ti: True)
    def act() -> str:
        return "acted"

    sub = LoadedAgent(
        name="runner",
        path=Path("."),
        spec=define_agent(
            model=FakeChatModel(
                responses=[
                    AIMessage(
                        content="", tool_calls=[{"name": "act", "args": {}, "id": "s1"}]
                    ),
                    "unreached",
                ]
            ),
            description="Runs.",
        ),
        tools=(act,),
    )
    parent_model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delegate_to_runner", "args": {"task": "do"}, "id": "c1"}
                ],
            ),
            "parent done",
        ]
    )
    parent = LoadedAgent(
        name="root",
        path=Path("."),
        spec=define_agent(model=parent_model),
        subagents=(sub,),
    )

    async with runtime_for(parent) as rt:
        events = await collect(rt.run(rt.new_session_id(), "go"))
        results = [e for e in events if e["type"] == "tool.result"]
        assert results and "paused awaiting human input" in str(results[0]["output"])
