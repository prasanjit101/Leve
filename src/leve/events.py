"""Event normalization (SPEC §9).

LangGraph's ``astream_events`` emits a rich, internal event stream. Leve narrows
it to a small, stable schema (``turn.start``, ``model.delta``, ``model.message``,
``tool.call``, ``tool.result``, ``approval.requested``, ``turn.end``) so the HTTP API, the
TUI, and the eval harness all consume one shape regardless of LangGraph
internals. Keeping the mapping here (not in the server) means every consumer —
in-process or over HTTP — sees identical events.
"""

from __future__ import annotations

from typing import Any

# --- Event constructors (the stable schema) --------------------------------


def turn_start(session_id: str) -> dict[str, Any]:
    return {"type": "turn.start", "session_id": session_id}


def turn_end(
    session_id: str, *, interrupted: bool = False, errored: bool = False
) -> dict[str, Any]:
    return {
        "type": "turn.end",
        "session_id": session_id,
        "interrupted": interrupted,
        "errored": errored,
    }


def approval_requested(session_id: str, interrupt: dict[str, Any]) -> dict[str, Any]:
    return {"type": "approval.requested", "session_id": session_id, "interrupt": interrupt}


def error(message: str) -> dict[str, Any]:
    return {"type": "error", "message": message}


# --- Mapping from LangGraph's astream_events -------------------------------


class EventNormalizer:
    """Maps a turn's ``astream_events`` into Leve events, de-duping assistant text.

    A streaming provider fires both ``on_chat_model_stream`` (token chunks) and a
    terminal ``on_chat_model_end`` (the full aggregated text). Emitting both would
    double the assistant output for any consumer that concatenates them. The
    normalizer remembers, per model run id, whether non-empty tokens streamed; if
    so it drops the redundant terminal ``model.message``. This makes the contract
    deterministic at the source rather than relying on each consumer to dedupe.

    One instance per turn (it is stateful); create a fresh one per stream.
    """

    def __init__(self) -> None:
        self._streamed_runs: set[Any] = set()

    def normalize(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Map one event to a Leve event, or ``None`` to drop it."""

        kind = event.get("event")
        run_id = event.get("run_id")

        if kind == "on_chat_model_stream":
            # Best-effort live tokens (only when the provider streams).
            text = _text_of(event.get("data", {}).get("chunk"))
            if text:
                self._streamed_runs.add(run_id)
                return {"type": "model.delta", "text": text}
            return None

        if kind == "on_chat_model_end":
            # Reliable assistant output. If this run already streamed tokens, the
            # deltas *are* the message — drop the duplicate. Tool-call-only
            # messages carry no text and are dropped (tool events represent them).
            if run_id in self._streamed_runs:
                return None
            text = _text_of(event.get("data", {}).get("output"))
            if text:
                return {"type": "model.message", "text": text}
            return None

        if kind == "on_tool_start":
            return {
                "type": "tool.call",
                "tool": event.get("name", ""),
                "input": event.get("data", {}).get("input"),
            }

        if kind == "on_tool_end":
            return {
                "type": "tool.result",
                "tool": event.get("name", ""),
                "output": _output_of(event.get("data", {}).get("output")),
            }

        return None


def _text_of(chunk: Any) -> str:
    """Extract plain text from a chat-model chunk (str or content-block list)."""

    content = getattr(chunk, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _output_of(output: Any) -> Any:
    """Reduce a tool's output to a JSON-friendly value for the event stream."""

    content = getattr(output, "content", None)
    if content is not None:
        return content
    if isinstance(output, (str, int, float, bool, type(None), list, dict)):
        return output
    return str(output)
