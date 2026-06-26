"""Backward-compatible shim — canonical home is :mod:`leve.core.models`."""
from leve.core.models import *  # noqa: F401,F403
from leve.core.models import build_model  # noqa: F401  explicit public re-export
