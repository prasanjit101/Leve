"""Channels — ``agent/channels/<name>.py`` (SPEC §4.7).

A channel adapter maps an external surface (Slack, Discord, …) onto Leve's
session API. The HTTP API is always on; channels add inbound webhooks that
verify the request, parse it into a message, drive the matching session, and
deliver the reply back to the surface. A conversation maps to a stable
``thread_id`` (the session key), so the same thread can move between surfaces.

The adapter interface is the contract — new surfaces add an adapter, not new
core surface (Open/Closed).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leve.auth import Principal


@dataclass(frozen=True)
class IncomingMessage:
    """A normalized inbound message extracted from a channel payload."""

    session_key: str  # becomes the LangGraph thread_id
    text: str
    target: dict[str, Any] = field(default_factory=dict)  # where to deliver the reply
    # The authenticated caller behind this message (SPEC §5.6). The adapter builds
    # it from the surface's verified identity; the broker is attached downstream.
    principal: Principal | None = None


class ChannelAdapter(ABC):
    """Maps one external surface to the session API."""

    name: str = "channel"

    def verify(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Authenticate an inbound request. Default: accept (override for real surfaces)."""

        return True

    def is_retry(self, headers: Mapping[str, str]) -> bool:
        """True if this is a provider re-delivery of an already-handled event.

        Retries are acknowledged without re-running the agent (prevents duplicate
        replies when a turn outlasts the provider's ack deadline).
        """

        return False

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Return the full handshake reply if this is a setup/ping payload, else None.

        Surfaces differ (Slack expects ``{"challenge": ...}``, Discord ``{"type": 1}``),
        so the adapter returns the exact body to send back.
        """

        return None

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> IncomingMessage | None:
        """Extract an :class:`IncomingMessage`, or None to ignore the payload."""

    @abstractmethod
    async def deliver(self, target: dict[str, Any], text: str) -> None:
        """Send the agent's reply back to the surface."""


@dataclass(frozen=True)
class ChannelSpec:
    """A described channel. ``name`` (the route segment) is filled by the loader."""

    adapter: ChannelAdapter
    name: str = ""


def define_channel(adapter: ChannelAdapter) -> ChannelSpec:
    """Declare a channel from an adapter (SPEC §4.7)."""

    return ChannelSpec(adapter=adapter)


__all__ = ["ChannelAdapter", "ChannelSpec", "IncomingMessage", "define_channel"]
