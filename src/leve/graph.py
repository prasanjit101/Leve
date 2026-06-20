"""Graph assembly (SPEC §6, step 8).

Turns a :class:`~leve.loader.LoadedAgent` into a compiled LangGraph graph: a
LangChain v1 ``create_agent`` ReAct loop (``model → tools → model``) wired with
the agent's model, instructions, tools, checkpointer and store. Cross-cutting
behaviour is expressed as **middleware** — the v1 mechanism that later milestones
extend (summarization/compaction in M2, human-in-the-loop approval in M2, the
principal-injection wrap in M5) without touching this assembly's shape.

This is the one module that knows the ``create_agent`` shape; everything
upstream is plain data, everything downstream drives the compiled graph.
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from leve.instructions import make_prompt_middleware
from leve.loader import LoadedAgent
from leve.models import build_model
from leve.runtime import LeveContext


def build_graph(
    loaded: LoadedAgent,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
) -> CompiledStateGraph:
    """Compile a loaded agent into a runnable graph.

    A per-agent ``checkpointer``/``store`` set on the descriptor (SPEC §4.1)
    overrides the project-level backend passed in here.
    """

    model = build_model(loaded.spec)
    tools = [tool.build() for tool in loaded.tools]
    middleware = _build_middleware(loaded)

    return create_agent(
        model,
        tools=tools,
        middleware=middleware,
        context_schema=LeveContext,
        checkpointer=loaded.spec.checkpointer or checkpointer,
        store=loaded.spec.store or store,
        name=loaded.name,
    )


def _build_middleware(loaded: LoadedAgent) -> list[AgentMiddleware]:
    """Assemble the middleware chain for a loaded agent.

    Order matters: the prompt middleware runs first so instructions are present
    on every model request; fallbacks wrap the model call. Later milestones
    append summarization and approval middleware here.
    """

    middleware: list[AgentMiddleware] = []
    if loaded.instructions.strip():
        middleware.append(make_prompt_middleware(loaded.instructions))
    if loaded.spec.fallbacks:
        middleware.append(ModelFallbackMiddleware(*loaded.spec.fallbacks))
    return middleware
