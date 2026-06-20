# Leve — Specification

**A filesystem-first, durable agent framework built on LangGraph.**

Status: Draft v0.1 · Date: 2026-06-20

---

## 1. Vision

Leve is the LangGraph-native counterpart to Vercel's *eve*. It is built on one
idea: **building an agent should mean defining what it does, not assembling the
plumbing it needs to run in production.**

You describe an agent as a **directory of files**. Leve compiles that directory
into a LangGraph `StateGraph` and runs it with durability, sandboxing,
human-in-the-loop approvals, subagents, evals, and tracing already wired in.

Where eve owns the agent loop on top of the Vercel/Workflow SDK, Leve owns it on
top of **LangGraph + LangGraph Platform**, so every production concern maps to a
first-class LangGraph primitive rather than custom infrastructure:

| Production concern         | eve mechanism              | Leve mechanism (LangGraph)                          |
| -------------------------- | -------------------------- | --------------------------------------------------- |
| Durable execution          | Workflow SDK checkpoints   | LangGraph **checkpointers** (Postgres/SQLite/Redis) |
| Human-in-the-loop          | `needsApproval` + pause    | `interrupt()` + `Command(resume=…)`                 |
| Subagents                  | `subagents/` directory     | **Subgraphs** invoked as tools                      |
| Long-running / resume      | Durable session            | Thread-scoped checkpointed state                    |
| Scheduling                 | Vercel Cron                | LangGraph Platform **Crons** / external scheduler   |
| Tracing & evals            | OpenTelemetry + eve evals  | **LangSmith** traces + datasets + evaluators        |
| Connections / tools        | MCP + OpenAPI files        | **`langchain-mcp-adapters`** + tool files           |
| Sandboxed compute          | Vercel Sandbox adapter     | Pluggable **`Sandbox` adapter** (microsandbox/OS-native/E2B) |
| Deployment                 | `vercel deploy`            | LangGraph Platform deploy / self-host container     |

---

## 2. Core principle — an agent is a directory

```text
agent/
  agent.py                   # the model + config it runs on
  instructions.md            # who it is (system prompt)
  tools/
    run_sql.py               # what it can do
    post_chart.py
  skills/
    revenue-definitions.md   # what it knows (loaded on demand)
  subagents/
    investigator/            # who it delegates to (same shape, one level down)
      agent.py
      instructions.md
      tools/
  connections/
    linear.py                # MCP / OpenAPI servers it can reach
  channels/
    slack.py                 # where it lives
  schedules/
    monday_summary.py        # when it acts on its own
evals/
  revenue.eval.py            # how it is tested
leve.toml                    # project config (model defaults, adapters)
```

The file's **name and place in the tree are its definition.** Leve discovers
each component at build time via convention-based loading and compiles it into a
runnable graph. No registration, no manual wiring of the agent loop.

> Just as Next.js turns a folder into a route by owning routing, Leve turns a
> file into a capability by owning the LangGraph agent loop.

---

## 3. Technology stack

- **Language:** Python 3.11+ **only**. Leve is a pure-Python framework — there
  is no TypeScript/JavaScript runtime or port. This matches LangGraph's primary
  ecosystem, where durability, the Store, MCP adapters, and Platform are most
  mature.
- **Graph runtime:** `langgraph` (`StateGraph`, prebuilt `create_react_agent`).
- **Model layer:** `langchain` chat models via the provider-agnostic
  `init_chat_model("anthropic:claude-opus-4-8")`, with fallback chains.
- **Persistence:** `langgraph-checkpoint-postgres` (prod),
  `langgraph-checkpoint-redis` (prod, low-latency), `langgraph-checkpoint-sqlite`
  (local), `MemorySaver` (tests).
- **Long-term memory:** LangGraph **Store** (`InMemoryStore` / `PostgresStore`).
- **Tools / connections:** `@define_tool` decorator (wrapping LangChain
  `StructuredTool`) + `langchain-mcp-adapters` for MCP.
- **Tracing & evals:** LangSmith (OTel export supported for Datadog/Honeycomb/Jaeger).
- **CLI:** `leve` (Typer-based), with a Textual TUI dev client.
- **Packaging:** distributed as the `leve` PyPI package.

---

## 4. Component specifications

### 4.1 Agent definition — `agent/agent.py`

The minimal agent:

```python
from leve import define_agent

agent = define_agent(
    model="anthropic:claude-opus-4-8",
)
```

`define_agent` accepts:

| Field          | Type                              | Default                  | Notes                                              |
| -------------- | --------------------------------- | ------------------------ | -------------------------------------------------- |
| `model`        | `str \| BaseChatModel`            | required                 | `provider:model` string or a LangChain model.      |
| `fallbacks`    | `list[str]`                       | `[]`                     | Provider/model fallback chain (gateway-style).     |
| `description`  | `str`                             | `""`                     | Required when used as a subagent.                  |
| `model_options`| `dict`                            | `{}`                     | temperature, max_tokens, etc.                      |
| `compaction`   | `CompactionConfig \| None`        | auto                     | Context-window summarization policy.               |
| `recursion_limit` | `int`                          | `25`                     | LangGraph step ceiling per turn.                   |
| `checkpointer` | `BaseCheckpointSaver \| None`     | from `leve.toml`         | Overridable per agent.                             |
| `store`        | `BaseStore \| None`               | from `leve.toml`         | Long-term memory backend.                          |

`define_agent` returns a thin descriptor; the **compiled graph** is produced by
the loader (§6), which assembles model + instructions + tools + skills +
subagents into a `create_react_agent`-style ReAct loop wrapped in Leve's
durable session graph.

### 4.2 Instructions — `agent/instructions.md`

Plain markdown. Becomes the **system prompt** prepended to every model call.
Supports lightweight templating for **non-sensitive** runtime values (e.g.
current date, channel name) via `{{ ... }}` placeholders resolved from session
state and run metadata. The caller's `Principal` (§5.6) is deliberately **not** exposed here —
identity lives in runtime context, never in the prompt, so the model cannot read,
reason about, or leak it. (A tool that legitimately needs the caller's id
receives it as an injected argument; see §5.6.)

### 4.3 Tools — `agent/tools/<name>.py`

A tool is one file. Decorating a function with `@define_tool` turns it into the
file's tool (Leve's equivalent of eve's `export default defineTool({…})`); the
loader collects the resulting object **by type**, so the symbol name is
irrelevant and the function name doubles as the tool name:

```python
from leve.tools import define_tool
from pydantic import BaseModel, Field
from ..lib.sample_db import run_readonly_sql

class RunSqlInput(BaseModel):
    sql: str = Field(description="A single read-only SELECT statement.")

@define_tool(
    description="Run a read-only SQL query against the orders and customers tables.",
    input_schema=RunSqlInput,
)
async def run_sql(sql: str):
    columns, rows = await run_readonly_sql(sql)
    return {"columns": columns, "rows": rows[:500], "truncated": len(rows) > 500}
```

- The decorated function's name (and the file name `run_sql.py`) is the tool
  name unless overridden in `define_tool`.
- `input_schema` is a Pydantic model (Leve's Zod equivalent); it generates the
  JSON schema handed to the model.
- `define_tool` wraps the function as a LangChain `StructuredTool` and registers
  it on the agent's tool node automatically — no manual registration.

**Human-in-the-loop approval** is one field. The predicate receives the tool
input **and the caller's `Principal`** (§5.6), so approval policy can be
identity-aware, not just input-aware:

```python
@define_tool(
    description="Run a SQL query against the warehouse.",
    input_schema=RunSqlInput,
    # Big scans always need a human; smaller scans only when the caller isn't an admin.
    needs_approval=lambda tool_input, principal: (
        estimate_scan_gb(tool_input.sql) > 50 or "admin" not in principal.claims["roles"]
    ),
)
async def run_sql(sql: str):
    ...  # unchanged
```

`principal` is optional in the signature — input-only policies just take
`tool_input`. Passing the principal costs nothing architecturally (it is already
in the runtime context at the tools node) and unlocks the policies teams actually
want: *"require approval for writes unless the caller has the `deploy` scope,"*
*"auto-approve for the on-call engineer,"* *"gate everything for external
guests."* It is also **safe**: `needs_approval` runs in the trusted harness and
reads the principal from runtime context, which the model cannot forge — so the
agent can never talk its way out of an approval gate.

When `needs_approval` returns truthy, the tool node calls LangGraph
`interrupt({...})` **before** execution. The session pauses, persists, and
consumes no compute until resumed with `Command(resume={"approved": True})`
(surfaced by channels as a button/menu, or via the HTTP `/resume` endpoint).

### 4.4 Skills — `agent/skills/<name>.md`

A skill is a markdown file with YAML frontmatter:

```markdown
---
description: How this team defines revenue. Load before answering any revenue question.
---
Revenue is recognized net of refunds, over the subscription term.
Weeks are Monday-anchored, in UTC.
Exclude trial and internal accounts from every number.
```

Leve exposes each skill as a synthetic `load_skill(<name>)` tool. Only the
**descriptions** are given to the model up front; the body is injected into
context only when the model chooses to load it — keeping the base prompt small.

### 4.5 Subagents — `agent/subagents/<name>/`

A subagent is the **same directory shape one level down**, compiled into its own
LangGraph **subgraph** with a fresh state channel (clean context window) and only
the tools defined in its folder.

```python
# agent/subagents/investigator/agent.py
from leve import define_agent

agent = define_agent(
    description="Investigates anomalies in the data before the analyst reports them.",
    model="anthropic:claude-opus-4-8",
)
```

The parent invokes it like a tool: Leve auto-generates a `delegate_to_investigator`
tool whose execution runs the subgraph to completion and returns **only its final
message** to the parent — the subagent's intermediate turns and tool calls are
not streamed into the parent's context or message state. (They still appear in
the subagent's own LangSmith trace, nested under the parent run, so nothing is
lost for debugging.) This keeps the parent's context window clean, which is the
point of delegating in the first place. Streaming intermediate progress to the
parent is deferred to a later version. Subagent state is checkpointed under a
child thread namespace so deep delegations remain durable and resumable.

### 4.6 Connections — `agent/connections/<name>.py`

A connection points at an MCP server or an OpenAPI-described API. Credentials are
brokered by Leve; **the model never sees URLs or tokens.**

```python
from leve.connections import define_mcp_connection

connection = define_mcp_connection(
    url="https://mcp.linear.app/sse",
    transport="sse",
    description="Linear workspace: issues, projects, cycles, and comments.",
    # get_token receives the caller's Principal (§5.6) so the call runs as the
    # asker, not a shared service account. A shared-token connection omits the arg.
    auth={"get_token": lambda principal: principal.credential("linear")},
)
```

At build time Leve uses `langchain-mcp-adapters` to discover the remote tools,
namespaces them (`linear.create_issue`), and adds them to the tool node. OAuth
flows (consent + refresh) are handled by the connection adapter, which resolves
a token **per caller** so the model never sees credentials and every call is
scoped to the asker (§5.6). An OpenAPI variant (`define_openapi_connection`)
generates tools from a spec document.

`auth` accepts **either** a custom `get_token(principal)` callable (shown above,
eve's `getToken` equivalent) **or** a declarative broker reference —
`auth={"broker": "oauth_store", "provider": "linear", "scopes": [...]}` — which
Leve expands into a `get_token` that resolves through the configured
`CredentialBroker` (§5.7). Prefer the declarative form for the common OAuth
providers (it carries presets); drop to `get_token` for anything custom.

### 4.7 Channels — `agent/channels/<name>.py`

A channel is a small adapter that maps an external surface to Leve's session API.
The **HTTP API is on by default**; **Slack and Discord** ship built-in in v1
(§13, M4), and `define_channel` covers custom surfaces. Further built-ins
(Teams, Telegram, GitHub, Linear) are roadmap, not v1 — the adapter interface is
the contract, so they add no new core surface.

```python
from leve.channels import define_channel
from leve.channels.slack import slack_adapter

channel = define_channel(slack_adapter(
    signing_secret_env="SLACK_SIGNING_SECRET",
    # approvals render as buttons, questions as select menus,
    # typing indicators while the agent works — provided by the adapter.
))
```

A session is identified by a LangGraph **thread_id**, so the same conversation
can move between channels (asked in Slack, continued on the web). One channel can
hand off to another (an incident webhook opens a Slack investigation thread).

### 4.8 Schedules — `agent/schedules/<name>.py`

A schedule is a cron expression plus a handler that starts a session on the
agent's own clock.

```python
from leve.schedules import define_schedule
from ..channels.slack import channel as slack

@define_schedule(cron="0 9 * * 1")
async def monday_summary(ctx):
    await ctx.receive(
        slack,
        message="Summarize last week's revenue and post it to the team channel.",
        target={"channel_id": "C0123ABC"},
        auth=ctx.app_auth,
    )
```

`ctx.app_auth` is the **app principal** the run executes under — scheduled runs
have no human asker, so they carry an explicit, auditable service identity rather
than any user's permissions (§5.6).

On LangGraph Platform this deploys as a **Cron** that creates a run on a fresh
thread. Self-hosted, Leve emits an APScheduler/Celery-beat config or a standard
crontab entry that hits the schedule endpoint.

---

## 5. Built-in production features (mapped to LangGraph)

### 5.1 Durable sessions
Every conversation is one **thread** with a **checkpointer**. Each node
transition (model call, tool call, approval) is a checkpoint, so a session can
pause, survive a crash or redeploy, and resume from the exact step. Local dev
uses SQLite; production uses Postgres (or Redis). Mid-task runs finish on the
graph version they started on.

### 5.2 Sandboxed compute
Agent-generated code never runs in the harness process. Each agent gets a
sandbox via a pluggable **`Sandbox` adapter** — a small interface (`run`,
`write_file`, `read_file`) so adapters swap by config (Dependency Inversion /
Open–Closed). Adapters are organized in tiers:

| Tier | Adapter | Isolation | Platforms | Use |
| ---- | ------- | --------- | --------- | --- |
| **0** | `subprocess` / virtual-shell | none / in-memory FS | any | Opt-in dev escape hatch, behind an explicit flag — **never the default**. |
| **1 (default)** | **`microsandbox`** | microVM (libkrun → KVM on Linux, Hypervisor.framework on Apple-Silicon macOS) | macOS + Linux | Local dev & self-host. |
| **1 (fallback)** | `os_native` (`sandbox-exec` on macOS, `bubblewrap`+landlock+seccomp on Linux) or `docker` | OS MAC policy / container | macOS + Linux | Auto-selected on Intel Macs / no-KVM Linux. |
| **2** | `e2b` / `modal` | managed microVM | cloud | Production deploy (mirrors eve's Vercel Sandbox). |

**Default: `microsandbox`.** It is the only option that meets every requirement
at once — true hardware-level microVM isolation, genuinely cross-platform across
both targets (macOS *and* Linux), sub-200 ms starts, a first-class Python SDK,
and self-hostable with no long-running daemon. It runs real `bash` in a separate
security context, which is exactly the harness/agent split Leve needs.

**Why not Zeroboot** (the candidate that prompted this): it is Linux+KVM only
(no macOS, so it can't be the default for Mac developers) and self-describes as
an unhardened prototype — the wrong place to take isolation risk. Its strength
(sub-millisecond CoW VM forking) targets high-throughput fan-out, not a
single-user harness. It is parked as a **future high-throughput Linux-server
adapter** once it hardens. **WASM/Pyodide is rejected outright**: it cannot run
arbitrary `bash` (no subprocess model) and Pyodide is browser/Node-bound.

The sandbox tools are injected into the tool node like any other tool, gated by
the active adapter and carrying no ambient credentials (§5.6).

**Resource quotas** (CPU, memory, wall-clock, disk, network egress) ship with
secure defaults — 1 vCPU / 1 GB RAM / 1 GB disk / 120 s per command, and egress
**denied** by default with an optional host allow-list. All fields are
overridable in `leve.toml` under `[sandbox.limits]` (§11), and an individual
agent may *tighten* them in `agent.py` but never loosen past the project ceiling.
The active adapter translates these to its native mechanism (microVM flags,
cgroups, `ulimit`, container limits).

### 5.3 Human-in-the-loop
Built on `interrupt()` / `Command(resume=…)` (§4.3). Pauses are free —
checkpointed and idle — and resume exactly where they stopped. Two interrupt
kinds: **approval** (boolean gate) and **question** (structured input request).

### 5.4 Tracing & evals
Every run emits a LangSmith trace with one span per turn, model call, and tool
call (including sandbox commands), with inputs/outputs. Spans export over OTel to
any backend. Evals are scored test suites (§7) runnable locally or against a
deployed app and wired into CI as a deploy gate.

### 5.5 Context compaction
When a thread approaches the model's context window, Leve runs a summarization
node (configurable via `compaction`) that folds older turns into a running
summary message, preserving the durable trace while bounding token cost.

### 5.6 Per-caller permission scoping (multi-tenant security)

This is eve's sharpest production property — *"every query is scoped to the
asker's own permissions, so the agent can never show you a table you could not
already see."* Leve treats the agent (the model **and** any code it writes) as
**untrusted**, and enforces the asker's permissions outside it.

#### The `Principal`

A `Principal` is an immutable object carrying the caller's identity and a way to
resolve credentials on their behalf. It is created by the **channel adapter** at
session start from an already-authenticated identity, never from anything the
model produced.

```python
# leve/auth.py (framework type — illustrative)
@dataclass(frozen=True)
class Principal:
    subject: str                 # stable user id (e.g. Slack U123 / OIDC sub)
    tenant: str | None           # org / workspace for multi-tenant isolation
    claims: Mapping[str, Any]    # roles, scopes, group memberships
    # resolve a downstream credential for THIS caller (DB role, OAuth token, …).
    # `audience` names the downstream system (e.g. "warehouse", "linear"); for
    # OAuth audiences it is the broker `provider` key (§5.7), so the broker can
    # look up the stored token by `(tenant, subject, provider)`.
    async def credential(self, audience: str) -> Credential: ...
```

#### Where it lives — never in model state

The principal travels in the run's **runtime context** (LangGraph
`config.configurable` / the typed runtime `context`), which is **not** part of
the `messages` state the model reads or writes. Two consequences:

- The model cannot read the principal, so it cannot leak or reason about it.
- The model cannot *set* it, so a prompt injection cannot escalate identity.

#### How tools receive it — injected, not model-provided

Tools that act on the caller's behalf declare the principal as an **injected
argument**. Leve uses LangGraph's `InjectedToolArg`/`InjectedState` so the field
is stripped from the JSON schema shown to the model and filled by the runtime at
execution time:

```python
from leve.tools import define_tool, InjectedPrincipal
from leve.auth import Principal

@define_tool(
    description="Run a read-only SQL query against the warehouse.",
    input_schema=RunSqlInput,
)
async def run_sql(sql: str, principal: Principal = InjectedPrincipal()):
    # Open the warehouse with the ASKER's role / RLS session, not a god account.
    cred = await principal.credential("warehouse")
    async with warehouse.session(role=cred.db_role, rls_user=principal.subject) as db:
        return await db.run_readonly(sql)
```

The model still only sees `{ "sql": "..." }`. The identity is appended by the
harness.

#### Enforcement happens at the system of record

Leve does **not** reimplement row-level security. It propagates the caller's
identity to the layer that already owns it:

- **Warehouse/DB:** connect with the asker's role and set the RLS/session user,
  so the database rejects rows the asker can't see.
- **Connections (§4.6):** `auth.get_token(principal)` returns the *asker's*
  OAuth token (via Vercel-Connect-style broker or your IdP), so the MCP/API call
  runs as them — not as a shared service account.
- **Store:** namespaced by `(tenant, subject)` so long-term memory never bleeds
  across users.

#### Subagents inherit, and can only narrow

A subagent runs with its parent's principal automatically passed down the
subgraph config. A parent may hand down a **narrowed** principal (fewer
scopes/claims) but the framework forbids widening — delegation can never grant
access the asker lacks.

#### Scheduled & non-interactive runs use an app principal

A schedule (§4.8) or webhook fires with no human asker, so there is no user
identity to scope to. Such runs execute under an explicit **app principal**
(`ctx.app_auth`) — a distinct, auditable service identity whose scopes are
configured, never inherited from a real user. A scheduled run therefore never
silently acts with someone's personal permissions; if it needs user-scoped data
it must target a specific subject explicitly. This mirrors eve's `appAuth`.

#### The sandbox has no ambient credentials

Agent-written code runs in a sandbox (§5.2) with **zero** ambient secrets — no
warehouse password, no tokens, no cloud metadata endpoint. If sandboxed code
needs privileged data it must call back through a Leve tool, which re-applies
the principal. So even arbitrary code the model writes is bounded by the asker's
permissions.

#### Thread & session isolation (deployed)

On LangGraph Platform, Leve registers an `Auth` handler: an `@auth.authenticate`
callback verifies the incoming request and produces the `Principal`, and
`@auth.on` authorization handlers scope every thread/store/run operation by
`(tenant, subject)`. A user can only create, read, resume, or stream **their
own** sessions; the asker's identity on a resumed session is re-verified, not
trusted from the original request.

#### Defense in depth — summary

| Layer            | Control                                                        |
| ---------------- | ------------------------------------------------------------- |
| Identity         | Channel authenticates the human → `Principal`                 |
| Transport        | Principal in runtime context, never in model-visible state    |
| Tool boundary    | Injected arg (model can't read or forge it)                   |
| Authorization    | Per-caller credentials for DB roles / OAuth tokens            |
| Data layer       | DB RLS / scoped tokens enforce at the source of truth         |
| Code isolation   | Sandbox has no ambient credentials; must re-broker            |
| Session isolation| Platform `Auth` scopes threads/store by `(tenant, subject)`   |

### 5.7 Credential brokering (the "Connect" equivalent)

§5.6 assumes `principal.credential(audience)` can produce the asker's token for a
third-party service. *Getting* that token is its own problem: the user must log
in to the service once and consent ("Allow Leve to act on my Linear"), and
something must then store, refresh, and hand out that token at runtime —
**without the model ever seeing it**. eve uses hosted **Vercel Connect** for
this; Leve has no hosted equivalent, so it ships a pluggable `CredentialBroker`
adapter (same pattern as checkpointer/sandbox) with three built-ins:

| Broker            | How it resolves a credential                                              | Use when                                  |
| ----------------- | ------------------------------------------------------------------------- | ----------------------------------------- |
| `static`          | Returns a fixed secret from env. Not per-caller (shared service account). | Dev, or a tool that legitimately acts as the app. |
| `oauth_store` *(default)* | Runs Authorization-Code + PKCE per provider; stores encrypted tokens keyed by `(tenant, subject, provider)` in Postgres; auto-refreshes on expiry. | Self-host with real per-user OAuth.       |
| `token_exchange`  | RFC 8693 OAuth token exchange: trades the caller's session token for a downstream-scoped token at the org's IdP. Stores nothing. | Orgs with an existing IdP (Okta/Entra/Auth0). |

**Capturing consent reuses the human-in-the-loop machinery (§5.3).** This applies
to interactive brokers — `oauth_store` and generic OAuth; `static` reads a fixed
secret and `token_exchange` swaps an already-held session token, so neither
prompts the user. When an interactive broker is asked for a credential the asker
hasn't granted yet, it raises a `NeedsConsent` interrupt. The session pauses (free, checkpointed) and the channel
surfaces an "Authorize Linear" link/button; the user completes OAuth at the
provider, the broker stores the token, and the session resumes exactly where it
stopped. The `oauth_store` broker adds callback routes
(`/leve/v1/connect/:provider/callback`) to the FastAPI server to catch the
redirect. No new pause primitive — consent is just another kind of interrupt.

The broker is selected in `leve.toml` (`[credentials] broker = "oauth_store"`)
and is the single thing every `connection`'s `get_token(principal)` and every
tool's `principal.credential(...)` call resolves through.

**Provider presets.** `oauth_store` ships presets for the common providers —
**Slack, GitHub, Google, Linear, Notion, Salesforce, Snowflake** — each
encoding that provider's authorize/token endpoints, default scopes, and
token-refresh quirks. You supply only `client_id` / `client_secret` (via env);
the rest comes from the preset. Anything else uses a generic OAuth 2.0 config
(endpoints + scopes spelled out). Presets are referenced by name in the
connection file:

```python
auth = {"broker": "oauth_store", "provider": "linear", "scopes": ["read", "write"]}
```

---

## 6. The loader (compilation model)

At startup (`leve dev`) or build (`leve build`), the loader:

1. Walks the `agent/` tree from the project root.
2. Parses `agent.py` → base config; `instructions.md` → system prompt.
3. Imports every `tools/*.py` → `StructuredTool` list.
4. Reads `skills/*.md` frontmatter → `load_skill` tool catalog.
5. Connects each `connections/*.py` → remote tool lists (MCP/OpenAPI).
6. Recursively compiles each `subagents/*/` → subgraph + delegation tool.
7. Wraps sandbox-adapter tools per the active adapter.
8. Builds a `create_react_agent`-style `StateGraph`: `model → tools → model`,
   with per-tool approval `interrupt()`s inside the tools node and context
   compaction wired in as a `pre_model_hook`.
9. Compiles with the configured checkpointer + store.
10. Threads the caller `Principal` (§5.6) into the runtime context and binds it
    as an injected arg on tools that request it — never into message state.
11. Registers channels and schedules against the compiled graph; on Platform,
    installs the `Auth` handler that scopes threads/store by `(tenant, subject)`.

The result is a single `CompiledGraph` plus a serving layer (FastAPI).

---

## 7. Evals — `evals/<name>.eval.py`

```python
from leve.evals import define_eval
from leve.evals.expect import includes

@define_eval(description="The analyst answers revenue questions by the team's rules.")
async def revenue(t):
    await t.send("What was revenue last week?")
    t.completed()
    t.called_tool("run_sql")
    t.check(t.reply, includes("net of refunds"))
```

- `t.send` starts/continues a session against an in-process compiled graph.
- Assertions: `t.called_tool`, `t.loaded_skill`, `t.check`, `t.no_errors`,
  plus model-graded scorers via LangSmith evaluators.
- Backed by LangSmith **datasets**; `leve eval` records results and diffs scores
  against the previous run. Suites become the CI deploy gate.

---

## 8. CLI

| Command                          | Purpose                                                      |
| -------------------------------- | ------------------------------------------------------------ |
| `leve init <name>`               | Scaffold a new agent project (wizard: model, first tool).    |
| `leve dev`                       | Run the dev server + TUI client (SQLite checkpointer).       |
| `leve build`                     | Compile and validate the graph (schemas, connections, optional mypy) without serving. |
| `leve eval [--remote <url>]`     | Run eval suites locally or against a deployment.             |
| `leve channels add <slack\|…>`   | Write a `channels/<name>.py` adapter file.                   |
| `leve connections add <name>`    | Scaffold an MCP/OpenAPI connection with auth stub.           |
| `leve deploy`                    | Deploy to LangGraph Platform (or build a self-host image).   |

`leve dev` serves structured events over HTTP so `curl`, test scripts, or CI can
drive the agent identically to the TUI — the TUI is just a client.

---

## 9. HTTP API

Mirrors eve's session API, backed by LangGraph runs/threads:

| Method & path                       | Purpose                                             |
| ----------------------------------- | --------------------------------------------------- |
| `POST /leve/v1/session`             | Create a session (thread); returns `session_id`.    |
| `GET  /leve/v1/session/:id/stream`  | SSE stream of structured turn/tool/approval events. |
| `POST /leve/v1/session/:id/message` | Send a message on the session (`:id` is the `session_id`).|
| `POST /leve/v1/session/:id/resume`  | Resume an interrupt (approval/answer).              |
| `GET  /leve/v1/session/:id`         | Fetch session state + history from the checkpointer.|

Events are derived from `graph.astream_events(...)` and normalized to a stable
schema (`turn.start`, `model.delta`, `tool.call`, `tool.result`,
`approval.requested`, `turn.end`).

---

## 10. Deployment

- **LangGraph Platform (default):** `leve deploy` emits a `langgraph.json`
  pointing at the compiled graph, with Postgres persistence, autoscaling, and
  Crons for schedules. Preview deployments per commit, instant rollback.
- **Self-host:** `leve build --docker` produces a container running the FastAPI
  server + compiled graph against a user-provided Postgres (Redis optional, when
  the low-latency checkpointer is selected), with schedules emitted as
  crontab/APScheduler config.

The same `agent/` directory runs identically in dev and prod; only the
checkpointer, store, and sandbox adapters swap via `leve.toml`.

---

## 11. Project config — `leve.toml`

```toml
[agent]
root = "agent"
default_model = "anthropic:claude-opus-4-8"

[persistence]
checkpointer = "postgres"        # postgres | redis | sqlite | memory
store = "postgres"               # postgres | memory

[sandbox]
adapter = "microsandbox"         # microsandbox | os_native | docker | e2b | modal | subprocess

# Resource quotas for untrusted agent code. These are the framework defaults;
# override any field here. An agent may further tighten (never loosen) them in agent.py.
[sandbox.limits]
memory_mb     = 1024             # RAM per sandbox
vcpus         = 1                # CPU cores
timeout_sec   = 120             # wall-clock per command
disk_mb       = 1024            # scratch filesystem
# Egress policy (secure-by-default):
#   network = "deny"  + empty network_allow  → block all egress
#   network = "deny"  + non-empty allow-list → block all except listed hosts
#   network = "allow"                        → permit all egress
network       = "deny"
network_allow = []               # host allow-list, e.g. ["pypi.org"]

[credentials]
broker = "oauth_store"           # static | oauth_store | token_exchange

[tracing]
provider = "langsmith"           # langsmith | otel
project = "leve-data-analyst"

[deploy]
target = "langgraph-platform"    # langgraph-platform | docker
```

Environments override via env vars (`LEVE_CHECKPOINTER`, `LEVE_SANDBOX_ADAPTER`,
…) so dev → preview → prod differ only in config, never in agent code.

---

## 12. Design principles

- **DRY:** one loader compiles all component types; channels/sandboxes/checkpointers
  are adapters behind shared interfaces.
- **YAGNI:** v1 ships only the conventions above; no speculative config surface.
- **SOLID:** each file = one responsibility; adapters invert dependencies
  (graph depends on `SandboxAdapter`/`Channel` abstractions, not concretes);
  new providers extend without modifying the core loop.
- **Convention over configuration:** the directory tree *is* the configuration.

---

## 13. Milestones

1. **M1 — Core loop:** loader, `define_agent`, instructions, tools, ReAct graph,
   SQLite checkpointer, `leve dev` + TUI, HTTP API.
2. **M2 — Production primitives:** approvals (`interrupt`), skills, compaction,
   Postgres checkpointer + store, LangSmith tracing.
3. **M3 — Reach:** MCP/OpenAPI connections, `Sandbox` adapter interface +
   microsandbox default (E2B/Modal for cloud), subagents (subgraphs).
4. **M4 — Surfaces & ops:** channels (Slack/Discord/HTTP), schedules, evals,
   `leve deploy` to LangGraph Platform, CI gate, preview deploys, rollback.
5. **M5 — Multi-tenant security (§5.6):** `Principal`, injected-arg tool auth,
   per-caller connection credentials, sandbox credential isolation, Platform
   `Auth` thread/store scoping.

---

## 14. Open questions

All v0.1 design questions are resolved:

- **Default sandbox** → microsandbox, tiered adapters (§5.2).
- **Sandbox resource quotas** → `[sandbox.limits]` in `leve.toml` with secure
  defaults, per-agent tightening only (§5.2, §11).
- **Subagent delegation** → returns only the final result (§4.5).
- **Credential brokering** → pluggable `CredentialBroker`, consent-as-interrupt,
  built-in provider presets (§5.7).
- **Approval policy** → `needs_approval` is principal-aware (§4.3).

No blocking questions remain for v0.1. Future work tracked in the milestones
(§13): high-throughput Linux sandbox tier (Zeroboot/Firecracker), and
streaming subagent progress to the parent trace.
```
