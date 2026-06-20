# Leve

**A filesystem-first, durable agent framework built on LangGraph.**

You describe an agent as a *directory of files*. Leve compiles that directory
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

## Status

All five milestones from the spec are implemented:

- **M1 — Core loop:** loader, `define_agent`, instructions, tools, ReAct graph
  (LangChain v1 `create_agent` + middleware), SQLite checkpointer, HTTP API, TUI.
- **M2 — Production primitives:** human-in-the-loop approvals, skills, context
  compaction, Postgres checkpointer/store, LangSmith/OTel tracing.
- **M3 — Reach:** MCP/OpenAPI connections, pluggable sandbox adapters
  (microsandbox default, subprocess dev tier), subagents as subgraphs.
- **M4 — Surfaces & ops:** Slack/Discord/HTTP channels, schedules, evals,
  `leve deploy` (LangGraph Platform / Docker).
- **M5 — Multi-tenant security:** `Principal`, injected-arg tool auth, pluggable
  credential broker with consent-as-interrupt, per-caller scoping.

Optional extras: `leve[postgres]`, `leve[microsandbox]`, `leve[discord]`.
