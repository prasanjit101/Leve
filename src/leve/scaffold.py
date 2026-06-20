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
