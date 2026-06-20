"""Schedules — ``agent/schedules/<name>.py`` (SPEC §4.8).

A schedule is a cron expression plus a handler that starts a session on the
agent's own clock. The handler receives a context whose ``receive`` drives the
agent and optionally delivers the result to a channel. Scheduled runs have no
human asker, so they execute under an explicit app principal (``ctx.app_auth``,
realized in M5) rather than any user's permissions.

On LangGraph Platform a schedule deploys as a Cron; self-hosted, Leve emits a
crontab/APScheduler entry that hits the schedule endpoint (see ``leve.deploy``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from leve.errors import LoaderError

if TYPE_CHECKING:  # avoid a runtime import cycle (session → loader → …)
    from leve.channels import ChannelSpec
    from leve.session import AgentRuntime


@dataclass(frozen=True)
class ScheduleSpec:
    """A described schedule: cron expression + handler."""

    name: str
    cron: str
    func: Callable[["ScheduleContext"], Awaitable[None]]


def define_schedule(*, cron: str, name: str | None = None):
    """Mark an async handler as a scheduled run (SPEC §4.8)."""

    _validate_cron(cron)

    def wrap(func: Callable[["ScheduleContext"], Awaitable[None]]) -> ScheduleSpec:
        return ScheduleSpec(name=name or func.__name__, cron=cron, func=func)

    return wrap


class ScheduleContext:
    """The ``ctx`` handed to a schedule handler."""

    def __init__(self, name: str, runtime: "AgentRuntime", app_auth: Any = None):
        self._name = name
        self._runtime = runtime
        # The app principal a scheduled run executes under (M5). Carried here so
        # handlers can pass it explicitly to receive().
        self.app_auth = app_auth

    async def receive(
        self,
        channel: "ChannelSpec | None" = None,
        *,
        message: str,
        target: dict[str, Any] | None = None,
        auth: Any = None,
    ) -> str:
        """Start a fresh session, run the message, and deliver any reply."""

        from leve.session import extract_reply

        session_key = f"schedule:{self._name}:{uuid.uuid4().hex}"
        events = [event async for event in self._runtime.run(session_key, message)]
        reply = extract_reply(events)
        if channel is not None:
            await channel.adapter.deliver(target or {}, reply)
        return reply


async def run_schedule(
    spec: ScheduleSpec, runtime: "AgentRuntime", *, app_auth: Any = None
) -> None:
    """Execute a schedule's handler once."""

    await spec.func(ScheduleContext(spec.name, runtime, app_auth))


def _validate_cron(cron: str) -> None:
    # Accept the 5-field POSIX form, the 6-field (seconds) form, and the common
    # @macros (@daily, @hourly, …) that both crontab and Platform Crons support.
    cron = cron.strip()
    if cron.startswith("@") or len(cron.split()) in (5, 6):
        return
    raise LoaderError(
        f"Invalid cron expression '{cron}': expected 5- or 6-field cron or an "
        "@macro (e.g. '0 9 * * 1' or '@daily')."
    )
