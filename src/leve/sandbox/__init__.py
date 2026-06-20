"""Sandbox factory and tool injection (SPEC §5.2).

``create_sandbox`` resolves the configured adapter name to a concrete
:class:`Sandbox` (the only place that knows the adapter→class mapping), and
``make_sandbox_tools`` exposes the sandbox to the model as ordinary tools. The
adapters themselves live in sibling modules and are imported lazily so an unused
adapter's optional dependency is never required.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from leve.config import SandboxConfig
from leve.errors import ConfigError
from leve.sandbox.base import Sandbox, SandboxResult

__all__ = ["Sandbox", "SandboxResult", "create_sandbox", "make_sandbox_tools"]


def create_sandbox(config: SandboxConfig) -> Sandbox:
    """Instantiate the configured sandbox adapter."""

    if config.adapter == "subprocess":
        from leve.sandbox.subprocess import SubprocessSandbox

        return SubprocessSandbox(config.limits)
    if config.adapter == "microsandbox":
        from leve.sandbox.microsandbox import MicrosandboxSandbox

        return MicrosandboxSandbox(config.limits)

    # os_native / docker / e2b / modal are roadmap tiers (SPEC §5.2); they share
    # this seam and will slot in here without touching the graph.
    raise ConfigError(
        f"Sandbox adapter '{config.adapter}' is not yet implemented. "
        "Use 'microsandbox' (default) or 'subprocess'."
    )


class RunShellInput(BaseModel):
    command: str = Field(description="A shell command to run inside the sandbox.")


def make_sandbox_tools(sandbox: Sandbox) -> list[BaseTool]:
    """Expose the sandbox to the model. M1 surface: a single ``run_shell`` tool."""

    async def run_shell(command: str) -> dict:
        result = await sandbox.run(command)
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
        }

    return [
        StructuredTool.from_function(
            coroutine=run_shell,
            name="run_shell",
            description=(
                "Run a shell command in an isolated sandbox and return its "
                "stdout, stderr, and exit code. The sandbox has no access to "
                "credentials or private data."
            ),
            args_schema=RunShellInput,
        )
    ]
