"""Tests for compaction (summarization) wiring."""

from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware import SummarizationMiddleware

from leve.agent import CompactionConfig, define_agent
from leve.graph import _build_middleware
from leve.loader import LoadedAgent
from leve.testing import FakeChatModel


def _loaded(spec) -> LoadedAgent:
    return LoadedAgent(name="a", path=Path("."), spec=spec)


def test_compaction_on_by_default():
    model = FakeChatModel(responses=["x"])
    middleware = _build_middleware(_loaded(define_agent(model=model)), model)
    assert any(isinstance(m, SummarizationMiddleware) for m in middleware)


def test_compaction_can_be_disabled():
    model = FakeChatModel(responses=["x"])
    spec = define_agent(model=model, compaction=CompactionConfig(enabled=False))
    middleware = _build_middleware(_loaded(spec), model)
    assert not any(isinstance(m, SummarizationMiddleware) for m in middleware)


def test_fractional_keep_degrades_for_profileless_model():
    """A fractional keep must not crash compile on a model without a profile."""

    model = FakeChatModel(responses=["x"])
    spec = define_agent(
        model=model,
        compaction=CompactionConfig(trigger=("tokens", 5000), keep=("fraction", 0.3)),
    )
    middleware = _build_middleware(_loaded(spec), model)  # must not raise
    assert any(isinstance(m, SummarizationMiddleware) for m in middleware)


def test_string_compaction_model_is_resolved():
    """A provider-string summary model is resolved so its profile is honoured."""

    model = FakeChatModel(responses=["x"])
    spec = define_agent(
        model=model, compaction=CompactionConfig(model="anthropic:claude-opus-4-8")
    )
    middleware = _build_middleware(_loaded(spec), model)  # must not raise
    assert any(isinstance(m, SummarizationMiddleware) for m in middleware)
