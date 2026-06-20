"""Leve — a filesystem-first, durable agent framework built on LangGraph.

The public surface is intentionally tiny: you *describe* an agent as a directory
of files and Leve compiles it into a runnable LangGraph graph. The two symbols
most projects import directly live here; everything else is reached through the
``leve.tools``, ``leve.channels`` (etc.) subpackages mirroring the directory
convention described in ``SPEC.md``.
"""

from leve.agent import AgentSpec, CompactionConfig, define_agent
from leve.auth import Credential, Principal

__all__ = [
    "AgentSpec",
    "CompactionConfig",
    "Credential",
    "Principal",
    "define_agent",
    "__version__",
]

__version__ = "0.1.0"
