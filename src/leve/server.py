"""HTTP API (SPEC §9).

A thin FastAPI layer over :class:`~leve.session.AgentRuntime`. The session model
separates *sending* from *streaming*: a message kicks off a turn whose events are
published to a per-session :class:`SessionBroker`; the POST response body *is*
the live SSE stream of that turn, and ``GET …/stream`` lets a second client
attach to the same in-flight turn. Both consume the identical normalized events
emitted in-process, so HTTP adds no new event semantics — only transport.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from leve.app import build_runtime
from leve.config import LeveConfig
from leve.runtime import LeveContext
from leve.session import AgentRuntime

API_PREFIX = "/leve/v1"


# --- Streaming plumbing -----------------------------------------------------


class SessionBroker:
    """A single turn's event fan-out: buffers events and serves many subscribers.

    Buffering means a subscriber that attaches mid-turn still receives the events
    it missed, then continues live until the turn closes.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._closed = False
        self._cond = asyncio.Condition()

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    async def close(self) -> None:
        async with self._cond:
            self._closed = True
            self._cond.notify_all()

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        idx = 0
        while True:
            async with self._cond:
                await self._cond.wait_for(
                    lambda: idx < len(self._events) or self._closed
                )
                batch = self._events[idx:]
                idx += len(batch)
                done = self._closed and idx >= len(self._events)
            for event in batch:
                yield event
            if done:
                return


class SessionManager:
    """Tracks known sessions and the one in-flight turn (broker/task) per session."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime
        self._sessions: set[str] = set()
        self._brokers: dict[str, SessionBroker] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def create(self) -> str:
        session_id = self.runtime.new_session_id()
        self._sessions.add(session_id)
        return session_id

    def exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def is_active(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def current_broker(self, session_id: str) -> SessionBroker | None:
        return self._brokers.get(session_id)

    def start(
        self, session_id: str, stream_factory: Callable[[], AsyncIterator[dict]]
    ) -> SessionBroker:
        """Run ``stream_factory`` in the background, publishing to a fresh broker."""

        broker = SessionBroker()
        self._brokers[session_id] = broker

        async def runner() -> None:
            try:
                async for event in stream_factory():
                    await broker.publish(event)
            finally:
                await broker.close()
                # Release transient bookkeeping once the turn is done so it does
                # not accumulate over the server's lifetime. Guard against a
                # newer turn for the same session having already replaced us.
                if self._brokers.get(session_id) is broker:
                    self._brokers.pop(session_id, None)
                    self._tasks.pop(session_id, None)

        self._tasks[session_id] = asyncio.create_task(runner())
        return broker

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            with suppress(asyncio.CancelledError, Exception):
                await task


# --- Request bodies ---------------------------------------------------------


class MessageBody(BaseModel):
    message: str
    template_vars: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResumeBody(BaseModel):
    value: Any = None


# --- App --------------------------------------------------------------------


def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[bytes]:
    async def gen() -> AsyncIterator[bytes]:
        async for event in events:
            payload = json.dumps(event, default=str)
            yield f"data: {payload}\n\n".encode("utf-8")

    return gen()


def _manager(request: Request) -> SessionManager:
    return request.app.state.manager


def create_app(config: LeveConfig) -> FastAPI:
    """Build the FastAPI app serving the compiled agent for ``config``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_runtime(config) as runtime:
            app.state.manager = SessionManager(runtime)
            try:
                yield
            finally:
                await app.state.manager.shutdown()

    app = FastAPI(title="Leve", version="0.1.0", lifespan=lifespan)

    @app.post(f"{API_PREFIX}/session")
    async def create_session(request: Request) -> dict[str, str]:
        return {"session_id": _manager(request).create()}

    @app.post(f"{API_PREFIX}/session/{{session_id}}/message")
    async def send_message(
        session_id: str, body: MessageBody, request: Request
    ) -> StreamingResponse:
        manager = _manager(request)
        _require_session(manager, session_id)
        _require_idle(manager, session_id)
        context = LeveContext(template_vars=body.template_vars, metadata=body.metadata)
        broker = manager.start(
            session_id,
            lambda: manager.runtime.run(session_id, body.message, context=context),
        )
        return StreamingResponse(_sse(broker.subscribe()), media_type="text/event-stream")

    @app.post(f"{API_PREFIX}/session/{{session_id}}/resume")
    async def resume_session(
        session_id: str, body: ResumeBody, request: Request
    ) -> StreamingResponse:
        manager = _manager(request)
        _require_session(manager, session_id)
        _require_idle(manager, session_id)
        broker = manager.start(
            session_id,
            lambda: manager.runtime.resume(session_id, body.value),
        )
        return StreamingResponse(_sse(broker.subscribe()), media_type="text/event-stream")

    @app.get(f"{API_PREFIX}/session/{{session_id}}/stream")
    async def stream_session(session_id: str, request: Request) -> StreamingResponse:
        manager = _manager(request)
        _require_session(manager, session_id)
        # Attach only to an in-flight turn. Between turns there is no broker
        # (it is released when the turn ends), so an idle session streams nothing
        # rather than replaying the previous turn's buffered transcript.
        broker = manager.current_broker(session_id) if manager.is_active(session_id) else None
        events: AsyncIterator[dict] = broker.subscribe() if broker else _empty()
        return StreamingResponse(_sse(events), media_type="text/event-stream")

    @app.get(f"{API_PREFIX}/session/{{session_id}}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        manager = _manager(request)
        _require_session(manager, session_id)
        return await manager.runtime.get_state(session_id)

    return app


async def _empty() -> AsyncIterator[dict[str, Any]]:
    return
    yield  # pragma: no cover - makes this an async generator


def _require_session(manager: SessionManager, session_id: str) -> None:
    if not manager.exists(session_id):
        raise HTTPException(status_code=404, detail=f"Unknown session '{session_id}'.")


def _require_idle(manager: SessionManager, session_id: str) -> None:
    if manager.is_active(session_id):
        raise HTTPException(
            status_code=409, detail="A turn is already in progress for this session."
        )
