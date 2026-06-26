"""Tests for deployment artifact generation."""

from __future__ import annotations

from pathlib import Path

from leve.channels import ChannelSpec
from leve.config import DeployConfig, LeveConfig, PersistenceConfig, SandboxConfig
from leve.core.agent import define_agent
from leve.deploy import crontab, dockerfile, langgraph_json, write_deploy_artifacts
from leve.loader import LoadedAgent
from leve.schedules import define_schedule
from leve.testing import FakeChatModel


def _loaded(name="analyst", schedules=(), channels=(), model=None):
    return LoadedAgent(
        name=name,
        path=Path("."),
        spec=define_agent(model=model or FakeChatModel(responses=["x"])),
        schedules=schedules,
        channels=channels,
    )


def test_langgraph_json_shape():
    payload = langgraph_json(LeveConfig(project_dir=Path(".")), _loaded())
    assert payload["dependencies"] == ["."]
    assert "analyst" in payload["graphs"]


def _config(tmp=Path("."), **kw):
    return LeveConfig(project_dir=tmp, deploy=DeployConfig(target="docker"), **kw)


def test_dockerfile_runs_server():
    out = dockerfile(_config(), _loaded())
    assert "uvicorn" in out and "leve.platform:app" in out


def test_dockerfile_does_not_hardcode_a_backend():
    # The backend must come from leve.toml/env at runtime, not be baked in —
    # otherwise a documented sqlite self-host deploy is silently forced to postgres.
    assert "LEVE_CHECKPOINTER" not in dockerfile(_config(), _loaded())


def test_dockerfile_installs_postgres_extra_when_selected():
    config = _config(
        persistence=PersistenceConfig(checkpointer="postgres", postgres_url="x")
    )
    out = dockerfile(config, _loaded())
    assert "postgres" in out  # the extra is installed so psycopg is present


def test_dockerfile_installs_store_postgres_extra():
    config = _config(persistence=PersistenceConfig(store="postgres", postgres_url="x"))
    assert "postgres" in dockerfile(config, _loaded())


def test_dockerfile_installs_microsandbox_extra_by_default():
    # microsandbox is the default sandbox adapter — its driver must be installed.
    assert "microsandbox" in dockerfile(_config(), _loaded())


def test_dockerfile_omits_microsandbox_extra_for_subprocess():
    config = _config(sandbox=SandboxConfig(adapter="subprocess"))
    assert "microsandbox" not in dockerfile(config, _loaded())


def test_dockerfile_installs_discord_extra_when_channel_present():
    config = _config(sandbox=SandboxConfig(adapter="subprocess"))
    out = dockerfile(
        config, _loaded(channels=(ChannelSpec(adapter=None, name="discord"),))
    )
    assert "discord" in out


def test_dockerfile_installs_google_extra_for_gemini_model():
    config = _config(sandbox=SandboxConfig(adapter="subprocess"))
    config = LeveConfig(
        project_dir=Path("."),
        deploy=DeployConfig(target="docker"),
        default_model="google_genai:gemini-2.5-pro",
        sandbox=SandboxConfig(adapter="subprocess"),
    )
    assert "google" in dockerfile(config, _loaded())


def test_dockerfile_plain_install_when_no_extras_needed():
    config = _config(
        persistence=PersistenceConfig(checkpointer="sqlite", store="memory"),
        sandbox=SandboxConfig(adapter="subprocess"),
    )
    out = dockerfile(config, _loaded())
    assert "pip install --no-cache-dir ." in out  # no extras bracket


def test_crontab_lines():
    @define_schedule(cron="0 9 * * 1")
    async def weekly(ctx):
        pass

    out = crontab(_loaded(schedules=(weekly,)), base_url="http://host:8000")
    assert "0 9 * * 1" in out
    assert "/leve/v1/schedules/weekly/run" in out
    assert "X-Leve-Schedule-Secret" in out  # authenticated trigger


def test_platform_target_emits_langgraph_json(tmp_path):
    config = LeveConfig(
        project_dir=tmp_path, deploy=DeployConfig(target="langgraph-platform")
    )
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
    config = LeveConfig(
        project_dir=tmp_path, deploy=DeployConfig(target="langgraph-platform")
    )
    _, warnings = write_deploy_artifacts(
        config, _loaded(channels=(ChannelSpec(adapter=None, name="slack"),))
    )
    assert warnings and "Channels" in warnings[0]
