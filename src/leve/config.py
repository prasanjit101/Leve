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

from dotenv import load_dotenv

from leve.errors import ConfigError

CONFIG_FILENAME = "leve.toml"

# Adapter names resolved in leve.persistence. New backends extend these sets
# without changing the resolution code (Open/Closed).
_CHECKPOINTER_KINDS = {"sqlite", "memory", "postgres"}
_STORE_KINDS = {"sqlite", "memory", "postgres"}


@dataclass(frozen=True)
class PersistenceConfig:
    """Which durability backends to use and how to reach them."""

    checkpointer: str = "sqlite"
    store: str = "memory"
    # Local SQLite checkpoint file (used when checkpointer == "sqlite").
    sqlite_path: str = ".leve/checkpoints.sqlite"
    # Local SQLite long-term-memory file (used when store == "sqlite"). Kept
    # separate from the checkpoint DB so durability and memory don't share a file.
    store_sqlite_path: str = ".leve/store.sqlite"
    # Postgres connection string (used when checkpointer/store == "postgres").
    postgres_url: str | None = None

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
        if "postgres" in (self.checkpointer, self.store) and not self.postgres_url:
            raise ConfigError(
                "Postgres backend selected but no postgres_url is set "
                "(leve.toml [persistence] postgres_url or env LEVE_POSTGRES_URL)."
            )


@dataclass(frozen=True)
class TracingConfig:
    """Observability backend selection (SPEC §5.4, §11)."""

    provider: str = "langsmith"  # langsmith | otel | none
    project: str | None = None


# Sandbox adapters by isolation tier (SPEC §5.2). microsandbox is the default;
# subprocess is the no-isolation dev escape hatch.
_SANDBOX_ADAPTERS = {"microsandbox", "subprocess", "os_native", "docker", "e2b", "modal"}


@dataclass(frozen=True)
class SandboxLimits:
    """Resource quotas for untrusted agent code (SPEC §5.2, §11).

    Secure defaults: 1 vCPU / 1 GB RAM / 1 GB disk / 120 s per command, egress
    denied. An agent may tighten these but the project ceiling is enforced here.
    """

    memory_mb: int = 1024
    vcpus: int = 1
    timeout_sec: int = 120
    disk_mb: int = 1024
    network: str = "deny"  # deny | allow
    network_allow: tuple[str, ...] = ()
    # Cap captured command output so a runaway command can't flood the model
    # context or bloat the checkpointer. Output beyond this is truncated.
    max_output_bytes: int = 256 * 1024


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox adapter selection and quotas."""

    adapter: str = "microsandbox"
    limits: SandboxLimits = field(default_factory=SandboxLimits)

    def validate(self) -> None:
        if self.adapter not in _SANDBOX_ADAPTERS:
            raise ConfigError(
                f"Unknown sandbox adapter '{self.adapter}'. "
                f"Supported: {sorted(_SANDBOX_ADAPTERS)}."
            )
        if self.limits.network not in {"deny", "allow"}:
            raise ConfigError(
                f"sandbox network must be 'deny' or 'allow', got '{self.limits.network}'."
            )


@dataclass(frozen=True)
class CredentialsConfig:
    """Credential broker selection (SPEC §5.7, §11)."""

    broker: str = "oauth_store"  # static | oauth_store | token_exchange


@dataclass(frozen=True)
class DeployConfig:
    """Deployment target (SPEC §10, §11)."""

    target: str = "langgraph-platform"  # langgraph-platform | docker
    # Public base URL of the served app (used in emitted crontab schedule calls).
    base_url: str = "http://localhost:8000"


@dataclass(frozen=True)
class LeveConfig:
    """Resolved project configuration."""

    project_dir: Path
    root: str = "agent"
    default_model: str = "anthropic:claude-opus-4-8"
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    deploy: DeployConfig = field(default_factory=DeployConfig)

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


def _load_project_dotenv(project_dir: Path) -> None:
    """Load ``project_dir/.env`` into the process environment if present.

    ``override=False`` keeps any value already exported in the real environment
    authoritative, so the precedence is: actual env > ``.env`` > config defaults.
    """

    dotenv_path = project_dir / ".env"
    if dotenv_path.is_file():
        load_dotenv(dotenv_path, override=False)


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

    # Load the project-local `.env` before resolving env overrides so a `.env`
    # behaves the same in local dev as it does on LangGraph Platform (which reads
    # the `.env` named in langgraph.json). The real environment always wins.
    _load_project_dotenv(config.project_dir)

    config = _apply_env_overrides(config)
    config.persistence.validate()
    config.sandbox.validate()
    return config


def _parse_config(path: Path) -> LeveConfig:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    agent = data.get("agent", {})
    persistence = data.get("persistence", {})
    tracing = data.get("tracing", {})
    sandbox = data.get("sandbox", {})
    limits = sandbox.get("limits", {})
    credentials = data.get("credentials", {})
    deploy = data.get("deploy", {})

    return LeveConfig(
        project_dir=path.parent.resolve(),
        root=agent.get("root", "agent"),
        default_model=agent.get("default_model", LeveConfig.default_model),
        persistence=PersistenceConfig(
            checkpointer=persistence.get("checkpointer", PersistenceConfig.checkpointer),
            store=persistence.get("store", PersistenceConfig.store),
            sqlite_path=persistence.get("sqlite_path", PersistenceConfig.sqlite_path),
            store_sqlite_path=persistence.get(
                "store_sqlite_path", PersistenceConfig.store_sqlite_path
            ),
            postgres_url=persistence.get("postgres_url", PersistenceConfig.postgres_url),
        ),
        tracing=TracingConfig(
            provider=tracing.get("provider", TracingConfig.provider),
            project=tracing.get("project", TracingConfig.project),
        ),
        sandbox=SandboxConfig(
            adapter=sandbox.get("adapter", SandboxConfig.adapter),
            limits=SandboxLimits(
                memory_mb=limits.get("memory_mb", SandboxLimits.memory_mb),
                vcpus=limits.get("vcpus", SandboxLimits.vcpus),
                timeout_sec=limits.get("timeout_sec", SandboxLimits.timeout_sec),
                disk_mb=limits.get("disk_mb", SandboxLimits.disk_mb),
                network=limits.get("network", SandboxLimits.network),
                network_allow=tuple(limits.get("network_allow", ())),
                max_output_bytes=limits.get("max_output_bytes", SandboxLimits.max_output_bytes),
            ),
        ),
        credentials=CredentialsConfig(broker=credentials.get("broker", CredentialsConfig.broker)),
        deploy=DeployConfig(
            target=deploy.get("target", DeployConfig.target),
            base_url=deploy.get("base_url", DeployConfig.base_url),
        ),
    )


def _apply_env_overrides(config: LeveConfig) -> LeveConfig:
    """Apply ``LEVE_*`` env vars over file/default values."""

    persistence = config.persistence
    if (ckpt := os.getenv("LEVE_CHECKPOINTER")) is not None:
        persistence = replace(persistence, checkpointer=ckpt)
    if (store := os.getenv("LEVE_STORE")) is not None:
        persistence = replace(persistence, store=store)
    if (pg := os.getenv("LEVE_POSTGRES_URL")) is not None:
        persistence = replace(persistence, postgres_url=pg)

    return replace(
        config,
        root=os.getenv("LEVE_AGENT_ROOT", config.root),
        default_model=os.getenv("LEVE_DEFAULT_MODEL", config.default_model),
        persistence=persistence,
    )
