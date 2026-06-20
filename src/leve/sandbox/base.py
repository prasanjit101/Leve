"""Sandbox interface (SPEC §5.2).

Agent-generated code never runs in the harness process. A ``Sandbox`` is the
small abstraction every adapter implements — ``run`` / ``write_file`` /
``read_file`` — so adapters swap by config (Dependency Inversion / Open–Closed).
The graph depends only on this interface, never on a concrete adapter.

Critically, a sandbox carries **no ambient credentials** (SPEC §5.6): code that
needs privileged data must call back through a Leve tool, which re-applies the
caller's permissions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of running a command in the sandbox."""

    stdout: str
    stderr: str
    exit_code: int

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Sandbox(ABC):
    """An isolated execution environment for untrusted code."""

    @abstractmethod
    async def run(self, command: str) -> SandboxResult:
        """Execute a shell command and capture its output."""

    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Write ``content`` to ``path`` inside the sandbox filesystem."""

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read and return the contents of ``path`` inside the sandbox."""

    async def close(self) -> None:
        """Release sandbox resources. Idempotent; default is a no-op."""
