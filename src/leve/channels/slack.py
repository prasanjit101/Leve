"""Slack channel adapter (SPEC §4.7).

Verifies Slack's request signature (HMAC-SHA256 over ``v0:{ts}:{body}`` with a
replay window), handles the Events API URL-verification handshake, parses message
events into a session keyed by ``slack:{channel}:{thread}``, and delivers replies
via ``chat.postMessage``. Signing uses only the stdlib, so verification is fully
testable offline.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Mapping

import httpx

from leve.channels import ChannelAdapter, IncomingMessage

_REPLAY_WINDOW_SEC = 60 * 5


class SlackAdapter(ChannelAdapter):
    name = "slack"

    def __init__(self, *, signing_secret_env: str, bot_token_env: str = "SLACK_BOT_TOKEN"):
        self._signing_secret_env = signing_secret_env
        self._bot_token_env = bot_token_env

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = os.environ.get(self._signing_secret_env, "")
        timestamp = headers.get("x-slack-request-timestamp", "")
        signature = headers.get("x-slack-signature", "")
        if not (secret and timestamp and signature):
            return False
        try:
            if abs(time.time() - int(timestamp)) > _REPLAY_WINDOW_SEC:
                return False  # stale request — replay guard
        except ValueError:
            return False
        base = b"v0:" + timestamp.encode() + b":" + body
        expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def is_retry(self, headers: Mapping[str, str]) -> bool:
        # Slack re-delivers an event (with this header) when it doesn't get a
        # 200 within 3s. We ack and skip to avoid duplicate agent runs.
        return "x-slack-retry-num" in {k.lower() for k in headers}

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        return None

    def parse(self, payload: dict[str, Any]) -> IncomingMessage | None:
        event = payload.get("event") or {}
        # Ignore non-messages and the bot's own messages (prevents loops).
        if event.get("type") != "message" or event.get("bot_id") or "text" not in event:
            return None
        channel = event.get("channel", "")
        thread = event.get("thread_ts") or event.get("ts", "")
        return IncomingMessage(
            session_key=f"slack:{channel}:{thread}",
            text=event["text"],
            target={"channel": channel, "thread_ts": thread},
        )

    async def deliver(self, target: dict[str, Any], text: str) -> None:
        token = os.environ.get(self._bot_token_env)
        if not token:  # nothing to post with; no-op in dev
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "channel": target.get("channel"),
                    "thread_ts": target.get("thread_ts"),
                    "text": text,
                },
            )


def slack_adapter(*, signing_secret_env: str, bot_token_env: str = "SLACK_BOT_TOKEN") -> SlackAdapter:
    """Build a Slack channel adapter (SPEC §4.7)."""

    return SlackAdapter(signing_secret_env=signing_secret_env, bot_token_env=bot_token_env)
