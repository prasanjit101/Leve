# Leve

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

**A filesystem-first, durable agent framework built on LangGraph.**

You describe an agent as a _directory of files_. Leve compiles that directory
into a LangGraph graph and runs it with durability, approvals, subagents,
connections, sandboxed compute, channels, schedules, evals, and per-caller
security already wired in. See [SPEC.md](SPEC.md) for the full design.

The core idea: there is no framework boilerplate to learn. You add a file, and
Leve discovers it by convention — a Python file under `tools/` becomes a tool, a
markdown file under `skills/` becomes an on-demand skill, a folder under
`subagents/` becomes a delegate. The same `agent/` directory runs unchanged from
your laptop to production; only the adapters in `leve.toml` and the environment
change.

```text
agent/
  agent.py            # the model + config it runs on
  instructions.md     # who it is (system prompt, with {{ templating }})
  tools/run_sql.py    # what it can do (@define_tool)
  skills/revenue.md   # what it knows (loaded on demand)
  subagents/…/        # who it delegates to (compiled as subgraphs)
  connections/…       # MCP / OpenAPI servers it can reach
  channels/slack.py   # where it lives
  schedules/…         # when it acts on its own
evals/*.eval.py       # how it is tested
leve.toml             # project config (adapters, backends)
```

## What you get

Everything below is wired in once you compile the directory — no glue code:

- **Durable runs** — every turn is checkpointed (SQLite locally, Postgres in
  prod), so sessions survive restarts and pause for free on interrupts.
- **Approvals & consent** — tools can require human approval; missing
  credentials raise a checkpointed consent interrupt instead of failing.
- **Subagents** — folders under `subagents/` compile into subgraphs the agent
  can delegate to.
- **Connections** — reach MCP and OpenAPI servers as tools the model can call.
- **Sandboxed compute** — agent-written code runs in microVM isolation with no
  ambient credentials.
- **Channels & schedules** — serve the same agent over Slack/Discord, or let it
  act on its own via cron.
- **Per-caller security** — the runtime enforces the asker's permissions
  outside the model; identity is never model-visible or forgeable.
- **Evals** — drive the compiled agent in-process and assert on its event
  stream as a CI gate.

## Quickstart

```bash
uv sync                  # install
uv run leve init myagent # scaffold a project
cd myagent
uv run leve dev          # dev server + TUI client
uv run leve build        # compile & validate without serving
uv run leve eval         # run eval suites (CI gate)
uv run leve deploy       # emit deploy artifacts
```

`leve dev` runs a local HTTP server with a TUI client over it, defaulting to a
SQLite checkpointer so sessions persist across restarts. Add capabilities at any
time without touching config:

```bash
uv run leve channels add slack          # writes agent/channels/slack.py
uv run leve connections add linear      # writes agent/connections/linear.py
```

## Documentation

- [Local development & testing](docs/local-development.md) — install, scaffold,
  run the dev server, drive the HTTP API, and write evals. Everything runs
  offline with SQLite + in-memory backends.
- [Production setup & operations](docs/production.md) — durable backends,
  deploy targets (LangGraph Platform or Docker), channels, schedules, and the
  multi-tenant security model.
- [SPEC.md](SPEC.md) — the full design and rationale.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, development workflow, and coding standards.

## Code of Conduct

Everyone participating in the Leve community is expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Leve is released under the [MIT License](LICENSE).
