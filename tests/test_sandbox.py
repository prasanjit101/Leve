"""Tests for the sandbox interface, subprocess adapter, factory, and wiring."""

from __future__ import annotations

import pytest

from leve.app import build_runtime
from leve.config import SandboxConfig, SandboxLimits
from leve.errors import ConfigError
from leve.sandbox import create_sandbox, make_sandbox_tools
from leve.sandbox.subprocess import SubprocessSandbox
from tests.conftest import collect

SANDBOX_TOML = """\
[agent]
root = "agent"
[persistence]
checkpointer = "memory"
store = "memory"
[sandbox]
adapter = "subprocess"
"""

SANDBOX_AGENT = """\
from leve import define_agent
from leve.testing import FakeChatModel
from langchain_core.messages import AIMessage
agent = define_agent(sandbox=True, model=FakeChatModel(responses=[
    AIMessage(content="", tool_calls=[{"name":"run_shell","args":{"command":"echo sandboxed"},"id":"c1"}]),
    "done",
]))
"""


async def test_subprocess_run():
    sandbox = SubprocessSandbox(SandboxLimits())
    try:
        result = await sandbox.run("echo hello")
        assert result.ok and "hello" in result.stdout
    finally:
        await sandbox.close()


async def test_subprocess_file_roundtrip():
    sandbox = SubprocessSandbox(SandboxLimits())
    try:
        await sandbox.write_file("data/a.txt", "payload")
        assert await sandbox.read_file("data/a.txt") == "payload"
    finally:
        await sandbox.close()


async def test_subprocess_timeout():
    sandbox = SubprocessSandbox(SandboxLimits(timeout_sec=1))
    try:
        result = await sandbox.run("sleep 5")
        assert result.exit_code == 124 and "timed out" in result.stderr
    finally:
        await sandbox.close()


async def test_path_traversal_is_blocked():
    sandbox = SubprocessSandbox(SandboxLimits())
    try:
        with pytest.raises(ValueError):
            await sandbox.write_file("../escape.txt", "x")
    finally:
        await sandbox.close()


async def test_sandbox_tool():
    sandbox = SubprocessSandbox(SandboxLimits())
    try:
        tool = make_sandbox_tools(sandbox)[0]
        assert tool.name == "run_shell"
        out = await tool.ainvoke({"command": "echo hi"})
        assert "hi" in out["stdout"] and out["exit_code"] == 0
    finally:
        await sandbox.close()


async def test_sandbox_has_no_ambient_credentials(monkeypatch):
    """Sandboxed code must not inherit harness secrets (SPEC §5.6)."""

    monkeypatch.setenv("LEVE_CRED_WAREHOUSE", "super-secret")
    sandbox = SubprocessSandbox(SandboxLimits())
    try:
        result = await sandbox.run("echo LEAK=$LEVE_CRED_WAREHOUSE")
        assert "super-secret" not in result.stdout
        assert "LEAK=" in result.stdout  # var simply absent, not the secret
    finally:
        await sandbox.close()


async def test_output_is_truncated():
    sandbox = SubprocessSandbox(SandboxLimits(max_output_bytes=10))
    try:
        result = await sandbox.run("printf 'abcdefghijklmnopqrstuvwxyz'")
        assert "truncated" in result.stdout
        assert result.stdout.startswith("abcdefghij")
    finally:
        await sandbox.close()


def test_factory_resolves_subprocess():
    assert isinstance(
        create_sandbox(SandboxConfig(adapter="subprocess")), SubprocessSandbox
    )


def test_factory_rejects_unimplemented_adapter():
    with pytest.raises(ConfigError):
        create_sandbox(SandboxConfig(adapter="docker"))


async def test_sandbox_tool_injected_when_enabled(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=SANDBOX_AGENT, leve_toml=SANDBOX_TOML)
    async with build_runtime(config) as rt:
        events = await collect(rt.run(rt.new_session_id(), "go"))
        results = [e for e in events if e["type"] == "tool.result"]
        assert results and "sandboxed" in str(results[0]["output"])
