"""Backward-compatible shim — canonical home is :mod:`leve.core.skills`."""
from leve.core.skills import *  # noqa: F401,F403
from leve.core.skills import (  # noqa: F401  explicit public re-exports
    SkillSpec,
    make_load_skill_tool,
    parse_skill,
)
