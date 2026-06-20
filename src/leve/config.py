"""Project configuration — ``leve.toml`` (SPEC §11).

The directory tree *is* the configuration; ``leve.toml`` only selects the
adapters (checkpointer, store, …) and project-wide defaults. Environments
override via env vars so dev → preview → prod differ only in config, never in
agent code. Backends are referenced by *name* here and resolved to concrete
adapters in ``leve.persistence`` — the Dependency-Inversion seam from SPEC §12.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from leve.errors import ConfigError

CONFIG_FILENAME = "leve.toml"

# Backends implemented in M1. Postgres/Redis land in M2 and extend these sets
# without changing the resolution code (Open/Closed).
_CHECKPOINTER_KINDS = {"sqlite", "memory"}
_STORE_KINDS = {"memory"}


@dataclass(frozen=True)
class PersistenceConfig:
    """Which durability backends to use and how to reach them."""

    checkpointer: str = "sqlite"
    store: str = "memory"
    # Local SQLite checkpoint file (used when checkpointer == "sqlite").
    sqlite_path: str = ".leve/checkpoints.sqlite"

    def validate(self) -> None:
        if self.checkpointer not in _CHECKPOINTER_KINDS:
            raise ConfigError(
                f"Unknown checkpointer '{self.checkpointer}'. "
                f"Supported: {sorted(_CHECKPOINTER_KINDS)}."
            )
        if self.store not in _STORE_KINDS:
            raise ConfigError(
                f"Unknown store '{self.store}'. Supported: {sorted(_STORE_KINDS)}."
            )


@dataclass(frozen=True)
class LeveConfig:
    """Resolved project configuration."""

    project_dir: Path
    root: str = "agent"
    default_model: str = "anthropic:claude-opus-4-8"
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)

    @property
    def agent_dir(self) -> Path:
        """Absolute path to the agent directory."""

        return (self.project_dir / self.root).resolve()


def find_config_file(start: Path) -> Path | None:
    """Search ``start`` and its ancestors for ``leve.toml``."""

    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(start_dir: Path | str | None = None) -> LeveConfig:
    """Load and validate project config, applying env overrides.

    When no ``leve.toml`` is found, sensible local-dev defaults are used with the
    project directory anchored at ``start_dir`` (cwd by default).
    """

    start = Path(start_dir or Path.cwd())
    config_path = find_config_file(start)

    if config_path is None:
        config = LeveConfig(project_dir=start.resolve())
    else:
        config = _parse_config(config_path)

    config = _apply_env_overrides(config)
    config.persistence.validate()
    return config


def _parse_config(path: Path) -> LeveConfig:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    agent = data.get("agent", {})
    persistence = data.get("persistence", {})

    return LeveConfig(
        project_dir=path.parent.resolve(),
        root=agent.get("root", "agent"),
        default_model=agent.get("default_model", LeveConfig.default_model),
        persistence=PersistenceConfig(
            checkpointer=persistence.get("checkpointer", PersistenceConfig.checkpointer),
            store=persistence.get("store", PersistenceConfig.store),
            sqlite_path=persistence.get("sqlite_path", PersistenceConfig.sqlite_path),
        ),
    )


def _apply_env_overrides(config: LeveConfig) -> LeveConfig:
    """Apply ``LEVE_*`` env vars over file/default values."""

    persistence = config.persistence
    if (ckpt := os.getenv("LEVE_CHECKPOINTER")) is not None:
        persistence = replace(persistence, checkpointer=ckpt)
    if (store := os.getenv("LEVE_STORE")) is not None:
        persistence = replace(persistence, store=store)

    return replace(
        config,
        root=os.getenv("LEVE_AGENT_ROOT", config.root),
        default_model=os.getenv("LEVE_DEFAULT_MODEL", config.default_model),
        persistence=persistence,
    )
