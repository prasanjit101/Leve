# Adding a Custom Channel

How to give your scaffolded agent a new external surface — a place where it
"lives" and answers messages (SPEC §4.7).

A **channel** is a small adapter that maps an outside surface (Slack, Discord, a
webhook from your own product, an SMS gateway, …) onto Leve's session API. The
HTTP API (§9) is always on; a channel *adds* an inbound webhook that:

1. verifies the request came from the surface,
2. parses the payload into a normalized message,
3. drives the matching session, and
4. delivers the agent's reply back to the surface.

Built-in Slack and Discord ship in v1. Anything else is a custom adapter — and
because **the adapter interface is the contract**, a new surface adds an adapter,
never new core surface (Open/Closed).

---

## 1. The fast path — built-in channels

If your surface is Slack or Discord, scaffold it instead of writing one:

```bash
leve channels add slack      # writes agent/channels/slack.py
leve channels add discord     # writes agent/channels/discord.py  (needs leve[discord])
```

Each command drops a one-liner into `agent/channels/<kind>.py`:

```python
# agent/channels/slack.py
from leve.channels import define_channel
from leve.channels.slack import slack_adapter

channel = define_channel(slack_adapter(signing_secret_env="SLACK_SIGNING_SECRET"))
```

Set the referenced env vars (`SLACK_SIGNING_SECRET` / `SLACK_BOT_TOKEN`, or
`DISCORD_PUBLIC_KEY` / `DISCORD_BOT_TOKEN`) and you're done. The rest of this doc
is for surfaces Leve doesn't ship.

---

## 2. How a channel is wired

The loader (§6) walks `agent/channels/*.py` and collects every module-level
`ChannelSpec` (the object `define_channel(...)` returns). **The file's stem is
the channel's name** — `agent/channels/slack.py` → `slack` — and that name
becomes the route segment. So no registration step exists: dropping a file in
`agent/channels/` *is* the registration.

At serve time the server registers one inbound endpoint per channel:

```
POST /leve/v1/channels/<name>/events
```

When a request hits it, the server runs your adapter through a fixed lifecycle
(`leve/server.py::_register_channel`):

```text
raw body ──▶ verify(headers, body)         reject → 401 (or 503 if a dep is missing)
         ──▶ json.loads(body)              malformed → 400
         ──▶ handshake_response(payload)   setup/ping → return that body, stop
         ──▶ is_retry(headers)             provider re-delivery → ack {"ok": true}, stop
         ──▶ parse(payload)                None → ack {"ok": true}, stop
         ──▶ ack {"ok": true} immediately, then run the turn in the BACKGROUND
         ──▶ deliver(target, reply)         best-effort; failure is logged, never 500
```

Two design points worth internalizing:

- **Verify runs on the raw bytes, before parsing.** An unauthenticated caller
  never reaches your parser.
- **The webhook acks fast and runs the agent in the background.** Surfaces like
  Slack and Discord give you ~3 seconds to respond or they re-deliver; a real
  agent turn is far slower. Leve returns `{"ok": true}` immediately and posts the
  answer asynchronously via `deliver(...)`. Turns are serialized per
  `session_key`, so retries and concurrent messages don't race the same thread.

---

## 3. The adapter contract

A custom channel subclasses `leve.channels.ChannelAdapter`. Only `parse` and
`deliver` are abstract; the rest have sensible defaults you override per surface.

| Method | Required | When it runs | Default |
| ------ | -------- | ------------ | ------- |
| `name` (class attr) | recommended | identifies the adapter | `"channel"` |
| `verify(headers, body)` | override for real surfaces | first, on raw bytes | accept all |
| `is_retry(headers)` | override if the surface re-delivers | after handshake | `False` |
| `handshake_response(payload)` | override if the surface has a setup ping | after JSON parse | `None` |
| `parse(payload)` | **yes** | turns a payload into a message | — (abstract) |
| `deliver(target, text)` | **yes** | sends the reply back | — (abstract) |

`parse` returns an `IncomingMessage` (or `None` to ignore the payload):

```python
@dataclass(frozen=True)
class IncomingMessage:
    session_key: str                 # becomes the LangGraph thread_id (the session)
    text: str                        # the user's message to the agent
    target: dict[str, Any] = {}      # where deliver() should send the reply
    principal: Principal | None = None  # the authenticated caller (SPEC §5.6)
```

- **`session_key`** is the conversation's identity. Build it so the *same*
  conversation always maps to the same key (e.g. `myapp:{room}:{thread}`).
  Because it's just a `thread_id`, a conversation can even move between surfaces.
- **`target`** is opaque to Leve — it's handed straight back to your `deliver`,
  so put whatever you need to address the reply (channel id, thread id, reply
  URL, …).
- **`principal`** carries the verified caller's identity and tenant. Set it from
  the surface's *authenticated* identity, never from anything the model
  produced — it's what scopes the agent's data access (§5.6). The credential
  broker is attached downstream automatically.

---

## 4. Writing one — a worked example

Say you run an internal "Helpdesk" web widget that POSTs JSON and authenticates
with a shared HMAC secret. Create `agent/channels/helpdesk.py`:

```python
"""Custom Helpdesk channel: an internal widget that POSTs signed messages."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Mapping

import httpx

from leve.auth import Principal
from leve.channels import ChannelAdapter, IncomingMessage, define_channel


class HelpdeskAdapter(ChannelAdapter):
    name = "helpdesk"  # → POST /leve/v1/channels/helpdesk/events

    def __init__(self, *, secret_env: str = "HELPDESK_SECRET"):
        self._secret_env = secret_env

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = os.environ.get(self._secret_env, "")
        signature = headers.get("x-helpdesk-signature", "")
        if not (secret and signature):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        # Constant-time compare guards against timing attacks.
        return hmac.compare_digest(expected, signature)

    def parse(self, payload: dict[str, Any]) -> IncomingMessage | None:
        text = (payload.get("message") or "").strip()
        if not text:
            return None  # nothing to answer — ack and ignore
        ticket = payload["ticket_id"]
        # The widget already authenticated the user; trust that identity here.
        principal = Principal(
            subject=payload["user_id"],
            tenant=payload.get("org_id"),
            claims={"plan": payload.get("plan", "free")},
        )
        return IncomingMessage(
            session_key=f"helpdesk:{ticket}",   # one session per ticket
            text=text,
            target={"reply_url": payload["reply_url"]},
            principal=principal,
        )

    async def deliver(self, target: dict[str, Any], text: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.post(target["reply_url"], json={"reply": text})


# The module-level ChannelSpec is what the loader discovers.
channel = define_channel(HelpdeskAdapter(secret_env="HELPDESK_SECRET"))
```

That's the whole integration. The filename (`helpdesk.py`) names the route; the
module-level `channel = define_channel(...)` is what the loader picks up.

> **Keep secrets in env, never in the file.** Adapters take `*_env` arguments
> and read `os.environ` at request time — mirror that so credentials stay out of
> source and out of the model's reach. Add the new variable to your `.env`.

### Optional overrides

Add these only if your surface needs them:

```python
def is_retry(self, headers: Mapping[str, str]) -> bool:
    # Skip provider re-deliveries so a slow turn doesn't produce duplicate replies.
    return "x-helpdesk-retry" in {k.lower() for k in headers}

def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
    # Answer a one-time setup/verification ping with the exact body the surface wants.
    if payload.get("type") == "ping":
        return {"pong": payload.get("nonce")}
    return None
```

If verification needs an optional dependency that may be missing, raise
`leve.errors.ConfigError` from `verify` (like the Discord adapter does for
PyNaCl) — the server turns that into a clean `503`, not a `500`.

---

## 5. Run and test it

Start the dev server and the route is live (no extra config):

```bash
leve dev
```

Drive it exactly as the real surface would — the endpoint is plain HTTP:

```bash
BODY='{"message":"hi","ticket_id":"T1","user_id":"U1","reply_url":"http://localhost:9000/cb"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$HELPDESK_SECRET" | awk '{print $2}')

curl -X POST http://localhost:8000/leve/v1/channels/helpdesk/events \
  -H "x-helpdesk-signature: $SIG" \
  -d "$BODY"
# → {"ok": true}   (the agent's reply is POSTed to reply_url asynchronously)
```

Unit-test the adapter offline — `parse`/`verify` are pure functions, and you can
drive a full turn through `SessionManager.run_channel_turn` with a
`FakeChatModel` (no network, no real surface). See `tests/test_channels.py` for
the pattern:

```python
async def test_helpdesk_turn_delivers(make_loaded):
    from leve.channels import IncomingMessage
    from leve.server import SessionManager
    from leve.testing import FakeChatModel
    from tests.conftest import runtime_for

    delivered = {}

    class FakeAdapter:
        async def deliver(self, target, text):
            delivered["text"] = text

    loaded = make_loaded(FakeChatModel(responses=["resolved!"]))
    async with runtime_for(loaded) as rt:
        manager = SessionManager(rt)
        incoming = IncomingMessage(session_key="helpdesk:T1", text="hi", target={})
        await manager.run_channel_turn(FakeAdapter(), incoming)

    assert delivered["text"] == "resolved!"
```

Verify discovery picked up the file:

```bash
leve build      # compiles and validates; fails loudly on a bad channel file
```

Two channels resolving to the same `name` (same filename stem) is a loader error
— keep filenames unique.

---

## 6. Checklist

- [ ] `agent/channels/<name>.py` exists; `<name>` is the route segment you want.
- [ ] It defines a `ChannelAdapter` subclass with a unique `name`.
- [ ] `verify(...)` authenticates the request on the **raw body** (don't skip in prod).
- [ ] `parse(...)` returns a stable `session_key`, the `text`, a `target` your
      `deliver` understands, and a `Principal` from the surface's verified identity.
- [ ] `deliver(...)` posts the reply back to `target`.
- [ ] A module-level `channel = define_channel(MyAdapter(...))`.
- [ ] Secrets read from env (`*_env` args), added to `.env`.
- [ ] `leve build` passes; an offline test exercises `parse`/`verify` and a turn.

---

## See also

- **SPEC §4.7** — channels in the framework design.
- **SPEC §5.6** — per-caller permission scoping (why `principal` matters).
- **SPEC §9** — the HTTP session API the channel drives under the hood.
- `src/leve/channels/slack.py`, `src/leve/channels/discord.py` — the built-in
  adapters as reference implementations.
