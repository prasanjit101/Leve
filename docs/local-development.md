# Local Development & Testing

How to install Leve, scaffold and run an agent locally, and test it. Everything
here runs offline with no cloud dependencies (SQLite + in-memory backends).

---

## 1. Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** (used for env + dependency management)
- A model provider key for real runs (the default model is Anthropic):
  ```bash
  export ANTHROPIC_API_KEY=sk-ant-...
  ```
  Not needed for the test suite — tests use `leve.testing.FakeChatModel`.

## 2. Install

From the framework repo:

```bash
uv sync                 # core dependencies
uv sync --extra dev     # + pytest / pytest-asyncio (to run the test suite)
```

Optional extras (install only what you use):

| Extra | Enables |
| ----- | ------- |
| `leve[postgres]` | Postgres checkpointer + store (`langgraph-checkpoint-postgres`, `psycopg`) |
| `leve[microsandbox]` | microVM sandbox adapter (`microsandbox`, needs the microsandbox server) |
| `leve[discord]` | Discord channel adapter (`pynacl` for Ed25519 verification) |
| `leve[google]` | Google Gemini models (`langchain-google-genai`; use `google_genai:<model>` strings) |

```bash
uv sync --extra postgres --extra dev
```

## 3. Scaffold a project

```bash
uv run leve init myagent
cd myagent
```

This writes a runnable project:

```text
myagent/
  leve.toml                       # project config
  .gitignore
  agent/
    agent.py                      # define_agent(model="anthropic:claude-opus-4-8")
    instructions.md               # system prompt (supports {{ current_date }})
    tools/current_time.py         # example @define_tool
    skills/answering-style.md     # example skill (loaded on demand)
```

Add capabilities by adding files (no registration needed):

```bash
uv run leve channels add slack          # writes agent/channels/slack.py
uv run leve channels add discord        # writes agent/channels/discord.py
uv run leve connections add linear      # writes agent/connections/linear.py (MCP stub)
```

`tools/`, `skills/`, `connections/`, `channels/`, `schedules/`, and
`subagents/<name>/` are all discovered by convention from the `agent/` tree.

## 4. Compile / validate (no serving)

```bash
uv run leve build
```

Loads the project, compiles the graph, and prints a summary (model, tools,
skills, connections, subagents, sandbox, backends). It raises on any error, so
it doubles as a fast structural check in CI.

## 5. Run it (dev server + TUI)

```bash
uv run leve dev                     # serves on 127.0.0.1:8000 and opens the TUI
uv run leve dev --no-tui            # server only (for curl / scripts)
uv run leve dev --host 0.0.0.0 --port 9000
```

`leve dev` uses the SQLite checkpointer by default (`./.leve/checkpoints.sqlite`),
so sessions survive restarts. The TUI is just a client over the HTTP API.

### Driving the HTTP API directly

```bash
# Create a session
SID=$(curl -s -XPOST localhost:8000/leve/v1/session | jq -r .session_id)

# Send a message — the response body is the SSE event stream for the turn
curl -N -XPOST localhost:8000/leve/v1/session/$SID/message \
  -H 'content-type: application/json' -d '{"message":"hello"}'

# Resume an approval/consent interrupt
curl -N -XPOST localhost:8000/leve/v1/session/$SID/resume \
  -H 'content-type: application/json' -d '{"value":{"approved":true}}'

# Fetch session state / history
curl -s localhost:8000/leve/v1/session/$SID | jq
```

Event stream schema (normalized): `turn.start`, `model.delta` (streaming
tokens), `model.message` (full reply), `tool.call`, `tool.result`,
`approval.requested`, `error`, `turn.end` (`interrupted` / `errored` flags).

## 6. Configuration for local dev

`leve.toml` selects adapters; everything has a local-friendly default.

```toml
[agent]
root = "agent"
default_model = "anthropic:claude-opus-4-8"

[persistence]
checkpointer = "sqlite"          # sqlite | memory | postgres
store = "memory"                 # sqlite | memory | postgres
# store_sqlite_path = ".leve/store.sqlite"   # used when store = "sqlite"

[credentials]
broker = "oauth_store"           # static | oauth_store | token_exchange

[sandbox]
adapter = "microsandbox"         # microsandbox | subprocess | (docker/e2b/modal: roadmap)

[sandbox.limits]
timeout_sec = 120
memory_mb = 1024
network = "deny"

[tracing]
provider = "langsmith"           # langsmith | otel | none
project = "myagent"
```

For pure-local dev without microsandbox installed, set `adapter = "subprocess"`
(no isolation — dev escape hatch only) and `broker = "static"`.

A few values can be overridden by environment variable (handy in CI):
`LEVE_CHECKPOINTER`, `LEVE_STORE`, `LEVE_POSTGRES_URL`, `LEVE_AGENT_ROOT`,
`LEVE_DEFAULT_MODEL`. Everything else lives in `leve.toml`.

---

## 7. Testing

### Running the framework's own tests

```bash
uv run pytest            # full suite (offline, no API key needed)
uv run pytest -q tests/test_auth.py::test_static_broker
```

`pyproject.toml` sets `asyncio_mode = "auto"`, so `async def test_*` functions
run directly.

### Writing evals for your agent (`evals/<name>.eval.py`)

Evals drive your compiled agent in-process and assert on the event stream:

```python
from leve.evals import define_eval
from leve.evals.expect import includes

@define_eval(description="The analyst answers revenue questions by the rules.")
async def revenue(t):
    await t.send("What was revenue last week?")
    t.completed()                       # no error / interrupt
    t.no_errors()
    t.called_tool("run_sql")            # a tool was invoked
    t.loaded_skill("revenue")           # a skill was loaded
    t.check(t.reply, includes("net of refunds"))
```

Run them (non-zero exit on failure → use as a CI gate):

```bash
uv run leve eval
```

Each eval runs against a **fresh runtime** (isolated model state + thread).
Matchers live in `leve.evals.expect`: `includes`, `excludes`, `equals`,
`matches` (regex).

### Unit-testing your tools without a model

Use `leve.testing.FakeChatModel` to script deterministic responses — including
tool calls — and inject it via `define_agent(model=...)`:

```python
from langchain_core.messages import AIMessage
from leve.testing import FakeChatModel

model = FakeChatModel(responses=[
    AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {"sql": "SELECT 1"}, "id": "c1"}]),
    "done",
])
```

It implements `bind_tools`, replays one response per model call, and records the
messages it received (`model.calls`) so you can assert on the rendered prompt.

### Tips

- Set `checkpointer = "memory"` / `store = "memory"` in a test `leve.toml` to
  avoid SQLite files.
- The subprocess sandbox runs with a **scrubbed environment** — your
  `LEVE_CRED_*` secrets are not visible to agent-run shell commands.
- Approvals and credential consent both surface as `approval.requested` events
  and resume via the `/resume` endpoint (or `rt.resume(...)` in-process).
