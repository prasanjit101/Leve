"""Tests for schedules: definition, validation, discovery, execution."""

from __future__ import annotations

import pytest

from leve.channels import ChannelSpec
from leve.errors import LoaderError
from leve.loader import load_project
from leve.schedules import ScheduleSpec, define_schedule, run_schedule
from leve.testing import FakeChatModel
from tests.conftest import runtime_for

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""


def test_define_schedule():
    @define_schedule(cron="0 9 * * 1")
    async def weekly(ctx):
        pass

    assert isinstance(weekly, ScheduleSpec)
    assert weekly.name == "weekly" and weekly.cron == "0 9 * * 1"


def test_invalid_cron_raises():
    with pytest.raises(LoaderError):
        define_schedule(cron="not-cron")(lambda ctx: None)


async def test_run_schedule_drives_runtime_and_delivers(make_loaded):
    delivered: dict = {}

    class FakeAdapter:
        def parse(self, payload):
            return None

        async def deliver(self, target, text):
            delivered["target"], delivered["text"] = target, text

    channel = ChannelSpec(adapter=FakeAdapter(), name="fake")

    @define_schedule(cron="0 9 * * 1")
    async def weekly(ctx):
        await ctx.receive(channel, message="summarize", target={"channel": "C1"})

    loaded = make_loaded(FakeChatModel(responses=["weekly summary"]))
    async with runtime_for(loaded) as rt:
        await run_schedule(weekly, rt)

    assert delivered == {"target": {"channel": "C1"}, "text": "weekly summary"}


async def test_schedule_runs_under_app_principal(make_loaded):
    from langchain_core.messages import AIMessage

    from leve.security.auth import InjectedPrincipal, Principal
    from leve.tools import define_tool

    seen: list = []

    @define_tool(description="Record the caller.")
    async def whoami(principal: Principal = InjectedPrincipal()) -> str:
        seen.append(
            (principal.subject, principal.claims.get("app")) if principal else None
        )
        return "ok"

    @define_schedule(cron="@daily")
    async def nightly(ctx):
        await ctx.receive(message="run")

    model = FakeChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": "whoami", "args": {}, "id": "s1"}]
            ),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(whoami,))
    async with runtime_for(loaded) as rt:
        await run_schedule(nightly, rt)

    # The scheduled run executed under an explicit app principal, not None/anonymous.
    assert seen and seen[0][1] is True and seen[0][0] == "schedule:nightly"


def test_schedule_discovery(tmp_path, write_project):
    schedule = """\
        from leve.schedules import define_schedule

        @define_schedule(cron="0 9 * * 1")
        async def weekly(ctx):
            pass
    """
    config = write_project(
        tmp_path, agent_py=AGENT_PY, extra_files={"agent/schedules/weekly.py": schedule}
    )
    loaded = load_project(config)
    assert [s.name for s in loaded.schedules] == ["weekly"]
