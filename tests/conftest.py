"""Shared test fixtures and helpers."""

from __future__ import annotations

import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field

from leve.config import load_config
from leve.core.agent import define_agent
from leve.core.graph import build_graph
from leve.loader import LoadedAgent
from leve.serving.session import AgentRuntime
from leve.tools import ToolSpec, define_tool

DEFAULT_TOML = """\
[agent]
root = "agent"
default_model = "anthropic:claude-opus-4-8"

[persistence]
checkpointer = "memory"
store = "memory"
"""


@pytest.fixture
def write_project():
    """Return a helper that writes a project tree to disk and returns its config."""

    def _write(
        root: Path,
        *,
        agent_py: str,
        instructions: str = "You are helpful.",
        tools: dict[str, str] | None = None,
        extra_files: dict[str, str] | None = None,
        leve_toml: str = DEFAULT_TOML,
    ):
        (root / "agent" / "tools").mkdir(parents=True, exist_ok=True)
        (root / "leve.toml").write_text(leve_toml)
        (root / "agent" / "agent.py").write_text(textwrap.dedent(agent_py))
        (root / "agent" / "instructions.md").write_text(instructions)
        for name, body in (tools or {}).items():
            (root / "agent" / "tools" / f"{name}.py").write_text(textwrap.dedent(body))
        for rel, body in (extra_files or {}).items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(body))
        return load_config(root)

    return _write


@pytest.fixture
def echo_tool() -> ToolSpec:
    """A simple synchronous tool that echoes its input."""

    class EchoInput(BaseModel):
        text: str = Field(description="Text to echo back.")

    @define_tool(description="Echo the given text.", input_schema=EchoInput)
    def echo(text: str) -> str:
        return f"echo:{text}"

    return echo


@pytest.fixture
def make_loaded():
    """Build an in-memory LoadedAgent (lets tests hold the model instance)."""

    def _make(
        model: Any,
        *,
        instructions: str = "",
        tools: tuple[ToolSpec, ...] = (),
        name: str = "agent",
        recursion_limit: int = 25,
    ) -> LoadedAgent:
        spec = define_agent(model=model, recursion_limit=recursion_limit)
        return LoadedAgent(
            name=name,
            path=Path("."),
            spec=spec,
            instructions=instructions,
            tools=tools,
        )

    return _make


@asynccontextmanager
async def runtime_for(loaded: LoadedAgent):
    """Yield an AgentRuntime backed by in-memory durability."""

    graph = build_graph(loaded, checkpointer=MemorySaver(), store=InMemoryStore())
    yield AgentRuntime(graph, loaded)


async def collect(aiter) -> list[dict]:
    """Drain an async event iterator into a list."""

    return [event async for event in aiter]
