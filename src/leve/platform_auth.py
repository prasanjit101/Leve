"""Platform thread/store auth scoping (SPEC §5.6).

On LangGraph Platform, Leve registers an ``Auth`` handler: ``@auth.authenticate``
verifies the incoming request and produces a :class:`~leve.auth.Principal`, and
``@auth.on`` handlers scope every thread/store/run operation by
``(tenant, subject)`` so a user can only touch their own sessions, and a resumed
session's identity is re-verified rather than trusted from the original request.

The ``langgraph_sdk`` Auth API is imported lazily so this module is importable
without the SDK; ``make_auth`` is called only in a Platform deployment.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from leve.auth import Principal


def store_namespace(principal: Principal) -> tuple[str, str]:
    """The Store namespace prefix for a caller — never bleeds across users (§5.6)."""

    return (principal.tenant or "_", principal.subject)


def make_auth(authenticate: Callable[[dict], Principal]) -> Any:
    """Build a LangGraph Platform ``Auth`` that scopes resources by caller.

    Args:
        authenticate: Maps a verified request (headers/token) to a Principal.
            You supply how identity is verified (OIDC, API key, …); Leve wires
            the per-resource authorization.
    """

    try:
        from langgraph_sdk import Auth  # type: ignore
    except ImportError as exc:  # pragma: no cover - only needed on Platform
        raise RuntimeError(
            "Platform auth requires langgraph_sdk (provided by LangGraph Platform)."
        ) from exc

    auth = Auth()

    @auth.authenticate
    async def _authenticate(
        headers: dict, **_: Any
    ):  # pragma: no cover - Platform path
        principal = authenticate(headers)
        # The identity dict langgraph stores; the owner filter below reads it.
        return {
            "identity": principal.subject,
            "tenant": principal.tenant or "_",
        }

    @auth.on  # pragma: no cover - Platform path
    async def _authorize(ctx: Any, value: Any):
        # Scope every thread/store/run op to (tenant, subject): stamp on writes,
        # filter on reads, so a user only ever sees their own resources.
        owner = f"{ctx.user.get('tenant', '_')}:{ctx.user.identity}"
        if isinstance(value, dict):
            metadata = value.setdefault("metadata", {})
            # Force the owner from the verified identity — never honour a
            # client-supplied owner (that would let a caller forge ownership).
            metadata["owner"] = owner
        return {"owner": owner}

    return auth
