"""Tracing configuration (SPEC §5.4, §11).

LangChain/LangGraph auto-emit a trace per run when the LangSmith env vars are
present. Leve's job is small: translate the ``[tracing]`` section of ``leve.toml``
into the right environment so the user configures observability in one place.
We never overwrite an explicit env var the user already set — config provides
defaults, the environment wins.
"""

from __future__ import annotations

import os

from leve.config import LeveConfig


def configure_tracing(config: LeveConfig) -> None:
    """Apply the project's tracing config to the process environment."""

    tracing = config.tracing

    if tracing.provider == "langsmith":
        # Only enable if the user supplied credentials; otherwise leave tracing
        # off so local dev doesn't error on a missing API key.
        if os.getenv("LANGSMITH_API_KEY"):
            _enable(tracing.project)
    elif tracing.provider == "otel":
        # OTEL_ENABLED only selects the *export mode*; tracing itself is still
        # gated by LANGSMITH_TRACING. Enable both, and only when an exporter
        # endpoint (or key) is configured so local dev doesn't activate blindly.
        if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("LANGSMITH_API_KEY"):
            os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
            _enable(tracing.project)
    # provider == "none" (or anything else): leave the environment untouched.


def _enable(project: str | None) -> None:
    """Turn tracing on and set the project (without clobbering explicit env)."""

    os.environ.setdefault("LANGSMITH_TRACING", "true")
    if project:
        os.environ.setdefault("LANGSMITH_PROJECT", project)
