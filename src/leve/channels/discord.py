"""Discord channel adapter (SPEC §4.7).

Discord's Interactions endpoint signs each request with Ed25519; verification
needs the ``[discord]`` optional dependency (PyNaCl), imported lazily so it is
only required when a Discord channel is actually used. The PING handshake is
surfaced through ``url_verification`` (the framework answers it), and slash/
message-command interactions are parsed into a session keyed by the channel.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx

from leve.channels import ChannelAdapter, IncomingMessage
from leve.errors import ConfigError

_PING = 1
_APPLICATION_COMMAND = 2


class DiscordAdapter(ChannelAdapter):
    name = "discord"

    def __init__(
        self, *, public_key_env: str, bot_token_env: str = "DISCORD_BOT_TOKEN"
    ):
        self._public_key_env = public_key_env
        self._bot_token_env = bot_token_env

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        public_key = os.environ.get(self._public_key_env, "")
        signature = headers.get("x-signature-ed25519", "")
        timestamp = headers.get("x-signature-timestamp", "")
        if not (public_key and signature and timestamp):
            return False
        try:
            from nacl.exceptions import BadSignatureError
            from nacl.signing import VerifyKey
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigError(
                "The Discord adapter requires the optional dependency: "
                "`pip install 'leve[discord]'`."
            ) from exc
        try:
            VerifyKey(bytes.fromhex(public_key)).verify(
                timestamp.encode() + body, bytes.fromhex(signature)
            )
            return True
        except (BadSignatureError, ValueError):
            return False

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        # A PING interaction is answered with a PONG (type 1).
        return {"type": _PING} if payload.get("type") == _PING else None

    def parse(self, payload: dict[str, Any]) -> IncomingMessage | None:
        if payload.get("type") != _APPLICATION_COMMAND:
            return None
        data = payload.get("data") or {}
        text = _option_value(data) or data.get("name", "")
        channel = payload.get("channel_id", "")
        if not text:
            return None
        return IncomingMessage(
            session_key=f"discord:{channel}",
            text=text,
            target={
                "channel_id": channel,
                "application_id": payload.get("application_id"),
            },
        )

    async def deliver(self, target: dict[str, Any], text: str) -> None:
        token = os.environ.get(self._bot_token_env)
        channel = target.get("channel_id")
        if not (token and channel):  # no-op in dev
            return
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://discord.com/api/v10/channels/{channel}/messages",
                headers={"Authorization": f"Bot {token}"},
                json={"content": text},
            )


def _option_value(data: dict[str, Any]) -> str | None:
    for option in data.get("options", []) or []:
        if option.get("type") in (3, None) and "value" in option:  # 3 = STRING
            return str(option["value"])
    return None


def discord_adapter(
    *, public_key_env: str, bot_token_env: str = "DISCORD_BOT_TOKEN"
) -> DiscordAdapter:
    """Build a Discord channel adapter (SPEC §4.7)."""

    return DiscordAdapter(public_key_env=public_key_env, bot_token_env=bot_token_env)
