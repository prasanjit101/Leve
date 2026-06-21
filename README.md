# Leve

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/prasanjit101/Leve/actions/workflows/ci.yml/badge.svg)](https://github.com/prasanjit101/Leve/actions/workflows/ci.yml)

**A filesystem-first, durable agent framework built on LangGraph.**

You describe an agent as a _directory of files_. Leve compiles that directory
into a LangGraph graph and runs it with durability, approvals, subagents,
connections, sandboxed compute, channels, schedules, evals, and per-caller
security already wired in. See [SPEC.md](SPEC.md) for the full design.

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

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, development workflow, and coding standards.

## Code of Conduct

Everyone participating in the Leve community is expected to follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Leve is released under the [MIT License](LICENSE).
