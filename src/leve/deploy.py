"""Deployment artifact generation (SPEC §10).

``leve deploy`` targets either LangGraph Platform (emit ``langgraph.json``) or a
self-host container (emit a ``Dockerfile``). Schedules become Platform Crons or,
self-hosted, crontab lines that hit the schedule endpoint. These functions are
pure (config/loaded-agent in, text out) so the emitted artifacts are testable
without running a deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

from leve.config import LeveConfig
from leve.loader import LoadedAgent

# Entry point exposing the compiled graph to LangGraph Platform. Leve ships a
# module-level `graph` built from leve.toml so Platform can import it directly.
_GRAPH_ENTRYPOINT = "leve.platform:graph"


def langgraph_json(config: LeveConfig, loaded: LoadedAgent) -> dict:
    """Build the ``langgraph.json`` describing the deployment."""

    return {
        "dependencies": ["."],
        "graphs": {loaded.name: _GRAPH_ENTRYPOINT},
        "env": ".env",
    }


def dockerfile() -> str:
    """A self-host container running the FastAPI server + compiled graph."""

    return (
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY . /app\n"
        "RUN pip install --no-cache-dir .\n"
        'ENV LEVE_CHECKPOINTER=postgres\n'
        "EXPOSE 8000\n"
        'CMD ["uvicorn", "leve.platform:app", "--host", "0.0.0.0", "--port", "8000"]\n'
    )


def crontab(loaded: LoadedAgent, *, base_url: str) -> str:
    """Emit crontab lines hitting the schedule endpoint, one per schedule.

    Each line carries the schedule secret header (resolved from the environment
    at run time) so the trigger endpoint authenticates the caller.
    """

    base = base_url.rstrip("/")
    secret = '-H "X-Leve-Schedule-Secret: $LEVE_SCHEDULE_SECRET" '
    lines = [
        f"{s.cron} curl -fsS -X POST {secret}{base}/leve/v1/schedules/{s.name}/run"
        for s in loaded.schedules
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def write_deploy_artifacts(config: LeveConfig, loaded: LoadedAgent) -> list[Path]:
    """Write the artifacts for the configured deploy target. Returns written paths.

    Platform target → ``langgraph.json``; docker/self-host → ``Dockerfile`` +
    (when there are schedules) a crontab. Channels are served only by the
    self-host app, so a Platform deploy with channels is surfaced as a warning.
    """

    written: list[Path] = []
    project = config.project_dir
    warnings: list[str] = []

    if config.deploy.target == "langgraph-platform":
        json_path = project / "langgraph.json"
        json_path.write_text(json.dumps(langgraph_json(config, loaded), indent=2) + "\n")
        written.append(json_path)
        if loaded.channels:
            warnings.append(
                "Channels are served only by the self-host app; the Platform "
                "target does not expose channel webhooks. Use target='docker' "
                "to serve channels."
            )
    else:  # docker / self-host
        dockerfile_path = project / "Dockerfile"
        dockerfile_path.write_text(dockerfile())
        written.append(dockerfile_path)
        if loaded.schedules:
            cron_path = project / "leve.crontab"
            cron_path.write_text(crontab(loaded, base_url=config.deploy.base_url))
            written.append(cron_path)

    return written, warnings
