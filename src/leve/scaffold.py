"""Project scaffolding for ``leve init`` (SPEC §8).

Holds the file templates for a new project as data, keeping the CLI a thin
caller. Every scaffolded project is immediately runnable with ``leve dev``.
"""

from __future__ import annotations

from pathlib import Path

from leve.errors import LeveError

_LEVE_TOML = """\
[agent]
root = "agent"
default_model = "{model}"

[persistence]
checkpointer = "sqlite"          # sqlite | memory
store = "memory"

[credentials]
broker = "oauth_store"           # static | oauth_store | token_exchange

[tracing]
provider = "langsmith"
project = "{name}"
"""

_AGENT_PY = '''\
from leve import define_agent

# The model + config the agent runs on. Capabilities (tools, skills, …) are
# discovered from sibling files — see tools/.
agent = define_agent(
    model="{model}",
)
'''

_INSTRUCTIONS_MD = """\
You are a helpful assistant built with Leve.

Today is {{{{ current_date }}}}. Be concise and cite the tools you used.
"""

_EXAMPLE_TOOL = '''\
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from leve.tools import define_tool


class CurrentTimeInput(BaseModel):
    timezone_name: str = Field(
        default="UTC",
        description="IANA timezone name; only UTC is supported in this example.",
    )


@define_tool(
    description="Return the current UTC time as an ISO-8601 string.",
    input_schema=CurrentTimeInput,
)
async def current_time(timezone_name: str = "UTC") -> str:
    return datetime.now(timezone.utc).isoformat()
'''

_EXAMPLE_SKILL = """\
---
description: House style for answering. Load before composing a user-facing reply.
---
Keep answers short and direct. Lead with the answer, then the reasoning.
Prefer bullet points over paragraphs. Always name any tool you used.
"""

_GITIGNORE = """\
.leve/
__pycache__/
*.pyc
.env
"""

# Starter secrets template. Copy to `.env` (gitignored) and fill in — Leve loads
# it automatically for `leve dev`/`build`/`eval`, and LangGraph Platform reads the
# `.env` named in the emitted langgraph.json on deploy.
_ENV_EXAMPLE = """\
# Copy this file to `.env` and fill in your secrets. `.env` is gitignored.
# Real environment variables always take precedence over values set here.

# --- Model provider (set the key for whichever provider your model uses) ---
ANTHROPIC_API_KEY=
# OPENAI_API_KEY=
# GOOGLE_API_KEY=          # for leve[google] / google_genai:<model>

# --- Tracing (optional; [tracing] provider = "langsmith") ---
# LANGSMITH_API_KEY=

# --- Config overrides (optional; override leve.toml) ---
# LEVE_DEFAULT_MODEL=anthropic:claude-opus-4-8
# LEVE_CHECKPOINTER=sqlite
# LEVE_STORE=memory
# LEVE_POSTGRES_URL=

# --- Channel adapters (only if you add them via `leve channels add`) ---
# SLACK_SIGNING_SECRET=
# SLACK_BOT_TOKEN=
# DISCORD_PUBLIC_KEY=
# DISCORD_BOT_TOKEN=
"""


_SLACK_CHANNEL = '''\
from leve.channels import define_channel
from leve.channels.slack import slack_adapter

channel = define_channel(slack_adapter(signing_secret_env="SLACK_SIGNING_SECRET"))
'''

_DISCORD_CHANNEL = '''\
from leve.channels import define_channel
from leve.channels.discord import discord_adapter

channel = define_channel(discord_adapter(public_key_env="DISCORD_PUBLIC_KEY"))
'''

_MCP_CONNECTION = '''\
from leve.connections import define_mcp_connection

connection = define_mcp_connection(
    url="https://mcp.example.com/sse",
    transport="sse",
    description="Describe what this connection exposes.",
)
'''

_CHANNEL_TEMPLATES = {"slack": _SLACK_CHANNEL, "discord": _DISCORD_CHANNEL}


def scaffold_channel(agent_dir: Path, kind: str) -> Path:
    """Write ``agent/channels/<kind>.py`` from a built-in template."""

    template = _CHANNEL_TEMPLATES.get(kind)
    if template is None:
        raise LeveError(f"Unknown channel '{kind}'. Available: {sorted(_CHANNEL_TEMPLATES)}.")
    return _write_component(agent_dir / "channels" / f"{kind}.py", template)


def scaffold_connection(agent_dir: Path, name: str) -> Path:
    """Write ``agent/connections/<name>.py`` MCP connection stub."""

    return _write_component(agent_dir / "connections" / f"{name}.py", _MCP_CONNECTION)


def _write_component(path: Path, content: str) -> Path:
    if path.exists():
        raise LeveError(f"{path} already exists.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def scaffold_project(directory: Path, *, name: str, model: str) -> list[Path]:
    """Write a runnable starter project into ``directory``.

    Returns the list of created files. Refuses to overwrite a non-empty target.
    """

    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise LeveError(f"Directory {directory} already exists and is not empty.")

    files = {
        "leve.toml": _LEVE_TOML.format(model=model, name=name),
        ".gitignore": _GITIGNORE,
        ".env.example": _ENV_EXAMPLE,
        "agent/agent.py": _AGENT_PY.format(model=model),
        "agent/instructions.md": _INSTRUCTIONS_MD,
        "agent/tools/current_time.py": _EXAMPLE_TOOL,
        "agent/skills/answering-style.md": _EXAMPLE_SKILL,
    }

    created: list[Path] = []
    for rel, content in files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(path)
    return created
