"""Tests for event normalization, including streaming de-duplication."""

from __future__ import annotations

from types import SimpleNamespace

from leve.serving.events import EventNormalizer


def _chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


def test_streamed_tokens_suppress_duplicate_message():
    n = EventNormalizer()
    events = [
        {
            "event": "on_chat_model_stream",
            "run_id": "r1",
            "data": {"chunk": _chunk("Hel")},
        },
        {
            "event": "on_chat_model_stream",
            "run_id": "r1",
            "data": {"chunk": _chunk("lo")},
        },
        {
            "event": "on_chat_model_end",
            "run_id": "r1",
            "data": {"output": _chunk("Hello")},
        },
    ]
    out = [e for e in (n.normalize(ev) for ev in events) if e]
    # The two deltas survive; the terminal full-text message is dropped.
    assert [e["type"] for e in out] == ["model.delta", "model.delta"]


def test_message_emitted_when_no_streaming():
    n = EventNormalizer()
    out = n.normalize(
        {
            "event": "on_chat_model_end",
            "run_id": "r1",
            "data": {"output": _chunk("Hello")},
        }
    )
    assert out == {"type": "model.message", "text": "Hello"}


def test_empty_message_dropped():
    n = EventNormalizer()
    assert (
        n.normalize(
            {
                "event": "on_chat_model_end",
                "run_id": "r1",
                "data": {"output": _chunk("")},
            }
        )
        is None
    )


def test_separate_runs_are_independent():
    n = EventNormalizer()
    n.normalize(
        {"event": "on_chat_model_stream", "run_id": "a", "data": {"chunk": _chunk("x")}}
    )
    # A different run that did not stream still gets its message.
    out = n.normalize(
        {
            "event": "on_chat_model_end",
            "run_id": "b",
            "data": {"output": _chunk("done")},
        }
    )
    assert out["type"] == "model.message"


def test_tool_events():
    n = EventNormalizer()
    call = n.normalize(
        {"event": "on_tool_start", "name": "echo", "data": {"input": {"text": "hi"}}}
    )
    result = n.normalize(
        {"event": "on_tool_end", "name": "echo", "data": {"output": _chunk("echo:hi")}}
    )
    assert call == {"type": "tool.call", "tool": "echo", "input": {"text": "hi"}}
    assert result == {"type": "tool.result", "tool": "echo", "output": "echo:hi"}
