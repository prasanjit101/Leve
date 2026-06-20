"""Application assembly — config → load → compile → runtime.

This is the single place that wires the pieces together, so the server, the TUI
host, and the eval/test harness never duplicate the open-backends-then-compile
dance. Durability backends are resources, so the runtime is delivered as an
async context manager: enter it to get a live :class:`AgentRuntime`, exit it to
close the checkpointer/store cleanly.
"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, AsyncIterator

from leve.config import LeveConfig
from leve.graph import build_graph
from leve.loader import LoadedAgent, load_project
from leve.persistence import open_checkpointer, open_store
from leve.session import AgentRuntime
from leve.tracing import configure_tracing


@asynccontextmanager
async def build_runtime(config: LeveConfig) -> AsyncIterator[AgentRuntime]:
    """Load the project and yield a live :class:`AgentRuntime`.

    Opens the configured checkpointer and store for the duration of the context
    and compiles the agent graph against them.
    """

    configure_tracing(config)
    loaded = load_project(config)
    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(open_checkpointer(config))
        store = await stack.enter_async_context(open_store(config))
        graph = build_graph(loaded, checkpointer=checkpointer, store=store)
        yield AgentRuntime(graph, loaded)


def inspect_project(config: LeveConfig) -> dict[str, Any]:
    """Load and compile the project without serving (backs ``leve build``).

    Returns a summary of the discovered components; raises ``LoaderError`` /
    ``ConfigError`` if the project does not compile, which makes this a CI-safe
    validation step.
    """

    loaded: LoadedAgent = load_project(config)
    # Compile the graph to validate model + tools + middleware assemble.
    build_graph(loaded)
    return {
        "agent": loaded.name,
        "model": loaded.spec.model if isinstance(loaded.spec.model, str) else "<instance>",
        "instructions": bool(loaded.instructions.strip()),
        "tools": [tool.name for tool in loaded.tools],
        "skills": [skill.name for skill in loaded.skills],
        "checkpointer": config.persistence.checkpointer,
        "store": config.persistence.store,
    }
