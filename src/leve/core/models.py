"""Model resolution (SPEC §3, §4.1).

Resolves an :class:`~leve.core.agent.AgentSpec`'s ``model`` reference to a concrete
LangChain chat model via the provider-agnostic ``init_chat_model``. A model
*instance* passed to ``define_agent`` is used as-is — the escape hatch for custom
configs and for tests (inject a fake model, no provider call).

Fallback chains are *not* applied here: in the ``create_agent`` world they are a
``ModelFallbackMiddleware`` assembled by :mod:`leve.core.graph`, which keeps model
construction a single responsibility.
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from leve.core.agent import AgentSpec


def build_model(spec: AgentSpec) -> BaseChatModel:
    """Resolve ``spec.model`` into a chat model instance."""

    if isinstance(spec.model, str):
        return init_chat_model(spec.model, **spec.model_options)
    # Already an instance — the caller owns its configuration; re-applying
    # model_options would be ambiguous, so they are intentionally ignored.
    return spec.model
