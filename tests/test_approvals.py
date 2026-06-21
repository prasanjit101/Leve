"""Tests for human-in-the-loop tool approval."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from leve.testing import FakeChatModel
from leve.tools import define_tool
from tests.conftest import collect, runtime_for


def _gated_tool():
    class RunSqlInput(BaseModel):
        sql: str = Field(description="A SQL statement.")

    @define_tool(
        description="Run SQL.",
        input_schema=RunSqlInput,
        needs_approval=lambda tool_input: "drop" in tool_input.sql.lower(),
    )
    def run_sql(sql: str) -> str:
        return f"ran:{sql}"

    return run_sql


def _model_calling(sql: str, final: str) -> FakeChatModel:
    return FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "run_sql", "args": {"sql": sql}, "id": "c1"}],
            ),
            final,
        ]
    )


async def test_approval_pauses_then_resumes_approved(make_loaded):
    loaded = make_loaded(_model_calling("DROP TABLE x", "done"), tools=(_gated_tool(),))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))

        assert any(e["type"] == "approval.requested" for e in events)
        assert events[-1] == {
            "type": "turn.end",
            "session_id": sid,
            "interrupted": True,
            "errored": False,
        }
        assert not any(e["type"] == "tool.result" for e in events)  # not executed yet

        resumed = await collect(rt.resume(sid, {"approved": True}))
        assert any(
            e["type"] == "tool.result" and e["output"] == "ran:DROP TABLE x"
            for e in resumed
        )
        state = await rt.get_state(sid)
        assert state["messages"][-1]["content"] == "done"


async def test_approval_denied_returns_denial(make_loaded):
    loaded = make_loaded(_model_calling("DROP TABLE x", "ok"), tools=(_gated_tool(),))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        await collect(rt.run(sid, "go"))
        await collect(rt.resume(sid, {"approved": False}))

        state = await rt.get_state(sid)
        denied = [
            m
            for m in state["messages"]
            if m["type"] == "tool" and "denied" in str(m["content"])
        ]
        assert denied


async def test_non_gated_input_runs_without_approval(make_loaded):
    loaded = make_loaded(_model_calling("SELECT 1", "done"), tools=(_gated_tool(),))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))

        assert not any(e["type"] == "approval.requested" for e in events)
        assert any(
            e["type"] == "tool.result" and e["output"] == "ran:SELECT 1" for e in events
        )


async def test_multiple_gated_calls_resume_by_broadcast(make_loaded):
    """Two gated calls in one turn resume together with a single decision."""

    from langchain_core.messages import AIMessage

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "run_sql", "args": {"sql": "DROP a"}, "id": "c1"},
                    {"name": "run_sql", "args": {"sql": "DROP b"}, "id": "c2"},
                ],
            ),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(_gated_tool(),))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))
        approvals = [e for e in events if e["type"] == "approval.requested"]
        assert len(approvals) == 2

        resumed = await collect(rt.resume(sid, {"approved": True}))  # broadcast
        outputs = {e["output"] for e in resumed if e["type"] == "tool.result"}
        assert outputs == {"ran:DROP a", "ran:DROP b"}
        assert (await rt.get_state(sid))["messages"][-1]["content"] == "done"


async def test_gate_without_input_schema(make_loaded):
    """A predicate using attribute access works even with no input_schema."""

    from langchain_core.messages import AIMessage

    @define_tool(
        description="Write a file.",
        needs_approval=lambda ti: ti.path.startswith("/etc"),
    )
    def write_file(path: str) -> str:
        return f"wrote:{path}"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "/etc/x"}, "id": "c1"}
                ],
            ),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(write_file,))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))
        assert any(e["type"] == "approval.requested" for e in events)


async def test_predicate_error_fails_closed(make_loaded):
    """A buggy predicate gates the call (fail closed) instead of crashing."""

    from langchain_core.messages import AIMessage
    from pydantic import BaseModel, Field

    class In(BaseModel):
        sql: str = Field(description="q")

    @define_tool(description="x", input_schema=In, needs_approval=lambda ti: 1 // 0)
    def run_sql(sql: str) -> str:
        return f"ran:{sql}"

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "run_sql", "args": {"sql": "SELECT 1"}, "id": "c1"}
                ],
            ),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(run_sql,))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))
        assert any(e["type"] == "approval.requested" for e in events)
        assert events[-1]["errored"] is False


async def test_malformed_args_recover_via_tool_node(make_loaded):
    """Malformed args aren't gated; the tool node returns a recoverable error."""

    from langchain_core.messages import AIMessage

    model = FakeChatModel(
        responses=[
            # Missing required 'sql' field.
            AIMessage(
                content="",
                tool_calls=[{"name": "run_sql", "args": {"wrong": "x"}, "id": "c1"}],
            ),
            "recovered",
        ]
    )
    loaded = make_loaded(model, tools=(_gated_tool(),))
    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "go"))
        assert not any(e["type"] == "approval.requested" for e in events)
        assert events[-1]["errored"] is False
        assert (await rt.get_state(sid))["messages"][-1]["content"] == "recovered"
