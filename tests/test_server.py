"""Tests for the HTTP API."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx

from leve.server import API_PREFIX, SessionManager, create_app

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
from langchain_core.messages import AIMessage

agent = define_agent(model=FakeChatModel(responses=[
    AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]),
    "all done",
]))
"""

ECHO_TOOL = """\
from pydantic import BaseModel, Field
from leve.tools import define_tool

class In(BaseModel):
    text: str = Field(description="t")

@define_tool(description="Echo.", input_schema=In)
def echo(text: str) -> str:
    return f"echo:{text}"
"""


def _parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@asynccontextmanager
async def _client(config):
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_full_message_flow(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=AGENT_PY, tools={"echo": ECHO_TOOL})
    async with _client(config) as client:
        sid = (await client.post(f"{API_PREFIX}/session")).json()["session_id"]

        resp = await client.post(
            f"{API_PREFIX}/session/{sid}/message", json={"message": "go"}
        )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]

        assert types[0] == "turn.start"
        assert types[-1] == "turn.end"
        assert any(e["type"] == "tool.call" and e["tool"] == "echo" for e in events)
        assert any(e["type"] == "tool.result" and e["output"] == "echo:hi" for e in events)
        assert any(e.get("text") == "all done" for e in events if e["type"] == "model.message")

        state = (await client.get(f"{API_PREFIX}/session/{sid}")).json()
        assert state["messages"][-1]["content"] == "all done"
        assert state["next"] == []


async def test_unknown_session_returns_404(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=AGENT_PY, tools={"echo": ECHO_TOOL})
    async with _client(config) as client:
        resp = await client.post(
            f"{API_PREFIX}/session/missing/message", json={"message": "go"}
        )
        assert resp.status_code == 404


async def test_get_unknown_session_returns_404(tmp_path, write_project):
    config = write_project(tmp_path, agent_py=AGENT_PY, tools={"echo": ECHO_TOOL})
    async with _client(config) as client:
        assert (await client.get(f"{API_PREFIX}/session/missing")).status_code == 404


async def test_stream_on_idle_session_is_empty(tmp_path, write_project):
    """GET /stream on a session with no in-flight turn streams nothing (no replay)."""

    config = write_project(tmp_path, agent_py=AGENT_PY, tools={"echo": ECHO_TOOL})
    async with _client(config) as client:
        sid = (await client.post(f"{API_PREFIX}/session")).json()["session_id"]
        resp = await client.get(f"{API_PREFIX}/session/{sid}/stream")
        assert resp.status_code == 200
        assert _parse_sse(resp.text) == []


async def test_manager_releases_broker_after_turn():
    """A finished turn's broker/task are dropped so they don't accumulate."""

    manager = SessionManager(runtime=None)
    sid = "s1"
    manager._sessions.add(sid)

    async def fake_stream():
        yield {"type": "turn.start"}
        yield {"type": "turn.end"}

    manager.start(sid, fake_stream)
    await manager._tasks[sid]  # run the turn to completion

    assert manager.current_broker(sid) is None
    assert not manager.is_active(sid)
