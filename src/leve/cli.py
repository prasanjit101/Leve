"""Backward-compatible shim — canonical home is :mod:`leve.serving.cli`.

This shim also registers ``leve.serving.cli`` under the legacy ``leve.cli``
key in ``sys.modules`` so that ``unittest.mock.patch("leve.cli.*")`` calls
made in tests affect the same module namespace that the implementation code
uses. Without this aliasing, patches on the shim namespace do not propagate
to the canonical module where the functions are actually called.
"""
import sys

import leve.serving.cli as _serving_cli  # noqa: E402

# Re-export all public symbols for static analysers / ``from leve.cli import X``
from leve.serving.cli import *  # noqa: F401,F403
from leve.serving.cli import (  # noqa: F401  explicit public re-exports
    app,
    channels_app,
    connections_app,
    _run_server_mode,  # noqa: F401  private symbol kept for tests/test_devlog.py
    _run_tui_mode,  # noqa: F401  private symbol kept for tests/test_devlog.py
)

# Alias this module to the canonical one so patches on ``leve.cli.*`` propagate.
sys.modules[__name__] = _serving_cli
