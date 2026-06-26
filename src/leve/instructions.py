"""Backward-compatible shim — canonical home is :mod:`leve.core.instructions`."""
from leve.core.instructions import *  # noqa: F401,F403
from leve.core.instructions import (  # noqa: F401  explicit public re-exports
    make_prompt_middleware,
    render_instructions,
)
