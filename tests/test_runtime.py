"""Tests for graph build + AgentRuntime (the session loop)."""

from __future__ import annotations

from datetime import date

from langchain_core.messages import AIMessage, SystemMessage

from leve.session import extract_reply
from leve.testing import FakeChatModel
from tests.conftest import collect, runtime_for


def test_extract_reply_joins_stream_deltas():
    events = [{"type": "model.delta", "text": "Hel"}, {"type": "model.delta", "text": "lo"}]
    assert extract_reply(events) == "Hello"


def test_extract_reply_prefers_full_message():
    events = [{"type": "model.delta", "text": "x"}, {"type": "model.message", "text": "final"}]
    assert extract_reply(events) == "final"


async def test_simple_turn_emits_events(make_loaded):
    model = FakeChatModel(responses=["Hello!"])
    loaded = make_loaded(model)
    async with runtime_for(loaded) as runtime:
        sid = runtime.new_session_id()
        events = await collect(runtime.run(sid, "hi"))

    types = [e["type"] for e in events]
    assert types[0] == "turn.start"
    assert types[-1] == "turn.end"
    assert "model.message" in types
    message = next(e for e in events if e["type"] == "model.message")
    assert message["text"] == "Hello!"


async def test_tool_call_loop(make_loaded, echo_tool):
    model = FakeChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "echo", "args": {"text": "hi"}, "id": "c1"}]),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(echo_tool,))
    async with runtime_for(loaded) as runtime:
        sid = runtime.new_session_id()
        events = await collect(runtime.run(sid, "go"))

    calls = [e for e in events if e["type"] == "tool.call"]
    results = [e for e in events if e["type"] == "tool.result"]
    assert calls and calls[0]["tool"] == "echo"
    assert results and results[0]["output"] == "echo:hi"


async def test_durability_across_turns(make_loaded):
    model = FakeChatModel(responses=["first", "second"])
    loaded = make_loaded(model)
    async with runtime_for(loaded) as runtime:
        sid = runtime.new_session_id()
        await collect(runtime.run(sid, "one"))
        await collect(runtime.run(sid, "two"))
        state = await runtime.get_state(sid)

    # 2 user + 2 assistant messages persisted on the same thread.
    assert len(state["messages"]) == 4
    assert state["messages"][-1]["content"] == "second"
    assert state["next"] == []


async def test_instructions_are_rendered_into_system_prompt(make_loaded):
    model = FakeChatModel(responses=["ok"])
    loaded = make_loaded(model, instructions="Today is {{ current_date }}.")
    async with runtime_for(loaded) as runtime:
        await collect(runtime.run(runtime.new_session_id(), "hi"))

    system = next(m for m in model.calls[0] if isinstance(m, SystemMessage))
    assert date.today().isoformat() in system.content
    assert "{{" not in system.content  # placeholder was resolved
