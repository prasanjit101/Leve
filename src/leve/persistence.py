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
    elif kind == "postgres":
        AsyncPostgresSaver = _require_postgres("AsyncPostgresSaver")
        async with AsyncPostgresSaver.from_conn_string(
            _postgres_url(config)
        ) as saver:
            await saver.setup()
            yield saver
    else:  # pragma: no cover - validate() rejects unknown kinds earlier
        raise ConfigError(f"Unsupported checkpointer backend '{kind}'.")


@asynccontextmanager
async def open_store(config: LeveConfig) -> AsyncIterator[BaseStore]:
    """Open the configured long-term-memory store for the context's lifetime."""

    kind = config.persistence.store
    if kind == "memory":
        yield InMemoryStore()
    elif kind == "postgres":
        AsyncPostgresStore = _require_postgres("AsyncPostgresStore")
        async with AsyncPostgresStore.from_conn_string(_postgres_url(config)) as store:
            await store.setup()
            yield store
    else:  # pragma: no cover - validate() rejects unknown kinds earlier
        raise ConfigError(f"Unsupported store backend '{kind}'.")


def _postgres_url(config: LeveConfig) -> str:
    url = config.persistence.postgres_url
    if not url:  # pragma: no cover - validate() enforces this earlier
        raise ConfigError("Postgres backend selected but postgres_url is unset.")
    return url


def _require_postgres(symbol: str):
    """Import a Postgres backend class, with an actionable error if absent."""

    try:
        if symbol == "AsyncPostgresSaver":
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            return AsyncPostgresSaver
        from langgraph.store.postgres.aio import AsyncPostgresStore

        return AsyncPostgresStore
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise ConfigError(
            "Postgres backend requires the optional dependency. Install with "
            "`pip install 'leve[postgres]'`."
        ) from exc
