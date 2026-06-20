"""The session API (SPEC §5.1, §9).

A *session* is one LangGraph **thread**; :class:`AgentRuntime` drives the
compiled graph for a thread and yields the normalized event stream. This is the
single in-process entry point: the HTTP server, the TUI, and the eval harness
all go through it, so they share identical semantics (durability, interrupts,
event shape). Streaming uses ``astream_events``; the typed :class:`LeveContext`
is threaded in per call and never enters message state (SPEC §5.6).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from leve import events
from leve.events import EventNormalizer
from leve.loader import LoadedAgent
from leve.runtime import LeveContext

logger = logging.getLogger("leve.session")


class AgentRuntime:
    """Drives a compiled agent graph over checkpointed threads (sessions)."""

    def __init__(self, graph: CompiledStateGraph, loaded: LoadedAgent):
        self._graph = graph
        self._loaded = loaded

    @property
    def name(self) -> str:
        return self._loaded.name

    @staticmethod
    def new_session_id() -> str:
        """Mint a fresh thread id; the thread itself is created on first message."""

        return uuid.uuid4().hex

    async def run(
        self,
        session_id: str,
        message: str,
        *,
        context: LeveContext | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a user message and stream the resulting turn's events."""

        async for event in self._stream(
            {"messages": [HumanMessage(content=message)]}, session_id, context
        ):
            yield event

    async def resume(
        self,
        session_id: str,
        value: Any,
        *,
        context: LeveContext | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Resume a paused turn (approval/answer) and stream the continued events.

        ``value`` may be a single decision (applied to a lone pending interrupt,
        or broadcast to several), or an ``{interrupt_id: decision}`` map to answer
        multiple pending interrupts individually (their ids arrive on the
        ``approval.requested`` events).
        """

        pending = await self._pending_interrupts(self._config(session_id))
        command = self._build_resume_command(value, pending)
        async for event in self._stream(command, session_id, context):
            yield event

    @staticmethod
    def _build_resume_command(value: Any, pending: list[dict[str, Any]]) -> Command:
        ids = [p["id"] for p in pending if p.get("id")]
        if len(ids) <= 1:
            # LangGraph accepts a bare resume value for a single pending interrupt.
            return Command(resume=value)
        if isinstance(value, dict) and set(value) >= set(ids):
            return Command(resume=value)  # explicit per-interrupt decisions
        # Convenience: broadcast one decision to every pending interrupt (LangGraph
        # requires an id-keyed map once more than one is pending).
        return Command(resume={interrupt_id: value for interrupt_id in ids})

    async def get_state(self, session_id: str) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the session from the checkpointer."""

        state = await self._graph.aget_state(self._config(session_id))
        return {
            "session_id": session_id,
            "messages": [_serialize_message(m) for m in state.values.get("messages", [])],
            "next": list(state.next),
            "interrupts": self._collect_interrupts(state),
        }

    # --- internals ---------------------------------------------------------

    def _config(self, session_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": session_id},
            "recursion_limit": self._loaded.spec.recursion_limit,
        }

    async def _stream(
        self,
        graph_input: Any,
        session_id: str,
        context: LeveContext | None,
    ) -> AsyncIterator[dict[str, Any]]:
        config = self._config(session_id)
        ctx = context or LeveContext()

        yield events.turn_start(session_id)
        normalizer = EventNormalizer()
        errored = False
        try:
            async for event in self._graph.astream_events(
                graph_input, config=config, context=ctx, version="v2"
            ):
                normalized = normalizer.normalize(event)
                if normalized is not None:
                    yield normalized
        except Exception as exc:  # surface as an event, don't break the stream
            errored = True
            logger.exception("Session %s turn failed", session_id)
            # repr() guarantees a non-empty message even for bare exceptions.
            yield events.error(str(exc) or repr(exc))

        # A turn may end paused on an interrupt (approval/question, M2+).
        interrupts = [] if errored else await self._pending_interrupts(config)
        for interrupt in interrupts:
            yield events.approval_requested(session_id, interrupt)
        yield events.turn_end(session_id, interrupted=bool(interrupts), errored=errored)

    async def _pending_interrupts(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        state = await self._graph.aget_state(config)
        return self._collect_interrupts(state)

    @staticmethod
    def _collect_interrupts(state: Any) -> list[dict[str, Any]]:
        raw = list(getattr(state, "interrupts", ()) or ())
        if not raw:
            for task in getattr(state, "tasks", ()) or ():
                raw.extend(getattr(task, "interrupts", ()) or ())
        return [
            {"id": getattr(i, "id", None), "value": getattr(i, "value", None)}
            for i in raw
        ]


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    """Reduce a LangChain message to a stable, JSON-friendly dict."""

    return {
        "type": message.type,
        "content": message.content,
        "tool_calls": getattr(message, "tool_calls", None) or None,
    }
