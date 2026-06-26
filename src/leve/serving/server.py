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
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from leve.config import LeveConfig
from leve.core.runtime import LeveContext
from leve.errors import ConfigError
from leve.loader import load_project
from leve.security.auth import anonymous, with_broker
from leve.serving.app import build_runtime
from leve.serving.session import AgentRuntime, extract_reply

logger = logging.getLogger("leve.server")

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
        # Channel turns run in the background (fast webhook ack) and serialize
        # per session_key so retries / concurrent messages don't race a thread.
        self._channel_tasks: set[asyncio.Task] = set()
        self._locks: dict[str, asyncio.Lock] = {}

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

    def spawn_channel_turn(self, adapter, incoming) -> asyncio.Task:
        """Run a channel turn in the background and deliver the reply."""

        task = asyncio.create_task(self.run_channel_turn(adapter, incoming))
        self._channel_tasks.add(task)
        task.add_done_callback(self._channel_tasks.discard)
        return task

    async def run_channel_turn(self, adapter, incoming) -> None:
        """Drive a turn for a channel message (serialized per session) and deliver."""

        principal = with_broker(incoming.principal, self.runtime.broker)
        context = LeveContext(principal=principal)
        lock = self._locks.setdefault(incoming.session_key, asyncio.Lock())
        async with lock:
            events = [
                event
                async for event in self.runtime.run(
                    incoming.session_key, incoming.text, context=context
                )
            ]
        if any(e["type"] == "error" for e in events):
            logger.warning("Channel turn errored for %s", incoming.session_key)
            return
        reply = extract_reply(events)
        if not reply:
            return
        try:
            await adapter.deliver(incoming.target, reply)
        except Exception:  # delivery is best-effort; never fail the webhook
            logger.exception("Channel delivery failed for %s", incoming.session_key)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values()) + list(self._channel_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
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
            yield f"data: {payload}\n\n".encode()

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
        # HTTP callers are anonymous by default (Platform Auth / a custom resolver
        # supplies a real principal); attach the broker so credential() works.
        context = LeveContext(
            template_vars=body.template_vars,
            metadata=body.metadata,
            principal=with_broker(anonymous(), manager.runtime.broker),
        )
        broker = manager.start(
            session_id,
            lambda: manager.runtime.run(session_id, body.message, context=context),
        )
        return StreamingResponse(
            _sse(broker.subscribe()), media_type="text/event-stream"
        )

    @app.post(f"{API_PREFIX}/session/{{session_id}}/resume")
    async def resume_session(
        session_id: str, body: ResumeBody, request: Request
    ) -> StreamingResponse:
        manager = _manager(request)
        _require_session(manager, session_id)
        _require_idle(manager, session_id)
        # Re-supply the caller principal on resume (runtime context isn't
        # checkpointed): without it a resumed/consent turn would lose identity.
        context = LeveContext(
            principal=with_broker(anonymous(), manager.runtime.broker)
        )
        broker = manager.start(
            session_id,
            lambda: manager.runtime.resume(session_id, body.value, context=context),
        )
        return StreamingResponse(
            _sse(broker.subscribe()), media_type="text/event-stream"
        )

    @app.get(f"{API_PREFIX}/session/{{session_id}}/stream")
    async def stream_session(session_id: str, request: Request) -> StreamingResponse:
        manager = _manager(request)
        _require_session(manager, session_id)
        # Attach only to an in-flight turn. Between turns there is no broker
        # (it is released when the turn ends), so an idle session streams nothing
        # rather than replaying the previous turn's buffered transcript.
        broker = (
            manager.current_broker(session_id)
            if manager.is_active(session_id)
            else None
        )
        events: AsyncIterator[dict] = broker.subscribe() if broker else _empty()
        return StreamingResponse(_sse(events), media_type="text/event-stream")

    @app.get(f"{API_PREFIX}/session/{{session_id}}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        manager = _manager(request)
        _require_session(manager, session_id)
        return await manager.runtime.get_state(session_id)

    # Channels (inbound webhooks) and schedules (timed runs). Names are known at
    # load time, so routes are registered up front; handlers use the live runtime.
    _register_surfaces(app, load_project(config))
    return app


def _register_surfaces(app: FastAPI, loaded) -> None:
    from leve.schedules import run_schedule

    for channel in loaded.channels:
        _register_channel(app, channel.name, channel.adapter)
    for schedule in loaded.schedules:
        _register_schedule(app, schedule, run_schedule)


def _register_channel(app: FastAPI, name: str, adapter) -> None:
    @app.post(f"{API_PREFIX}/channels/{name}/events")
    async def channel_events(request: Request) -> Any:
        raw = await request.body()

        # Verify on raw bytes first, so unauthenticated callers never reach the
        # parser. A missing optional dep (e.g. Discord's PyNaCl) → 503, not 500.
        try:
            verified = adapter.verify(request.headers, raw)
        except ConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not verified:
            raise HTTPException(status_code=401, detail="Invalid channel signature.")

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc

        handshake = adapter.handshake_response(payload)
        if handshake is not None:
            return handshake

        # Drop provider retries (we ack fast below; retries would duplicate runs).
        if adapter.is_retry(request.headers):
            return {"ok": True}

        incoming = adapter.parse(payload)
        if incoming is None:
            return {"ok": True}

        # Ack immediately (within the provider's deadline); run the turn in the
        # background and deliver the reply when it completes.
        _manager(request).spawn_channel_turn(adapter, incoming)
        return {"ok": True}


def _register_schedule(app: FastAPI, schedule, run_schedule) -> None:
    @app.post(f"{API_PREFIX}/schedules/{schedule.name}/run")
    async def schedule_run(request: Request) -> dict[str, bool]:
        _verify_schedule_secret(request)
        await run_schedule(schedule, _manager(request).runtime)
        return {"ok": True}


def _verify_schedule_secret(request: Request) -> None:
    """Require a shared secret on the schedule trigger when one is configured.

    The endpoint drives a real agent run, so it must not be openly triggerable.
    With no ``LEVE_SCHEDULE_SECRET`` set, the route stays open for local dev.
    """

    secret = os.environ.get("LEVE_SCHEDULE_SECRET")
    if not secret:
        return
    provided = request.headers.get("x-leve-schedule-secret", "")
    if not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Invalid schedule secret.")


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
