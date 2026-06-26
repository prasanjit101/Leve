"""Credential brokering — the "Connect" equivalent (SPEC §5.7).

A ``CredentialBroker`` turns a ``(principal, audience)`` into a downstream
:class:`~leve.auth.Credential` without the model ever seeing the secret. Three
built-ins mirror the spec:

* ``static`` — a fixed secret from the environment (dev / shared service account).
* ``oauth_store`` — per-caller OAuth tokens keyed by ``(tenant, subject, provider)``;
  raises :class:`NeedsConsent` when the caller hasn't authorized yet.
* ``token_exchange`` — RFC 8693 exchange of the caller's session token (stateless).

Capturing consent reuses the human-in-the-loop machinery: a :class:`NeedsConsent`
raised inside a tool is converted to an ``interrupt`` by the principal middleware,
so the session pauses (free, checkpointed) until the user authorizes and resumes.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from leve.config import CredentialsConfig
from leve.errors import ConfigError
from leve.security.auth import Credential, Principal

# Provider presets: authorize/token endpoints + default scopes (SPEC §5.7).
# You supply only client_id / client_secret via env; the rest comes from here.
PROVIDER_PRESETS: dict[str, dict] = {
    "slack": {
        "authorize": "https://slack.com/oauth/v2/authorize",
        "token": "https://slack.com/api/oauth.v2.access",
    },
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
    },
    "linear": {
        "authorize": "https://linear.app/oauth/authorize",
        "token": "https://api.linear.app/oauth/token",
    },
    "notion": {
        "authorize": "https://api.notion.com/v1/oauth/authorize",
        "token": "https://api.notion.com/v1/oauth/token",
    },
    "salesforce": {
        "authorize": "https://login.salesforce.com/services/oauth2/authorize",
        "token": "https://login.salesforce.com/services/oauth2/token",
    },
    "snowflake": {"authorize": "", "token": ""},
}


class NeedsConsent(Exception):
    """Raised when an interactive broker needs the caller to authorize a provider.

    The principal middleware turns this into a consent interrupt (SPEC §5.7).
    """

    def __init__(self, provider: str, authorize_url: str | None = None):
        super().__init__(f"Caller has not authorized '{provider}'.")
        self.provider = provider
        self.authorize_url = authorize_url


class CredentialBroker(ABC):
    @abstractmethod
    async def resolve(self, principal: Principal, audience: str) -> Credential:
        """Resolve a credential for ``principal`` to access ``audience``."""


class StaticBroker(CredentialBroker):
    """Returns a fixed secret from the environment (not per-caller).

    Resolves ``LEVE_CRED_<AUDIENCE>`` (e.g. ``LEVE_CRED_WAREHOUSE``). Suitable for
    dev or a tool that legitimately acts as the app.
    """

    async def resolve(self, principal: Principal, audience: str) -> Credential:
        token = os.environ.get(f"LEVE_CRED_{audience.upper()}")
        if token is None:
            # Fail loudly and consistently with the other brokers, rather than
            # returning a None token that becomes a confusing downstream 401.
            raise PermissionError(
                f"No static credential for audience '{audience}' "
                f"(set LEVE_CRED_{audience.upper()})."
            )
        return Credential(token=token)


class OAuthStoreBroker(CredentialBroker):
    """Per-caller OAuth tokens keyed by ``(tenant, subject, provider)``.

    M5 ships the consent-as-interrupt contract with a pluggable token store
    (in-memory by default). When a caller has no token for the provider, it
    raises :class:`NeedsConsent`; once stored (after the OAuth callback), resolve
    succeeds on the resumed run.
    """

    def __init__(self, store: dict | None = None):
        # key: (tenant, subject, provider) -> token. Swap for a Postgres-backed
        # store in production; the interface is what matters here.
        self._tokens: dict[tuple, str] = store if store is not None else {}

    def key(self, principal: Principal, provider: str) -> tuple:
        return (principal.tenant, principal.subject, provider)

    def put(self, principal: Principal, provider: str, token: str) -> None:
        self._tokens[self.key(principal, provider)] = token

    async def resolve(self, principal: Principal, audience: str) -> Credential:
        token = self._tokens.get(self.key(principal, audience))
        if token is None:
            preset = PROVIDER_PRESETS.get(audience, {})
            raise NeedsConsent(audience, authorize_url=preset.get("authorize") or None)
        return Credential(token=token)


class TokenExchangeBroker(CredentialBroker):
    """RFC 8693 token exchange against the org IdP (stateless).

    Exchanges the caller's session token (carried in claims) for a downstream
    token. The HTTP exchange itself is environment-specific; M5 provides the
    contract and a passthrough when the caller already holds an audience token.
    """

    async def resolve(self, principal: Principal, audience: str) -> Credential:
        # Read from the private `secrets` field, never `claims` (which is
        # repr-visible authorization data, not secret material).
        token = (principal.secrets.get("audience_tokens") or {}).get(audience)
        if token is None:
            raise PermissionError(f"No exchangeable token for audience '{audience}'.")
        return Credential(token=token)


def create_broker(config: CredentialsConfig) -> CredentialBroker:
    """Instantiate the configured credential broker (SPEC §5.7, §11)."""

    if config.broker == "static":
        return StaticBroker()
    if config.broker == "oauth_store":
        return OAuthStoreBroker()
    if config.broker == "token_exchange":
        return TokenExchangeBroker()
    raise ConfigError(f"Unknown credentials broker '{config.broker}'.")
