"""Graph assembly (SPEC §6, step 8).

Turns a :class:`~leve.loader.LoadedAgent` into a compiled LangGraph graph: a
LangChain v1 ``create_agent`` ReAct loop (``model → tools → model``) wired with
the agent's model, instructions, tools, skills, checkpointer and store.

Cross-cutting behaviour is expressed as **middleware** — the v1 mechanism that
each milestone extends without changing this assembly's shape:

* instructions templating → ``dynamic_prompt`` (M1)
* context compaction → ``SummarizationMiddleware`` (M2)
* human-in-the-loop approval → :class:`~leve.core.middleware.ApprovalMiddleware` (M2)
* model fallbacks → ``ModelFallbackMiddleware`` (M1)
"""

from __future__ import annotations

from collections.abc import Callable

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelFallbackMiddleware,
    SummarizationMiddleware,
)
from langchain.chat_models import init_chat_model
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from leve.core.agent import CompactionConfig
from leve.core.instructions import make_prompt_middleware
from leve.core.middleware import ApprovalMiddleware, PrincipalMiddleware
from leve.core.models import build_model
from leve.core.runtime import LeveContext
from leve.core.skills import make_load_skill_tool
from leve.core.subagents import make_delegation_tool
from leve.loader import LoadedAgent

# Resolves the runtime-discovered tools (connection + sandbox tools) for a given
# agent node. Supplied by the async app layer, since discovery is async and the
# sync graph builder cannot do it. Threaded through the subagent recursion so a
# subagent's connections/sandbox reach its subgraph too (not just the root).
ExtraToolsResolver = Callable[[LoadedAgent], list[BaseTool]]


def build_graph(
    loaded: LoadedAgent,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    extra_tools_for: ExtraToolsResolver | None = None,
) -> CompiledStateGraph:
    """Compile a loaded agent (and its subagents) into a runnable graph.

    A per-agent ``checkpointer``/``store`` set on the descriptor (SPEC §4.1)
    overrides the project-level backend passed in here. ``extra_tools_for`` maps
    each agent node to its runtime-discovered tools; it is applied to the root
    *and* recursively to every subagent so declared connections/sandbox compose.
    """

    resolve = extra_tools_for or (lambda _agent: [])
    resolved_checkpointer = loaded.spec.checkpointer or checkpointer
    resolved_store = loaded.spec.store or store

    model = build_model(loaded.spec)
    tools: list[BaseTool] = [tool.build() for tool in loaded.tools]
    if loaded.skills:
        tools.append(make_load_skill_tool(loaded.skills))
    tools.extend(
        _build_subagent_tools(loaded, resolved_checkpointer, resolved_store, resolve)
    )
    tools.extend(resolve(loaded))

    middleware = _build_middleware(loaded, model)

    return create_agent(
        model,
        tools=tools,
        middleware=middleware,
        context_schema=LeveContext,
        checkpointer=resolved_checkpointer,
        store=resolved_store,
        name=loaded.name,
    )


def _build_subagent_tools(
    loaded: LoadedAgent,
    checkpointer: BaseCheckpointSaver | None,
    store: BaseStore | None,
    extra_tools_for: ExtraToolsResolver,
) -> list[BaseTool]:
    """Compile each subagent into a subgraph and wrap it as a delegation tool."""

    tools: list[BaseTool] = []
    for sub in loaded.subagents:
        subgraph = build_graph(
            sub,
            checkpointer=checkpointer,
            store=store,
            extra_tools_for=extra_tools_for,
        )
        tools.append(
            make_delegation_tool(
                sub.name,
                sub.spec.description,
                subgraph,
                recursion_limit=sub.spec.recursion_limit,
            )
        )
    return tools


def _build_middleware(loaded: LoadedAgent, model) -> list[AgentMiddleware]:
    """Assemble the middleware chain for a loaded agent.

    Order: instructions first (present on every model request), then compaction
    (folds history before the model sees it), then tool-approval gating, then
    model fallbacks wrapping the call.
    """

    middleware: list[AgentMiddleware] = []

    if loaded.instructions.strip():
        middleware.append(make_prompt_middleware(loaded.instructions))

    # Always bind the caller principal around tool calls (outermost tool wrapper),
    # so injected-principal tools and consent interrupts work for every agent.
    middleware.append(PrincipalMiddleware())

    compaction = loaded.spec.compaction or CompactionConfig()  # None == auto-on
    if compaction.enabled:
        middleware.append(_build_summarization(compaction, model))

    approval = ApprovalMiddleware(loaded.tools)
    if approval.gated_tools:
        middleware.append(approval)

    if loaded.spec.fallbacks:
        middleware.append(ModelFallbackMiddleware(*loaded.spec.fallbacks))

    return middleware


# Absolute fallback when a fractional trigger is requested but the model exposes
# no context-window profile (custom/fake models). Conservative enough that it
# never fires on small conversations.
_FALLBACK_SUMMARY_TOKENS = 100_000


def _build_summarization(
    compaction: CompactionConfig, model
) -> SummarizationMiddleware:
    """Build summarization, degrading fractional clauses when no profile exists.

    Both ``trigger`` and ``keep`` may be fractional, and either fractional clause
    needs the model's ``max_input_tokens`` profile (real providers have it;
    fakes/custom models may not). A string ``compaction.model`` is resolved first
    so a real model behind a provider string keeps its fractional clauses. Rather
    than crash at compile, fractional clauses degrade to absolute defaults for
    genuinely profile-less models.
    """

    summary_model = compaction.model or model
    if isinstance(summary_model, str):
        summary_model = init_chat_model(summary_model)

    has_profile = _has_token_profile(summary_model)
    trigger = _degrade_if_fractional(
        compaction.trigger, has_profile, ("tokens", _FALLBACK_SUMMARY_TOKENS)
    )
    keep = _degrade_if_fractional(compaction.keep, has_profile, ("messages", 20))
    return SummarizationMiddleware(model=summary_model, trigger=trigger, keep=keep)


def _degrade_if_fractional(clause, has_profile: bool, fallback):
    """Replace a fractional clause with an absolute fallback when no profile."""

    if clause[0] == "fraction" and not has_profile:
        return fallback
    return clause


def _has_token_profile(model) -> bool:
    profile = getattr(model, "profile", None)
    return bool(profile and profile.get("max_input_tokens"))
