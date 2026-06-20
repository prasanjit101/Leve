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

from dataclasses import dataclass, field, replace
from pathlib import Path

from leve.agent import AgentSpec
from leve.config import LeveConfig
from leve.connections import ConnectionSpec
from leve.errors import LoaderError
from leve.loader import discovery
from leve.skills import SkillSpec, parse_skill
from leve.tools import ToolSpec


@dataclass(frozen=True)
class LoadedAgent:
    """The discovered, not-yet-compiled definition of an agent."""

    name: str
    path: Path
    spec: AgentSpec
    instructions: str = ""
    tools: tuple[ToolSpec, ...] = field(default_factory=tuple)
    skills: tuple[SkillSpec, ...] = field(default_factory=tuple)
    connections: tuple[ConnectionSpec, ...] = field(default_factory=tuple)
    subagents: tuple["LoadedAgent", ...] = field(default_factory=tuple)


def load_agent(agent_dir: Path, config: LeveConfig) -> LoadedAgent:
    """Discover and validate the agent rooted at ``agent_dir``.

    Mounts the project tree once, then recurses into ``subagents/`` without
    re-mounting (which would purge the parent's freshly imported modules).
    """

    root = discovery.mount_project(config.project_dir)
    return _load_dir(agent_dir, root, config)


def load_project(config: LeveConfig) -> LoadedAgent:
    """Load the project's top-level agent using ``config.agent_dir``."""

    return load_agent(config.agent_dir, config)


def _load_dir(agent_dir: Path, root: str, config: LeveConfig) -> LoadedAgent:
    """Load a single agent directory (used for the root agent and each subagent)."""

    agent_dir = agent_dir.resolve()
    if not agent_dir.is_dir():
        raise LoaderError(f"Agent directory not found: {agent_dir}")

    agent_file = agent_dir / "agent.py"
    if not agent_file.is_file():
        raise LoaderError(f"Missing agent.py in {agent_dir}")

    # 1. agent.py → base config (exactly one AgentSpec).
    agent_module = discovery.import_path(root, config.project_dir, agent_file)
    spec = discovery.find_single(
        agent_module, AgentSpec, what="agent (define_agent call)", path=agent_file
    )

    # 2. instructions.md → system prompt (optional).
    instructions = _read_instructions(agent_dir / "instructions.md")

    # 3. tools/*.py → ToolSpec list.
    tools = _load_tools(root, config.project_dir, agent_dir / "tools")

    # 4. skills/*.md frontmatter → load_skill catalog.
    skills = _load_skills(agent_dir / "skills")

    # 5. connections/*.py → MCP/OpenAPI connection specs (tools discovered at runtime).
    connections = _load_connections(root, config.project_dir, agent_dir / "connections")

    # 6. subagents/<name>/ → recursively compiled subgraphs.
    subagents = _load_subagents(agent_dir / "subagents", root, config)

    return LoadedAgent(
        name=agent_dir.name,
        path=agent_dir,
        spec=spec,
        instructions=instructions,
        tools=tuple(tools),
        skills=tuple(skills),
        connections=tuple(connections),
        subagents=tuple(subagents),
    )


def _load_subagents(subagents_dir: Path, root: str, config: LeveConfig) -> list[LoadedAgent]:
    if not subagents_dir.is_dir():
        return []
    loaded: list[LoadedAgent] = []
    names: set[str] = set()
    for child in sorted(subagents_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        sub = _load_dir(child, root, config)
        if sub.name in names:
            raise LoaderError(f"Duplicate subagent name '{sub.name}'.")
        names.add(sub.name)
        loaded.append(sub)
    return loaded


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


def _load_skills(skills_dir: Path) -> list[SkillSpec]:
    skills: list[SkillSpec] = []
    names: set[str] = set()
    for file in _markdown_files(skills_dir):
        skill = parse_skill(file)
        if skill.name in names:
            raise LoaderError(f"Duplicate skill name '{skill.name}' (in {file}).")
        names.add(skill.name)
        skills.append(skill)
    return skills


def _load_connections(
    root: str, project_dir: Path, connections_dir: Path
) -> list[ConnectionSpec]:
    connections: list[ConnectionSpec] = []
    names: set[str] = set()
    for file in discovery.python_files(connections_dir):
        module = discovery.import_path(root, project_dir, file)
        for spec in discovery.collect_instances(module, ConnectionSpec):
            # The file name is the connection's namespace prefix (linear.<tool>).
            named = replace(spec, name=file.stem)
            if named.name in names:
                raise LoaderError(f"Duplicate connection name '{named.name}'.")
            names.add(named.name)
            connections.append(named)
    return connections


def _markdown_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix == ".md"
    )


__all__ = ["LoadedAgent", "load_agent", "load_project"]
