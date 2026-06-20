"""Graph assembly (SPEC §6, step 8).

Turns a :class:`~leve.loader.LoadedAgent` into a compiled LangGraph graph: a
LangChain v1 ``create_agent`` ReAct loop (``model → tools → model``) wired with
the agent's model, instructions, tools, skills, checkpointer and store.

Cross-cutting behaviour is expressed as **middleware** — the v1 mechanism that
each milestone extends without changing this assembly's shape:

* instructions templating → ``dynamic_prompt`` (M1)
* context compaction → ``SummarizationMiddleware`` (M2)
* human-in-the-loop approval → :class:`~leve.middleware.ApprovalMiddleware` (M2)
* model fallbacks → ``ModelFallbackMiddleware`` (M1)
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelFallbackMiddleware,
    SummarizationMiddleware,
)
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from leve.agent import CompactionConfig
from leve.instructions import make_prompt_middleware
from leve.loader import LoadedAgent
from leve.middleware import ApprovalMiddleware
from leve.models import build_model
from leve.runtime import LeveContext
from leve.skills import make_load_skill_tool


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
    if loaded.skills:
        tools.append(make_load_skill_tool(loaded.skills))
    middleware = _build_middleware(loaded, model)

    return create_agent(
        model,
        tools=tools,
        middleware=middleware,
        context_schema=LeveContext,
        checkpointer=loaded.spec.checkpointer or checkpointer,
        store=loaded.spec.store or store,
        name=loaded.name,
    )


def _build_middleware(loaded: LoadedAgent, model) -> list[AgentMiddleware]:
    """Assemble the middleware chain for a loaded agent.

    Order: instructions first (present on every model request), then compaction
    (folds history before the model sees it), then tool-approval gating, then
    model fallbacks wrapping the call.
    """

    middleware: list[AgentMiddleware] = []

    if loaded.instructions.strip():
        middleware.append(make_prompt_middleware(loaded.instructions))

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


def _build_summarization(compaction: CompactionConfig, model) -> SummarizationMiddleware:
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
    trigger = _degrade_if_fractional(compaction.trigger, has_profile, ("tokens", _FALLBACK_SUMMARY_TOKENS))
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
