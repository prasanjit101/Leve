"""The loader — convention-based compilation of an agent directory (SPEC §6).

This package is the *single* place that walks the ``agent/`` tree and turns its
files into a runnable graph. M1 implements the core steps: parse ``agent.py``,
read ``instructions.md``, import ``tools/*.py``. Skills, connections, subagents
and sandbox tools are later milestones — each adds a discovery step here without
changing the steps that already exist (Open/Closed).

Loading is split in two so discovery stays pure and runtime concerns stay out:

* :func:`load_agent` → an inert :class:`LoadedAgent` (spec + instructions + tools).
* ``leve.graph.build_graph`` → the compiled ``StateGraph`` (needs the
  checkpointer/store, which are runtime-managed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from leve.agent import AgentSpec
from leve.config import LeveConfig
from leve.errors import LoaderError
from leve.loader import discovery
from leve.tools import ToolSpec


@dataclass(frozen=True)
class LoadedAgent:
    """The discovered, not-yet-compiled definition of an agent."""

    name: str
    path: Path
    spec: AgentSpec
    instructions: str = ""
    tools: tuple[ToolSpec, ...] = field(default_factory=tuple)


def load_agent(agent_dir: Path, config: LeveConfig) -> LoadedAgent:
    """Discover and validate the agent rooted at ``agent_dir``.

    Args:
        agent_dir: Directory containing ``agent.py`` (and optionally
            ``instructions.md``, ``tools/``).
        config: Project config (provides the import root for the project tree).
    """

    agent_dir = agent_dir.resolve()
    if not agent_dir.is_dir():
        raise LoaderError(f"Agent directory not found: {agent_dir}")

    agent_file = agent_dir / "agent.py"
    if not agent_file.is_file():
        raise LoaderError(f"Missing agent.py in {agent_dir}")

    root = discovery.mount_project(config.project_dir)

    # 1. agent.py → base config (exactly one AgentSpec).
    agent_module = discovery.import_path(root, config.project_dir, agent_file)
    spec = discovery.find_single(
        agent_module, AgentSpec, what="agent (define_agent call)", path=agent_file
    )

    # 2. instructions.md → system prompt (optional).
    instructions = _read_instructions(agent_dir / "instructions.md")

    # 3. tools/*.py → ToolSpec list.
    tools = _load_tools(root, config.project_dir, agent_dir / "tools")

    return LoadedAgent(
        name=agent_dir.name,
        path=agent_dir,
        spec=spec,
        instructions=instructions,
        tools=tuple(tools),
    )


def load_project(config: LeveConfig) -> LoadedAgent:
    """Load the project's top-level agent using ``config.agent_dir``."""

    return load_agent(config.agent_dir, config)


def _read_instructions(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _load_tools(root: str, project_dir: Path, tools_dir: Path) -> list[ToolSpec]:
    tools: list[ToolSpec] = []
    names: set[str] = set()
    for file in discovery.python_files(tools_dir):
        module = discovery.import_path(root, project_dir, file)
        for spec in discovery.collect_instances(module, ToolSpec):
            if spec.name in names:
                raise LoaderError(f"Duplicate tool name '{spec.name}' (in {file}).")
            names.add(spec.name)
            tools.append(spec)
    return tools


__all__ = ["LoadedAgent", "load_agent", "load_project"]
