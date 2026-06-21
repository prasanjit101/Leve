"""Tests for dev-server logging and the ``leve dev`` run modes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from leve.cli import app
from leve.devlog import configure_dev_logging, dev_log_path, run_split

runner = CliRunner()


def _config(project_dir):
    return SimpleNamespace(project_dir=project_dir)


# --- configure_dev_logging --------------------------------------------------


def test_configure_dev_logging_creates_dir_and_returns_path(tmp_path):
    log_path, log_config = configure_dev_logging(_config(tmp_path))

    assert log_path == tmp_path / ".leve" / "dev.log"
    assert log_path.parent.is_dir()
    assert log_config["version"] == 1


def test_log_config_routes_uvicorn_to_rotating_file(tmp_path):
    log_path, log_config = configure_dev_logging(_config(tmp_path))

    handler = log_config["handlers"]["file"]
    assert handler["class"] == "logging.handlers.RotatingFileHandler"
    assert handler["filename"] == str(log_path)
    assert handler["mode"] == "w"  # truncate on each run
    assert handler["maxBytes"] > 0 and handler["backupCount"] > 0

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = log_config["loggers"][name]
        assert logger["handlers"] == ["file"]
        assert logger["propagate"] is False  # keep logs off the TUI's stream


# --- run_split --------------------------------------------------------------


def test_run_split_returns_false_without_tmux(tmp_path):
    run_client = MagicMock()
    with patch("leve.devlog.shutil.which", return_value=None):
        assert (
            run_split(_config(tmp_path), "127.0.0.1", 8000, run_client=run_client)
            is False
        )
    run_client.assert_not_called()


def test_run_split_inside_tmux_splits_and_runs_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-sock")
    run_client = MagicMock()
    with (
        patch("leve.devlog.shutil.which", return_value="/usr/bin/tmux"),
        patch("leve.devlog.subprocess.run") as sub,
    ):
        result = run_split(_config(tmp_path), "127.0.0.1", 8000, run_client=run_client)

    assert result is True
    run_client.assert_called_once()
    commands = [call.args[0] for call in sub.call_args_list]
    assert any("split-window" in cmd for cmd in commands)
    # No new detached session when already inside tmux.
    assert not any("new-session" in cmd for cmd in commands)


def test_run_split_outside_tmux_creates_session(tmp_path, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    run_client = MagicMock()
    with (
        patch("leve.devlog.shutil.which", return_value="/usr/bin/tmux"),
        patch("leve.devlog.subprocess.run") as sub,
    ):
        result = run_split(_config(tmp_path), "127.0.0.1", 8000, run_client=run_client)

    assert result is True
    run_client.assert_not_called()  # the spawned pane runs the client
    commands = [call.args[0] for call in sub.call_args_list]
    assert any("new-session" in cmd for cmd in commands)
    assert any("attach-session" in cmd for cmd in commands)
    # The chat pane re-invokes the CLI in clean tui mode.
    new_session = next(cmd for cmd in commands if "new-session" in cmd)
    assert "--mode tui" in new_session[-1]
    # And the log pane tails the dev log.
    assert any(str(dev_log_path(_config(tmp_path))) in cmd[-1] for cmd in commands)


# --- CLI dispatch -----------------------------------------------------------


def _patch_dispatch():
    """Patch config loading and the three mode runners for dispatch tests."""

    return (
        patch("leve.cli.load_config", return_value=MagicMock()),
        patch("leve.cli._run_server_mode"),
        patch("leve.cli._run_tui_mode"),
    )


def test_dev_defaults_to_tui_mode():
    load, server, tui = _patch_dispatch()
    with load, server as srv, tui as cli_tui:
        result = runner.invoke(app, ["dev"])
    assert result.exit_code == 0
    cli_tui.assert_called_once()
    srv.assert_not_called()


def test_dev_mode_server_runs_server_only():
    load, server, tui = _patch_dispatch()
    with load, server as srv, tui as cli_tui:
        result = runner.invoke(app, ["dev", "--mode", "server"])
    assert result.exit_code == 0
    srv.assert_called_once()
    cli_tui.assert_not_called()


def test_dev_no_tui_alias_maps_to_server():
    load, server, tui = _patch_dispatch()
    with load, server as srv, tui as cli_tui:
        result = runner.invoke(app, ["dev", "--no-tui"])
    assert result.exit_code == 0
    srv.assert_called_once()
    cli_tui.assert_not_called()


def test_dev_no_tui_with_mode_is_an_error():
    load, server, tui = _patch_dispatch()
    with load, server, tui:
        result = runner.invoke(app, ["dev", "--no-tui", "--mode", "tui"])
    assert result.exit_code == 1
    assert "not both" in result.output


def test_dev_split_falls_back_to_tui_without_tmux():
    load, server, tui = _patch_dispatch()
    with (
        load,
        server,
        tui as cli_tui,
        patch("leve.devlog.run_split", return_value=False) as split,
    ):
        result = runner.invoke(app, ["dev", "--mode", "split"])
    assert result.exit_code == 0
    split.assert_called_once()
    cli_tui.assert_called_once()
    assert "falling back" in result.output
