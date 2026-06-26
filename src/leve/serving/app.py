"""Application assembly — config → load → compile → runtime.

This is the single place that wires the pieces together, so the server, the TUI
host, and the eval/test harness never duplicate the open-backends-then-compile
dance. Durability backends are resources, so the runtime is delivered as an
async context manager: enter it to get a live :class:`AgentRuntime`, exit it to
close the checkpointer/store cleanly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from langchain_core.tools import BaseTool

from leve.config import LeveConfig
from leve.connections import discover_tools
from leve.core.graph import build_graph
from leve.evals import EvalResult, EvalSpec, run_eval
from leve.loader import LoadedAgent, discovery, load_project
from leve.persistence import open_checkpointer, open_store
from leve.sandbox import create_sandbox, make_sandbox_tools
from leve.security.credentials import create_broker
from leve.serving.session import AgentRuntime
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

        # Resolve runtime tools (connections + sandbox) for every agent in the
        # tree — root and subagents alike — so declared capabilities compose.
        extra_tools = await _resolve_extra_tools(loaded, config, stack)
        graph = build_graph(
            loaded,
            checkpointer=checkpointer,
            store=store,
            extra_tools_for=lambda agent: extra_tools.get(id(agent), []),
        )
        yield AgentRuntime(graph, loaded, broker=create_broker(config.credentials))


async def _resolve_extra_tools(
    loaded: LoadedAgent, config: LeveConfig, stack: AsyncExitStack
) -> dict[int, list[BaseTool]]:
    """Discover connection + sandbox tools for every node in the agent tree.

    Done in the async app layer (discovery is async; sandboxes are resources
    closed on shutdown) and keyed by node identity so the sync graph builder can
    look each agent's tools up while recursing.
    """

    mapping: dict[int, list[BaseTool]] = {}

    async def walk(node: LoadedAgent) -> None:
        tools: list[BaseTool] = list(await discover_tools(node.connections))
        if node.spec.sandbox:
            sandbox = create_sandbox(config.sandbox)
            stack.push_async_callback(sandbox.close)
            tools.extend(make_sandbox_tools(sandbox))
        mapping[id(node)] = tools
        for sub in node.subagents:
            await walk(sub)

    await walk(loaded)
    return mapping


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
        "model": loaded.spec.model
        if isinstance(loaded.spec.model, str)
        else "<instance>",
        "instructions": bool(loaded.instructions.strip()),
        "tools": [tool.name for tool in loaded.tools],
        "skills": [skill.name for skill in loaded.skills],
        "connections": [c.name for c in loaded.connections],
        "subagents": [s.name for s in loaded.subagents],
        "sandbox": config.sandbox.adapter if loaded.spec.sandbox else None,
        "checkpointer": config.persistence.checkpointer,
        "store": config.persistence.store,
    }


def load_evals(config: LeveConfig) -> tuple[EvalSpec, ...]:
    """Discover ``evals/*.eval.py`` at the project root (SPEC §7)."""

    evals_dir = config.project_dir / "evals"
    if not evals_dir.is_dir():
        return ()
    specs: list[EvalSpec] = []
    for file in sorted(evals_dir.glob("*.eval.py")):
        module = discovery.import_standalone(file)
        specs.extend(discovery.collect_instances(module, EvalSpec))
    return tuple(specs)


async def run_evals(config: LeveConfig) -> list[EvalResult]:
    """Run every eval against a fresh runtime (isolated model state + threads)."""

    results: list[EvalResult] = []
    for spec in load_evals(config):
        async with build_runtime(config) as runtime:
            results.append(await run_eval(spec, runtime))
    return results
