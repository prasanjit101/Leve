"""The agent descriptor — ``agent/agent.py``.

``define_agent`` returns a *thin descriptor* (``AgentSpec``). It deliberately
does **no** compilation: the loader (``leve.loader``) is the single place that
assembles model + instructions + tools + … into a runnable graph (SPEC §4.1,
§6). Keeping this as inert data — not a half-built graph — is what lets the same
descriptor be compiled with different checkpointers/stores per environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # avoid importing heavy langgraph/langchain types at module load
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.store.base import BaseStore

# A summarization trigger/keep clause: ("fraction", 0.8) | ("tokens", N) | ("messages", N).
TriggerClause = tuple[Literal["fraction", "tokens", "messages"], float]


@dataclass(frozen=True)
class CompactionConfig:
    """Context-window summarization policy (SPEC §5.5).

    When a thread approaches the model's context window, a summarization step
    folds older turns into a running summary, bounding token cost. Maps to
    LangChain's ``SummarizationMiddleware``.
    """

    enabled: bool = True
    # Summarize when this clause is met; fraction is of the model's context window.
    trigger: TriggerClause = ("fraction", 0.8)
    # How much recent conversation to retain verbatim after summarizing.
    keep: TriggerClause = ("messages", 20)
    # Model used to write the summary; defaults to the agent's own model.
    model: "str | BaseChatModel | None" = None


@dataclass(frozen=True)
class AgentSpec:
    """An immutable description of an agent's model + run configuration.

    Capabilities (tools, skills, subagents, connections, channels, schedules)
    are *not* fields here — they are discovered from sibling files in the agent
    directory by the loader. This descriptor only carries what cannot be
    inferred from the tree: the model and how the loop runs.
    """

    model: "str | BaseChatModel"
    fallbacks: tuple[str, ...] = ()
    description: str = ""
    model_options: dict[str, Any] = field(default_factory=dict)
    recursion_limit: int = 25
    # None means "auto" — a default CompactionConfig is applied at compile time.
    compaction: "CompactionConfig | None" = None
    # Opt-in: inject sandbox tools (run untrusted code) using the project adapter.
    sandbox: bool = False
    # Per-agent overrides; when ``None`` the project-level backend (leve.toml) is used.
    checkpointer: "BaseCheckpointSaver | None" = None
    store: "BaseStore | None" = None


def define_agent(
    model: "str | BaseChatModel",
    *,
    fallbacks: "list[str] | None" = None,
    description: str = "",
    model_options: "dict[str, Any] | None" = None,
    recursion_limit: int = 25,
    compaction: "CompactionConfig | None" = None,
    sandbox: bool = False,
    checkpointer: "BaseCheckpointSaver | None" = None,
    store: "BaseStore | None" = None,
) -> AgentSpec:
    """Describe an agent.

    Args:
        model: A ``provider:model`` string (resolved via ``init_chat_model``) or
            an instantiated LangChain chat model. A string is the common case;
            passing a model instance is the escape hatch for custom configs and
            for tests (inject a fake model — no provider call).
        fallbacks: Ordered ``provider:model`` strings tried if the primary model
            errors (gateway-style failover).
        description: Required when this agent is used as a subagent — it becomes
            the description of the auto-generated ``delegate_to_<name>`` tool.
        model_options: Extra model kwargs (``temperature``, ``max_tokens``, …).
        recursion_limit: LangGraph step ceiling per turn (guards runaway loops).
        compaction: Context-window summarization policy; ``None`` enables a
            sensible default (auto). Pass ``CompactionConfig(enabled=False)`` off.
        sandbox: When True, inject sandbox tools (run untrusted code/shell) using
            the project's configured sandbox adapter. Off by default.
        checkpointer: Override the project checkpointer for this agent.
        store: Override the project long-term-memory store for this agent.
    """

    return AgentSpec(
        model=model,
        fallbacks=tuple(fallbacks or ()),
        description=description,
        model_options=dict(model_options or {}),
        recursion_limit=recursion_limit,
        compaction=compaction,
        sandbox=sandbox,
        checkpointer=checkpointer,
        store=store,
    )
