"""Tier-0 subprocess sandbox (SPEC §5.2).

The opt-in dev escape hatch: **no isolation**. Commands run as real subprocesses
in an ephemeral working directory, with the wall-clock timeout enforced. Memory,
CPU, disk, and network quotas are *not* enforced here (a subprocess shares the
host) — those require a real isolation tier (microsandbox/docker/e2b). This
adapter exists so the framework is runnable everywhere for development and tests;
it must never be the production default.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from leve.config import SandboxLimits
from leve.sandbox.base import Sandbox, SandboxResult


class SubprocessSandbox(Sandbox):
    """Runs commands as host subprocesses in a temp working directory."""

    def __init__(self, limits: SandboxLimits):
        self._limits = limits
        # resolve() so traversal checks compare like-for-like (e.g. macOS maps
        # /var -> /private/var, which would otherwise fail the containment test).
        self._workdir = Path(tempfile.mkdtemp(prefix="leve-sandbox-")).resolve()

    async def run(self, command: str) -> SandboxResult:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self._workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self._limits.timeout_sec
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SandboxResult(
                stdout="",
                stderr=f"Command timed out after {self._limits.timeout_sec}s.",
                exit_code=124,
            )
        return SandboxResult(
            stdout=self._truncate(stdout),
            stderr=self._truncate(stderr),
            exit_code=proc.returncode if proc.returncode is not None else -1,
        )

    def _truncate(self, raw: bytes) -> str:
        """Decode output, capping it so a runaway command can't flood context."""

        limit = self._limits.max_output_bytes
        text = raw[:limit].decode(errors="replace")
        if len(raw) > limit:
            text += f"\n...[truncated, {len(raw) - limit} bytes omitted]"
        return text

    async def write_file(self, path: str, content: str) -> None:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    async def read_file(self, path: str) -> str:
        return self._safe_path(path).read_text(encoding="utf-8")

    async def close(self) -> None:
        shutil.rmtree(self._workdir, ignore_errors=True)

    def _safe_path(self, path: str) -> Path:
        """Resolve ``path`` within the workdir, rejecting traversal escapes."""

        resolved = (self._workdir / path).resolve()
        if not resolved.is_relative_to(self._workdir):
            raise ValueError(f"Path '{path}' escapes the sandbox working directory.")
        return resolved
