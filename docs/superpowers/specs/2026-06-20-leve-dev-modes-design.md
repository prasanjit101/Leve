# `leve dev` run modes — design

**Date:** 2026-06-20
**Status:** Approved (pending spec review)

## Problem

`leve dev` today runs the HTTP server in a background thread and the Rich TUI
client in the foreground (default), or the server alone with `--no-tui`. Because
uvicorn logs to stdout while the TUI prints to the same terminal, server log
lines tangle with the chat transcript — when you send a message you see uvicorn
access/info lines interleaved with the agent's reply.

The goal is to **separate server-log noise from the chat TUI**, and to offer a
clean way to view both side-by-side when wanted.

## Modes

A single `--mode` option on `leve dev` with three values:

| Mode            | Behavior                                                                 |
| --------------- | ------------------------------------------------------------------------ |
| `tui` (default) | Server runs in the background with logs redirected to `.leve/dev.log`; the clean TUI client runs in the foreground. |
| `server`        | Server runs in the foreground, logs to stdout. No TUI. (Today's `--no-tui`.) |
| `split`         | A terminal multiplexer (`tmux`) shows two panes: chat (`--mode tui`) on the left, a live `tail -F .leve/dev.log` on the right. Falls back to `tui` if `tmux` is unavailable. |

**Backward compatibility:** `--no-tui` is retained as a *hidden, deprecated*
boolean flag that maps to `--mode server`, so existing scripts and docs keep
working. Passing both `--no-tui` and an explicit `--mode` is an error.

`--host` and `--port` are unchanged and apply to every mode.

## Architecture

The feature is intentionally small. The key insight from design: routing
uvicorn's logs to a file makes the *existing* TUI clean with no rendering
changes, and delegating the split to a real multiplexer means each pane is an
ordinary scrolling terminal — no in-process layout/`Live` machinery, no flicker,
and the existing client (`tui.py`) is reused untouched.

### 1. CLI dispatch (`leve/cli.py`)

`dev(--mode, --host, --port, --no-tui[hidden])`:

- Validate the mode (Typer `Enum`); reconcile the deprecated `--no-tui` alias.
- `server`: build the uvicorn server as today and call `server.run()` in the
  foreground. No log redirection.
- `tui`: call `configure_dev_logging(config)`, start the server in the
  background thread (existing start-up-wait/fail-fast logic is unchanged), print
  `logs → .leve/dev.log`, then run the TUI client in the foreground. On exit,
  shut the server down (existing `finally` logic).
- `split`: call `run_split(config, host, port)` (below). If the multiplexer is
  unavailable, emit a warning and fall through to `tui`.

### 2. Log redirection (`leve/devlog.py`, new)

`configure_dev_logging(config) -> Path`:

- Resolve `log_path = config.project_dir / ".leve" / "dev.log"`; ensure the
  parent dir exists.
- Build a `logging.config.dictConfig`-style dict that attaches a
  `logging.handlers.RotatingFileHandler` (`maxBytes=2_000_000`,
  `backupCount=2`, `mode="w"` so each run starts fresh) to uvicorn's loggers
  (`uvicorn`, `uvicorn.error`, `uvicorn.access`), with `propagate=False` so
  nothing leaks to the root/stderr stream.
- Return `log_path`.

The CLI passes this dict to uvicorn via `uvicorn.Config(log_config=...)`. Disk
use is bounded both across runs (truncate on start) and within a long session
(rotation ≈ 6 MB max: `dev.log` + `.1` + `.2`).

### 3. Split orchestration (`leve/devlog.py` or a small `leve/split.py`)

`run_split(config, host, port) -> bool` (returns `False` if it could not start a
split, so the caller falls back to `tui`):

- Detect `tmux` on `PATH` (`shutil.which("tmux")`). If absent, return `False`.
- Build the client command from the current entrypoint:
  `[sys.argv[0], "dev", "--mode", "tui", "--host", host, "--port", str(port)]`.
- Build the log command: `tail -F <log_path>` (`-F` retries until the file
  exists, so the race with first-write is handled).
- If already inside tmux (`$TMUX` set): `tmux split-window -h "<tail cmd>"`,
  then run the TUI inline in the current pane.
- Otherwise: create a detached session with the two panes and `attach` to it;
  closing the chat pane tears the server down with it.

Only `tmux` is supported in this iteration (YAGNI); `zellij` is a possible
future extension. Non-multiplexer users get the graceful `tui` fallback.

### 4. TUI client (`leve/tui.py`)

**No changes.** `tui` and `split` both reuse the existing clean `StreamView`
behavior; the only reason it was noisy before was uvicorn logging to the same
stream, which (2) removes.

## Error handling

- `tui`/`split` with an unwritable `.leve/dev.log`: warn and run the TUI with
  logs left on stdout (degraded but functional).
- `split` without `tmux`: warn (`install tmux for split mode`) and fall back to
  `tui`.
- Both `--no-tui` and `--mode` given: error with a clear message and non-zero
  exit.
- Server start-up failure (e.g. port in use): unchanged — the existing
  background-thread death / 15s-timeout checks still apply to `tui`/`split`.

## Testing

Interactive run loops stay `# pragma: no cover`; everything below is unit-tested
without a live terminal:

- `configure_dev_logging`: returns the expected path, creates `.leve/`, and the
  produced config dict attaches a `RotatingFileHandler` to the uvicorn loggers
  with `propagate=False` and the rotation/`mode="w"` settings.
- CLI dispatch (mocked `uvicorn.Server` / `run_tui` / `run_split`):
  - `--mode server` → `server.run()` called, no background thread, no log
    redirect.
  - default / `--mode tui` → `configure_dev_logging` called and the client runs
    against the started server.
  - `--no-tui` → behaves as `--mode server`; `--no-tui` + `--mode` → exits non-zero.
  - `--mode split` with `tmux` absent (mocked `shutil.which` → `None`) → warns
    and falls back to the `tui` path.
- `run_split` builds the correct client/`tail` commands and chooses the
  inside-tmux vs new-session branch based on a mocked `$TMUX` env (subprocess
  calls mocked).

## Out of scope

- A native single-process split (Textual / `prompt_toolkit`) — rejected in favor
  of the multiplexer approach.
- `zellij` and other multiplexers — future extension.
- Changes to the HTTP API or the event stream.
