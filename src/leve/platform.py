"""Deployment entrypoints (SPEC §10).

LangGraph Platform imports a module-level ``graph``; the self-host container runs
the ASGI ``app``. Both are built lazily via module ``__getattr__`` so importing
this module never requires a project in the cwd — they resolve only when
accessed in a real deployment (Platform/uvicorn), where the project is present.

The Platform ``graph`` is built synchronously and therefore omits async
runtime-discovered tools (connections) and sandbox tools; the self-host ``app``
(which runs ``build_runtime``) includes them.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "app":
        from leve.config import load_config
        from leve.server import create_app

        return create_app(load_config())

    if name == "graph":
        from leve.config import load_config
        from leve.graph import build_graph
        from leve.loader import load_project

        config = load_config()
        return build_graph(load_project(config))

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
