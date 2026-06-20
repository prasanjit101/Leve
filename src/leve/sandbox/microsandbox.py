"""Tier-1 microsandbox adapter (SPEC §5.2) — the default.

microsandbox gives true microVM isolation (libkrun→KVM on Linux,
Hypervisor.framework on Apple-Silicon macOS), sub-200 ms starts, and runs real
bash in a separate security context — exactly the harness/agent split Leve needs.

The SDK is an optional dependency (``leve[microsandbox]``) and needs the
microsandbox server running, so it is imported lazily: importing this module
never requires the SDK; only constructing the adapter does.
"""

from __future__ import annotations

import asyncio
import uuid

from leve.config import SandboxLimits
from leve.errors import ConfigError
from leve.sandbox.base import Sandbox, SandboxResult


class MicrosandboxSandbox(Sandbox):
    """Runs commands inside a microsandbox microVM."""

    def __init__(self, limits: SandboxLimits):
        try:
            from microsandbox import PythonSandbox  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigError(
                "The microsandbox adapter requires the optional dependency. "
                "Install with `pip install 'leve[microsandbox]'` and run the "
                "microsandbox server, or set [sandbox] adapter to 'subprocess' "
                "for local development."
            ) from exc

        self._limits = limits
        self._sandbox_cls = PythonSandbox
        self._sandbox = None  # lazily started on first use

    async def _ensure(self):  # pragma: no cover - requires running server
        if self._sandbox is None:
            self._sandbox = self._sandbox_cls(
                memory=self._limits.memory_mb, cpus=self._limits.vcpus
            )
            await self._sandbox.start()
        return self._sandbox

    async def run(self, command: str) -> SandboxResult:  # pragma: no cover
        sandbox = await self._ensure()
        try:
            execution = await asyncio.wait_for(
                sandbox.command.run("sh", ["-c", command]),
                timeout=self._limits.timeout_sec,
            )
        except asyncio.TimeoutError:
            return SandboxResult(
                stdout="",
                stderr=f"Command timed out after {self._limits.timeout_sec}s.",
                exit_code=124,
            )
        # Default a missing exit code to -1 (unknown), never 0 — a failure must
        # not be reported as success.
        return SandboxResult(
            stdout=await execution.output(),
            stderr=await execution.error(),
            exit_code=getattr(execution, "exit_code", -1),
        )

    async def write_file(self, path: str, content: str) -> None:  # pragma: no cover
        # Randomized heredoc delimiter so agent-controlled content can't close
        # the heredoc early; shlex-quote the path against shell injection.
        import shlex

        delimiter = f"LEVE_EOF_{uuid.uuid4().hex}"
        await self.run(f"cat > {shlex.quote(path)} <<'{delimiter}'\n{content}\n{delimiter}")

    async def read_file(self, path: str) -> str:  # pragma: no cover
        import shlex

        result = await self.run(f"cat {shlex.quote(path)}")
        return result.stdout

    async def close(self) -> None:  # pragma: no cover
        if self._sandbox is not None:
            await self._sandbox.stop()
            self._sandbox = None
