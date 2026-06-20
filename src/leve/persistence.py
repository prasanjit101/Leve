"""Durability backends (SPEC §5.1, §11).

Checkpointers and stores are *adapters behind a name*: ``leve.toml`` picks one
by string and these factories resolve it to a concrete backend. The graph
depends only on the LangGraph ``BaseCheckpointSaver`` / ``BaseStore`` interfaces
(Dependency Inversion), so adding Postgres/Redis in M2 means adding a branch
here — not touching the loop.

Each factory yields an *async context manager* because real backends (SQLite
connections, Postgres pools) own resources that must be opened for the life of
the server and closed on shutdown. The server lifespan enters them via an
``AsyncExitStack``; in-process tests use the ``memory`` backends, which hold
nothing open.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from leve.config import LeveConfig
from leve.errors import ConfigError


@asynccontextmanager
async def open_checkpointer(config: LeveConfig) -> AsyncIterator[BaseCheckpointSaver]:
    """Open the configured checkpointer for the lifetime of the context."""

    kind = config.persistence.checkpointer
    if kind == "memory":
        yield MemorySaver()
    elif kind == "sqlite":
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        path = config.project_dir / config.persistence.sqlite_path
        path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
            await saver.setup()  # idempotent: creates checkpoint tables once
            yield saver
    else:  # pragma: no cover - validate() rejects unknown kinds earlier
        raise ConfigError(f"Unsupported checkpointer backend '{kind}'.")


@asynccontextmanager
async def open_store(config: LeveConfig) -> AsyncIterator[BaseStore]:
    """Open the configured long-term-memory store for the context's lifetime."""

    kind = config.persistence.store
    if kind == "memory":
        yield InMemoryStore()
    else:  # pragma: no cover - validate() rejects unknown kinds earlier
        raise ConfigError(f"Unsupported store backend '{kind}'.")
