"""Tests for durability backends — checkpointer and store factories (SPEC §5.1)."""

from __future__ import annotations

import pytest

from leve.config import load_config
from leve.errors import ConfigError
from leve.persistence import open_checkpointer, open_store


def _config(tmp_path, **persistence):
    body = "".join(f'{k} = "{v}"\n' for k, v in persistence.items())
    (tmp_path / "leve.toml").write_text(f"[persistence]\n{body}")
    return load_config(tmp_path)


async def test_sqlite_store_roundtrip(tmp_path):
    """The SQLite store persists long-term memory to its own file (SPEC §5.1)."""

    config = _config(tmp_path, checkpointer="memory", store="sqlite")
    async with open_store(config) as store:
        await store.aput(("tenant", "user"), "fact", {"value": 42})
        item = await store.aget(("tenant", "user"), "fact")
        assert item is not None and item.value == {"value": 42}

    # The store's file is distinct from the checkpoint DB.
    assert (tmp_path / ".leve" / "store.sqlite").exists()


async def test_sqlite_store_survives_reopen(tmp_path):
    """Data written in one session is readable after the store is reopened."""

    config = _config(tmp_path, checkpointer="memory", store="sqlite")
    async with open_store(config) as store:
        await store.aput(("ns",), "k", {"v": "durable"})
    async with open_store(config) as store:
        item = await store.aget(("ns",), "k")
        assert item is not None and item.value == {"v": "durable"}


async def test_sqlite_checkpointer_and_store_use_separate_files(tmp_path):
    config = _config(tmp_path, checkpointer="sqlite", store="sqlite")
    async with open_checkpointer(config), open_store(config):
        pass
    leve_dir = tmp_path / ".leve"
    assert (leve_dir / "checkpoints.sqlite").exists()
    assert (leve_dir / "store.sqlite").exists()


def test_sqlite_store_is_a_valid_config(tmp_path):
    config = _config(tmp_path, store="sqlite")
    assert config.persistence.store == "sqlite"  # no postgres_url required


def test_invalid_store_raises(tmp_path):
    with pytest.raises(ConfigError):
        _config(tmp_path, store="nope")
