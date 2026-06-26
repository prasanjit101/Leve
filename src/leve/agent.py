"""Backward-compatible shim — canonical home is :mod:`leve.core.agent`."""
from leve.core.agent import *  # noqa: F401,F403
from leve.core.agent import (  # noqa: F401  explicit public re-exports
    AgentSpec,
    CompactionConfig,
    TriggerClause,
    define_agent,
)
