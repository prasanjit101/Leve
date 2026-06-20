"""Tests for leve.toml parsing, defaults, and env overrides."""

from __future__ import annotations

import pytest

from leve.config import load_config
from leve.errors import ConfigError


def test_defaults_when_no_config(tmp_path):
    config = load_config(tmp_path)
    assert config.root == "agent"
    assert config.persistence.checkpointer == "sqlite"
    assert config.persistence.store == "memory"
    assert config.agent_dir == (tmp_path / "agent").resolve()


def test_parses_leve_toml(tmp_path):
    (tmp_path / "leve.toml").write_text(
        '[agent]\nroot = "src/agent"\ndefault_model = "anthropic:claude-opus-4-8"\n'
        '[persistence]\ncheckpointer = "memory"\nstore = "memory"\n'
    )
    config = load_config(tmp_path)
    assert config.root == "src/agent"
    assert config.persistence.checkpointer == "memory"


def test_config_found_in_parent_dir(tmp_path):
    (tmp_path / "leve.toml").write_text('[persistence]\ncheckpointer = "memory"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    config = load_config(nested)
    # project_dir anchors at the file location, not the start dir.
    assert config.project_dir == tmp_path.resolve()


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("LEVE_CHECKPOINTER", "memory")
    config = load_config(tmp_path)
    assert config.persistence.checkpointer == "memory"


def test_invalid_checkpointer_raises(tmp_path):
    (tmp_path / "leve.toml").write_text('[persistence]\ncheckpointer = "nope"\n')
    with pytest.raises(ConfigError):
        load_config(tmp_path)
