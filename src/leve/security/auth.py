"""Per-caller identity and credentials (SPEC §5.6).

Leve treats the agent — the model *and* any code it writes — as untrusted, and
enforces the asker's permissions outside it. The :class:`Principal` carries the
caller's identity and a way to resolve downstream credentials on their behalf. It
is created by the channel adapter from an already-authenticated identity, travels
in runtime context (never in model-visible message state), and reaches tools as
an *injected* argument the model cannot read or forge.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leve.security.credentials import CredentialBroker


@dataclass(frozen=True)
class Credential:
    """A downstream credential resolved for a specific caller and audience."""

    token: str | None = None
    db_role: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Principal:
    """The caller's identity plus a broker to resolve their credentials.

    The broker is private (leading underscore) and never serialized into model
    state — only the harness uses it, via :meth:`credential`.
    """

    subject: str
    tenant: str | None = None
    claims: Mapping[str, Any] = field(default_factory=dict)
    broker: CredentialBroker | None = field(default=None, repr=False)
    # Secret material (e.g. pre-exchanged downstream tokens) kept OUT of `claims`
    # and hidden from repr/tracing so it can't leak into logs or model state.
    secrets: Mapping[str, Any] = field(default_factory=dict, repr=False)

    async def credential(self, audience: str) -> Credential:
        """Resolve a downstream credential for THIS caller and ``audience``."""

        if self.broker is None:
            raise PermissionError(
                f"No credential broker configured to resolve '{audience}'."
            )
        return await self.broker.resolve(self, audience)

    def narrow(self, *, claims: Mapping[str, Any] | None = None) -> Principal:
        """Return a principal with a *subset* of claims (delegation can only narrow).

        A claim value can only shrink, never grow: requested values are intersected
        against the held values (list/set claims) or kept only if identical (scalars).
        Values are taken from the *held* claims, never from the caller — so a
        subagent can never gain access the asker lacks (SPEC §5.6).
        """

        if claims is None:
            return self
        kept: dict[str, Any] = {}
        for key, requested in claims.items():
            if key not in self.claims:
                continue  # cannot introduce a new claim
            held = self.claims[key]
            if isinstance(held, (list, tuple, set)) and isinstance(
                requested, (list, tuple, set)
            ):
                allowed = set(requested)
                kept[key] = [item for item in held if item in allowed]  # shrink only
            elif requested == held:
                kept[key] = held  # scalar may be kept, never replaced
            # else: a different scalar value would widen → drop the claim entirely
        return Principal(
            subject=self.subject,
            tenant=self.tenant,
            claims=kept,
            broker=self.broker,
            secrets=self.secrets,
        )


def anonymous() -> Principal:
    """The default principal for unauthenticated/local contexts."""

    return Principal(subject="anonymous", tenant=None, claims={})


def with_broker(
    principal: Principal | None, broker: CredentialBroker | None
) -> Principal | None:
    """Attach a credential broker to a principal (no-op if either is missing)."""

    from dataclasses import replace

    if principal is None or broker is None or principal.broker is not None:
        return principal
    return replace(principal, broker=broker)


def app_principal(
    subject: str = "app",
    *,
    tenant: str | None = None,
    scopes: tuple[str, ...] = (),
    broker: CredentialBroker | None = None,
) -> Principal:
    """An explicit, auditable service identity for scheduled/non-interactive runs (SPEC §5.6)."""

    return Principal(
        subject=subject,
        tenant=tenant,
        claims={"scopes": list(scopes), "app": True},
        broker=broker,
    )


class InjectedPrincipal:
    """Marker default for a tool parameter that should receive the caller principal.

    Usage: ``async def run_sql(sql: str, principal: Principal = InjectedPrincipal())``.
    The parameter is stripped from the model-facing schema and filled by the
    runtime from the trusted context — the model neither sees nor sets it.
    """


# The principal for the in-flight tool call, set by PrincipalMiddleware from the
# run's runtime context and read by injected-principal tools. A ContextVar keeps
# it correct under concurrency (each task sees its own value).
_current_principal: ContextVar[Principal | None] = ContextVar(
    "leve_principal", default=None
)


def set_current_principal(principal: Principal | None):
    return _current_principal.set(principal)


def reset_current_principal(token) -> None:
    _current_principal.reset(token)


def current_principal() -> Principal | None:
    return _current_principal.get()
