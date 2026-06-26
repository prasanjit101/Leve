"""Backward-compatible shim — canonical home is :mod:`leve.serving.cli`.

This shim also registers ``leve.serving.cli`` under the legacy ``leve.cli``
key in ``sys.modules`` so that ``unittest.mock.patch("leve.cli.*")`` calls
made in tests affect the same module namespace that the implementation code
uses. Without this aliasing, patches on the shim namespace do not propagate
to the canonical module where the functions are actually called.
"""
import sys

import leve.serving.cli as _serving_cli  # noqa: E402

# Alias this module to the canonical one so ``from leve.cli import X`` resolves
# every symbol (public and private) and ``patch("leve.cli.*")`` mutates the same
# namespace the live commands read from. The canonical module IS leve.cli.
sys.modules[__name__] = _serving_cli
