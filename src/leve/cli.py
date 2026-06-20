"""The ``leve`` CLI (SPEC §8).

Typer app exposing the project lifecycle. M1 ships ``init``, ``dev`` and
``build``; later milestones add ``eval``, ``channels``, ``connections`` and
``deploy`` as their features land. Each command is a thin wrapper over the
library so the same operations are scriptable/testable without the CLI.
"""

from __future__ import annotations

import threading
import time
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
    typer.echo(f"  checkpointer: {summary['checkpointer']}")
    typer.echo(f"  store:        {summary['store']}")


@app.command()
def dev(
    host: str = typer.Option("127.0.0.1", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    tui: bool = typer.Option(True, help="Launch the Textual dev client."),
) -> None:
    """Run the dev server (SQLite checkpointer) and, by default, the TUI client."""

    import uvicorn

    from leve.server import create_app

    try:
        config = load_config()
    except LeveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    server = uvicorn.Server(
        uvicorn.Config(create_app(config), host=host, port=port, log_level="info")
    )

    if not tui:
        server.run()
        return

    # TUI needs the terminal, so serve in a background thread and run the client
    # in the foreground; stopping the client shuts the server down.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for startup, but fail fast if the server thread dies (e.g. port in
    # use) instead of spinning forever.
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
            typer.secho("Server did not start within 15s.", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        time.sleep(0.05)

    from leve.tui import run_tui

    try:
        run_tui(f"http://{host}:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":  # pragma: no cover
    app()
