"""Tests for deployment artifact generation."""

from __future__ import annotations

from pathlib import Path

from leve.agent import define_agent
from leve.channels import ChannelSpec
from leve.config import DeployConfig, LeveConfig
from leve.deploy import crontab, dockerfile, langgraph_json, write_deploy_artifacts
from leve.loader import LoadedAgent
from leve.schedules import define_schedule
from leve.testing import FakeChatModel


def _loaded(name="analyst", schedules=(), channels=()):
    return LoadedAgent(
        name=name,
        path=Path("."),
        spec=define_agent(model=FakeChatModel(responses=["x"])),
        schedules=schedules,
        channels=channels,
    )


def test_langgraph_json_shape():
    payload = langgraph_json(LeveConfig(project_dir=Path(".")), _loaded())
    assert payload["dependencies"] == ["."]
    assert "analyst" in payload["graphs"]


def test_dockerfile_runs_server():
    assert "uvicorn" in dockerfile() and "leve.platform:app" in dockerfile()


def test_crontab_lines():
    @define_schedule(cron="0 9 * * 1")
    async def weekly(ctx):
        pass

    out = crontab(_loaded(schedules=(weekly,)), base_url="http://host:8000")
    assert "0 9 * * 1" in out
    assert "/leve/v1/schedules/weekly/run" in out
    assert "X-Leve-Schedule-Secret" in out  # authenticated trigger


def test_platform_target_emits_langgraph_json(tmp_path):
    config = LeveConfig(project_dir=tmp_path, deploy=DeployConfig(target="langgraph-platform"))
    written, warnings = write_deploy_artifacts(config, _loaded())
    names = {p.name for p in written}
    assert names == {"langgraph.json"}  # no stray Dockerfile
    assert not warnings


def test_docker_target_emits_dockerfile_and_crontab(tmp_path):
    @define_schedule(cron="@daily")
    async def nightly(ctx):
        pass

    config = LeveConfig(project_dir=tmp_path, deploy=DeployConfig(target="docker"))
    written, _ = write_deploy_artifacts(config, _loaded(schedules=(nightly,)))
    names = {p.name for p in written}
    assert names == {"Dockerfile", "leve.crontab"}


def test_platform_with_channels_warns(tmp_path):
    config = LeveConfig(project_dir=tmp_path, deploy=DeployConfig(target="langgraph-platform"))
    _, warnings = write_deploy_artifacts(config, _loaded(channels=(ChannelSpec(adapter=None, name="slack"),)))
    assert warnings and "Channels" in warnings[0]
