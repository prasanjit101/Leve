"""Backward-compatible shim — canonical home is :mod:`leve.core.graph`."""
from leve.core.graph import *  # noqa: F401,F403
from leve.core.graph import (  # noqa: F401  explicit public re-exports
    ExtraToolsResolver,
    _build_middleware,  # noqa: F401  private symbol kept for tests/test_compaction.py
    build_graph,
)
