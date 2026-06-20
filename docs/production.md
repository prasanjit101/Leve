# Production Setup & Operations

How to configure, deploy, and operate a Leve agent in production. The same
`agent/` directory runs identically in dev and prod — only the adapters in
`leve.toml` and the environment change.

---

## 1. Production configuration

A production `leve.toml` swaps the local defaults for durable, isolated, and
observable backends:

```toml
[agent]
root = "agent"
default_model = "anthropic:claude-opus-4-8"

[persistence]
checkpointer = "postgres"        # sqlite | memory | postgres
store = "postgres"               # sqlite | memory | postgres — long-term memory
postgres_url = "postgresql://user:pass@host:5432/leve"

[credentials]
broker = "oauth_store"           # per-user OAuth tokens (static | oauth_store | token_exchange)

[sandbox]
adapter = "microsandbox"         # microVM isolation (the production default)

[sandbox.limits]
memory_mb = 1024
vcpus = 1
timeout_sec = 120
disk_mb = 1024
network = "deny"                 # deny | allow — egress denied by default
network_allow = []               # host allow-list when network = "deny"
max_output_bytes = 262144

[tracing]
provider = "langsmith"           # langsmith | otel | none
project = "myagent-prod"

[deploy]
target = "langgraph-platform"    # langgraph-platform | docker
base_url = "https://myagent.example.com"   # used in emitted schedule crontab
```

For a single-node self-host deploy, `checkpointer`/`store` may both be `sqlite`
(file-backed, durable across restarts but not shared across replicas) — Postgres
is required only for multi-replica or autoscaled deployments.

Install the matching extras on the production image:

```bash
uv sync --extra postgres --extra microsandbox   # + --extra discord / --extra google if used
```

### Environment variables

| Variable | Purpose |
| -------- | ------- |
| `ANTHROPIC_API_KEY` (or provider key) | Model access |
| `GOOGLE_API_KEY` | Gemini model access (`leve[google]` / `google_genai:<model>`) |
| `LEVE_POSTGRES_URL` | Overrides `[persistence] postgres_url` |
| `LEVE_CHECKPOINTER`, `LEVE_STORE` | Override backend selection per environment |
| `LANGSMITH_API_KEY` | Enables LangSmith tracing (also `LANGSMITH_PROJECT`) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Enables OTel export when `provider = "otel"` |
| `LEVE_SCHEDULE_SECRET` | Required shared secret to trigger schedule endpoints |
| `LEVE_CRED_<AUDIENCE>` | Static credential for the `static` broker (e.g. `LEVE_CRED_WAREHOUSE`) |
| `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN` | Slack channel verify + post |
| `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN` | Discord channel verify + post |
| OAuth `client_id`/`client_secret` per provider | For the `oauth_store` broker |

Dev → preview → prod should differ **only** in config/env, never in agent code.

## 2. Deploy

`leve deploy` emits artifacts for the configured `[deploy] target`:

```bash
uv run leve deploy
```

### Target: LangGraph Platform (default)

Emits `langgraph.json` pointing at the compiled graph (`leve.platform:graph`).
Deploy with the LangGraph Platform tooling for managed Postgres persistence,
autoscaling, Crons for schedules, preview deploys, and instant rollback.

> Channel webhooks (`/leve/v1/channels/...`) are served by the self-host app, not
> the Platform-managed graph. `leve deploy` warns when the project has channels
> but targets Platform — use the `docker` target (or front the self-host app) if
> you need inbound Slack/Discord.

### Target: self-host container

Set `[deploy] target = "docker"`, then:

```bash
uv run leve deploy        # emits Dockerfile (+ leve.crontab if you have schedules)
docker build -t myagent .
docker run -p 8000:8000 \
  -e LEVE_POSTGRES_URL=postgresql://... \
  -e ANTHROPIC_API_KEY=... \
  -e LEVE_SCHEDULE_SECRET=... \
  myagent
```

The container runs the FastAPI server (`leve.platform:app`) with the compiled
graph. Point a Postgres instance at it via `LEVE_POSTGRES_URL`.

## 3. HTTP API surface

Served at `/leve/v1` (see local-development.md for request/response details):

| Method & path | Purpose |
| ------------- | ------- |
| `POST /leve/v1/session` | Create a session (thread) |
| `POST /leve/v1/session/:id/message` | Send a message; SSE stream of the turn |
| `POST /leve/v1/session/:id/resume` | Resume an approval/consent interrupt |
| `GET  /leve/v1/session/:id/stream` | Attach to the in-flight turn (SSE) |
| `GET  /leve/v1/session/:id` | Session state + history |
| `POST /leve/v1/channels/:name/events` | Channel webhook (verified per adapter) |
| `POST /leve/v1/schedules/:name/run` | Trigger a schedule (requires `LEVE_SCHEDULE_SECRET`) |

## 4. Channels

Add `agent/channels/<name>.py` and set the provider secrets. Point the provider
at `https://<host>/leve/v1/channels/<name>/events`.

- **Slack** — set `SLACK_SIGNING_SECRET` (request verification, with a replay
  window) and `SLACK_BOT_TOKEN` (to post replies). Retries are de-duplicated and
  the turn runs in the background so Slack gets its 3-second ack.
- **Discord** — set `DISCORD_PUBLIC_KEY` (Ed25519 verification; needs
  `leve[discord]`) and `DISCORD_BOT_TOKEN`.

A conversation maps to a stable `thread_id`, so the same session can move between
surfaces, and concurrent messages on one thread are serialized.

## 5. Schedules

Add `agent/schedules/<name>.py` with `@define_schedule(cron=...)`. Triggering:

- **Platform:** deploys as a Cron on a fresh thread.
- **Self-host:** `leve deploy` (docker target) emits `leve.crontab` with lines
  like:
  ```cron
  0 9 * * 1 curl -fsS -X POST -H "X-Leve-Schedule-Secret: $LEVE_SCHEDULE_SECRET" https://myagent.example.com/leve/v1/schedules/monday_summary/run
  ```
  Install it (`crontab leve.crontab`) and export `LEVE_SCHEDULE_SECRET` so the
  endpoint authenticates the caller. Scheduled runs execute under an explicit,
  auditable **app principal** — never a user's permissions.

## 6. Security (multi-tenant)

Leve treats the agent (model **and** code it writes) as untrusted and enforces
the asker's permissions outside it:

- **Identity:** the channel adapter builds a `Principal` from the verified
  caller. It lives in runtime context — never in model-visible state — so the
  model can't read or forge it.
- **Tools:** declare `principal: Principal = InjectedPrincipal()`; it's stripped
  from the model schema and filled by the runtime. Use
  `await principal.credential(audience)` to open downstream systems as the asker.
- **Credentials:** the `oauth_store` broker resolves per-caller OAuth tokens
  keyed by `(tenant, subject, provider)`; a missing grant raises a consent
  interrupt that pauses (free, checkpointed) until the user authorizes. Supply
  each provider's `client_id`/`client_secret` via env (presets ship for Slack,
  GitHub, Google, Linear, Notion, Salesforce, Snowflake).
- **Sandbox:** runs with **no ambient credentials** — code needing privileged
  data must call back through a Leve tool that re-applies the principal. Keep the
  default `microsandbox` (microVM isolation) in production; `subprocess` is a
  dev-only escape hatch with no isolation.
- **Platform isolation:** register the Platform `Auth` handler (`leve.platform_auth.make_auth`,
  supplying how you verify identity) to scope every thread/store/run by
  `(tenant, subject)`.

### Operational checklist

- [ ] `checkpointer`/`store` set to `postgres` with a reachable `LEVE_POSTGRES_URL`
- [ ] `sandbox.adapter = "microsandbox"`; `network = "deny"` (+ allow-list as needed)
- [ ] `LEVE_SCHEDULE_SECRET` set if any schedules are exposed
- [ ] Channel signing secrets + bot tokens set; webhook URLs registered
- [ ] OAuth `client_id`/`client_secret` per provider for `oauth_store`
- [ ] `LANGSMITH_API_KEY` (or OTel endpoint) set if tracing is enabled
- [ ] `leve eval` wired into CI as a deploy gate
