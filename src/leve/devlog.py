"""Dev-server logging and the ``split`` run mode (SPEC §8).

``leve dev`` runs the HTTP server and the Rich TUI client in one terminal. Left
to its defaults, uvicorn logs to stdout and tangles with the chat transcript.
Two helpers fix that:

* :func:`configure_dev_logging` routes uvicorn's loggers to ``.leve/dev.log`` so
  the foreground TUI stays clean (the only change needed to declutter ``tui``
  mode — the client itself is untouched).
* :func:`run_split` delegates the "logs beside chat" view to ``tmux``: one pane
  runs the clean TUI, the other tails the log file. Each pane is an ordinary
  terminal, so there is no in-process layout to flicker.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from leve.config import LeveConfig

# Rotation bounds: each run starts fresh (``mode="w"``) and a single long
# session is capped at roughly 6 MB (dev.log + .1 + .2).
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 2

# tmux session name used when launching a split from outside an existing server.
_SESSION = "leve-dev"


def dev_log_path(config: LeveConfig) -> Path:
    """Return the path to the dev-server log file (``.leve/dev.log``)."""

    return config.project_dir / ".leve" / "dev.log"


def configure_dev_logging(config: LeveConfig) -> tuple[Path, dict[str, Any]]:
    """Build a uvicorn ``log_config`` that writes to ``.leve/dev.log``.

    Returns the log path and the dict to hand to :class:`uvicorn.Config`. The
    parent directory is created eagerly so an unwritable location fails here
    (where the caller can degrade gracefully) rather than mid-startup.
    """

    log_path = dev_log_path(config)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = {
        "class": "logging.handlers.RotatingFileHandler",
        "formatter": "default",
        "filename": str(log_path),
        "maxBytes": _MAX_BYTES,
        "backupCount": _BACKUP_COUNT,
        "mode": "w",  # truncate on each run
    }
    logger = {"handlers": ["file"], "level": "INFO", "propagate": False}

    log_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
        },
        "handlers": {"file": handler},
        # propagate=False keeps these off the root/stderr stream the TUI uses.
        "loggers": {
            name: dict(logger)
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }
    return log_path, log_config


def run_split(
    config: LeveConfig,
    host: str,
    port: int,
    *,
    run_client: Callable[[], None],
) -> bool:
    """Show chat and live logs side-by-side via ``tmux``.

    Returns ``False`` (without side effects) when ``tmux`` is unavailable so the
    caller can fall back to plain ``tui`` mode. When already inside tmux, a log
    pane is split off and ``run_client`` runs in the current pane; otherwise a
    detached session is created with both panes and attached to.
    """

    tmux = shutil.which("tmux")
    if not tmux:
        return False

    tail_cmd = f"tail -F {shlex.quote(str(dev_log_path(config)))}"

    if os.environ.get("TMUX"):
        # Inside tmux already: add a log pane to the right, keep focus on the
        # current (left) pane, and run the clean TUI here.
        subprocess.run([tmux, "split-window", "-h", tail_cmd], check=True)
        subprocess.run([tmux, "select-pane", "-L"], check=False)
        run_client()
        return True

    # Outside tmux: spawn a detached session whose left pane runs the clean TUI
    # (which owns the server) and whose right pane tails the log, then attach.
    client_cmd = shlex.join(
        [sys.argv[0], "dev", "--mode", "tui", "--host", host, "--port", str(port)]
    )
    subprocess.run([tmux, "new-session", "-d", "-s", _SESSION, client_cmd], check=True)
    subprocess.run([tmux, "split-window", "-h", "-t", _SESSION, tail_cmd], check=True)
    subprocess.run([tmux, "select-pane", "-t", f"{_SESSION}.0"], check=False)
    subprocess.run([tmux, "attach-session", "-t", _SESSION], check=True)
    return True
