"""The ``leve`` CLI (SPEC §8).

Typer app exposing the project lifecycle. M1 ships ``init``, ``dev`` and
``build``; later milestones add ``eval``, ``channels``, ``connections`` and
``deploy`` as their features land. Each command is a thin wrapper over the
library so the same operations are scriptable/testable without the CLI.
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from pathlib import Path

import typer

from leve.app import inspect_project
from leve.config import load_config
from leve.errors import LeveError

app = typer.Typer(
    add_completion=False,
    help="Leve — a filesystem-first, durable agent framework built on LangGraph.",
)


@app.command()
def init(
    name: str = typer.Argument(..., help="Project directory name."),
    model: str = typer.Option(
        "anthropic:claude-opus-4-8", help="Default model (provider:model)."
    ),
) -> None:
    """Scaffold a new agent project."""

    from leve.scaffold import scaffold_project

    try:
        created = scaffold_project(Path.cwd() / name, name=name, model=model)
    except LeveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.secho(f"Created project '{name}':", fg=typer.colors.GREEN)
    for path in created:
        typer.echo(f"  {path.relative_to(Path.cwd())}")
    typer.echo(f"\nNext: cd {name} && leve dev")


@app.command()
def build() -> None:
    """Compile and validate the agent graph without serving."""

    try:
        summary = inspect_project(load_config())
    except LeveError as exc:
        typer.secho(f"Build failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.secho(f"✓ Agent '{summary['agent']}' compiles.", fg=typer.colors.GREEN)
    typer.echo(f"  model:        {summary['model']}")
    typer.echo(f"  instructions: {'yes' if summary['instructions'] else 'none'}")
    typer.echo(f"  tools:        {', '.join(summary['tools']) or 'none'}")
    typer.echo(f"  skills:       {', '.join(summary['skills']) or 'none'}")
    typer.echo(f"  connections:  {', '.join(summary['connections']) or 'none'}")
    typer.echo(f"  subagents:    {', '.join(summary['subagents']) or 'none'}")
    typer.echo(f"  sandbox:      {summary['sandbox'] or 'off'}")
    typer.echo(f"  checkpointer: {summary['checkpointer']}")
    typer.echo(f"  store:        {summary['store']}")


@app.command()
def eval() -> None:
    """Run the project's eval suites; exit non-zero if any fail (CI gate)."""

    import asyncio

    from leve.app import run_evals

    try:
        results = asyncio.run(run_evals(load_config()))
    except LeveError as exc:
        typer.secho(f"Eval run failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if not results:
        typer.secho("No evals found (evals/*.eval.py).", fg=typer.colors.YELLOW)
        return

    failed = 0
    for result in results:
        if result.passed:
            typer.secho(f"  ✓ {result.name}", fg=typer.colors.GREEN)
        else:
            failed += 1
            typer.secho(f"  ✗ {result.name}: {result.error}", fg=typer.colors.RED)

    passed = len(results) - failed
    typer.echo(f"\n{passed} passed, {failed} failed")
    if failed:
        raise typer.Exit(1)


channels_app = typer.Typer(help="Manage channel adapters.")
connections_app = typer.Typer(help="Manage connections.")
app.add_typer(channels_app, name="channels")
app.add_typer(connections_app, name="connections")


@channels_app.command("add")
def channels_add(kind: str = typer.Argument(..., help="slack | discord")) -> None:
    """Scaffold a channel adapter file."""

    from leve.scaffold import scaffold_channel

    _scaffold(lambda: scaffold_channel(load_config().agent_dir, kind))


@connections_app.command("add")
def connections_add(name: str = typer.Argument(..., help="Connection name.")) -> None:
    """Scaffold an MCP/OpenAPI connection with an auth stub."""

    from leve.scaffold import scaffold_connection

    _scaffold(lambda: scaffold_connection(load_config().agent_dir, name))


def _scaffold(action) -> None:
    try:
        path = action()
    except LeveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(f"Created {path}", fg=typer.colors.GREEN)


@app.command()
def deploy() -> None:
    """Emit deployment artifacts (langgraph.json, Dockerfile, crontab)."""

    from leve.deploy import write_deploy_artifacts
    from leve.loader import load_project

    try:
        config = load_config()
        written, warnings = write_deploy_artifacts(config, load_project(config))
    except LeveError as exc:
        typer.secho(f"Deploy failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.secho(
        f"Emitted deployment artifacts (target: {config.deploy.target}):",
        fg=typer.colors.GREEN,
    )
    for path in written:
        typer.echo(f"  {path.relative_to(config.project_dir)}")
    for warning in warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)


class DevMode(str, Enum):
    """How ``leve dev`` runs: clean TUI, server only, or a tmux split."""

    tui = "tui"
    server = "server"
    split = "split"


@app.command()
def dev(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    mode: DevMode | None = typer.Option(
        None,
        "--mode",
        help="tui (default): clean chat, server logs → .leve/dev.log. "
        "server: logs to stdout, no chat. split: chat + live logs side-by-side (tmux).",
    ),
    no_tui: bool = typer.Option(
        False,
        "--no-tui",
        hidden=True,
        help="Deprecated alias for --mode server.",
    ),
) -> None:
    """Run the dev server (SQLite checkpointer) and, by default, the TUI client."""

    # Reconcile the deprecated --no-tui alias with --mode.
    if no_tui:
        if mode is not None:
            typer.secho(
                "Pass either --no-tui or --mode, not both.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        mode = DevMode.server
    if mode is None:
        mode = DevMode.tui

    try:
        config = load_config()
    except LeveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if mode is DevMode.server:
        _run_server_mode(config, host, port)
    elif mode is DevMode.split:
        from leve.devlog import run_split

        started = run_split(
            config, host, port, run_client=lambda: _run_tui_mode(config, host, port)
        )
        if not started:
            typer.secho(
                "tmux not found; falling back to --mode tui.",
                fg=typer.colors.YELLOW,
            )
            _run_tui_mode(config, host, port)
    else:
        _run_tui_mode(config, host, port)


def _build_server(config, host: str, port: int, log_config=None):
    """Construct the uvicorn server, optionally with a custom logging config."""

    import uvicorn

    from leve.server import create_app

    return uvicorn.Server(
        uvicorn.Config(
            create_app(config),
            host=host,
            port=port,
            log_level="info",
            log_config=log_config,
        )
    )


def _run_server_mode(config, host: str, port: int) -> None:
    """Serve in the foreground with logs on stdout (no TUI)."""

    _build_server(config, host, port).run()


def _run_tui_mode(config, host: str, port: int) -> None:
    """Serve in the background (logs → .leve/dev.log) and run the TUI client."""

    from leve.devlog import configure_dev_logging

    try:
        log_path, log_config = configure_dev_logging(config)
    except OSError as exc:
        typer.secho(
            f"Could not open log file ({exc}); server logs will stay on stdout.",
            fg=typer.colors.YELLOW,
        )
        log_path, log_config = None, None

    server = _build_server(config, host, port, log_config)

    # TUI needs the terminal, so serve in a background thread and run the client
    # in the foreground; stopping the client shuts the server down.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _await_server_start(server, thread, host, port)

    if log_path is not None:
        typer.secho(
            f"logs → {log_path.relative_to(config.project_dir)}", fg=typer.colors.CYAN
        )

    from leve.tui import run_tui

    try:
        run_tui(f"http://{host}:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def _await_server_start(server, thread: threading.Thread, host: str, port: int) -> None:
    """Block until the server reports started; fail fast if it dies or stalls."""

    deadline = time.monotonic() + 15.0
    while not server.started:
        if not thread.is_alive():
            typer.secho(
                f"Server failed to start on {host}:{port} (is the port in use?).",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        if time.monotonic() > deadline:
            server.should_exit = True
            typer.secho(
                "Server did not start within 15s.", fg=typer.colors.RED, err=True
            )
            raise typer.Exit(1)
        time.sleep(0.05)


if __name__ == "__main__":  # pragma: no cover
    app()
