"""Tests for tracing configuration and the new persistence/tracing config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from leve.config import LeveConfig, TracingConfig, load_config
from leve.errors import ConfigError
from leve.tracing import configure_tracing


def test_postgres_requires_url(tmp_path):
    (tmp_path / "leve.toml").write_text('[persistence]\ncheckpointer = "postgres"\n')
    with pytest.raises(ConfigError, match="postgres_url"):
        load_config(tmp_path)


def test_postgres_url_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LEVE_POSTGRES_URL", "postgresql://localhost/db")
    (tmp_path / "leve.toml").write_text(
        '[persistence]\ncheckpointer = "postgres"\nstore = "postgres"\n'
    )
    config = load_config(tmp_path)
    assert config.persistence.postgres_url == "postgresql://localhost/db"


def test_tracing_parsed(tmp_path):
    (tmp_path / "leve.toml").write_text('[tracing]\nprovider = "langsmith"\nproject = "p"\n')
    config = load_config(tmp_path)
    assert config.tracing.project == "p"


def test_configure_tracing_enables_langsmith(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "key")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    config = LeveConfig(
        project_dir=Path("."), tracing=TracingConfig(provider="langsmith", project="proj")
    )
    configure_tracing(config)
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "proj"


def test_configure_tracing_noop_without_api_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    config = LeveConfig(project_dir=Path("."), tracing=TracingConfig(provider="langsmith"))
    configure_tracing(config)
    assert "LANGSMITH_TRACING" not in os.environ


def test_configure_tracing_otel_enables_master_switch(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_OTEL_ENABLED", raising=False)
    config = LeveConfig(project_dir=Path("."), tracing=TracingConfig(provider="otel", project="p"))
    configure_tracing(config)
    # OTEL must turn tracing ON (the master switch), not just select export mode.
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_OTEL_ENABLED"] == "true"


def test_configure_tracing_otel_noop_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    config = LeveConfig(project_dir=Path("."), tracing=TracingConfig(provider="otel"))
    configure_tracing(config)
    assert "LANGSMITH_TRACING" not in os.environ
