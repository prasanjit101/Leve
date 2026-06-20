"""Connections — ``agent/connections/<name>.py`` (SPEC §4.6).

A connection points at an MCP server or an OpenAPI-described API. At build time
Leve discovers the remote tools, **namespaces** them by the connection name
(``linear.create_issue``), and adds them to the tool node. Credentials are
brokered by Leve — the model never sees URLs or tokens.

Per-caller credential resolution (``auth.get_token(principal)``) is wired through
here but only fully realized in M5 (the credential broker). In M3, discovery and
namespacing work; a connection with a static token resolves it here.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import BaseTool

from leve.errors import LoaderError


@dataclass(frozen=True)
class ConnectionSpec:
    """A described connection. ``name`` is filled by the loader (file stem)."""

    kind: str  # "mcp" | "openapi"
    description: str
    config: dict[str, Any]
    auth: dict[str, Any] | None = None
    name: str = ""


def define_mcp_connection(
    *,
    url: str,
    transport: str = "sse",
    description: str,
    auth: dict[str, Any] | None = None,
) -> ConnectionSpec:
    """Define an MCP server connection (SPEC §4.6)."""

    return ConnectionSpec(
        kind="mcp",
        description=description,
        config={"url": url, "transport": transport},
        auth=auth,
    )


def define_openapi_connection(
    *,
    spec: dict[str, Any],
    base_url: str,
    description: str,
    auth: dict[str, Any] | None = None,
) -> ConnectionSpec:
    """Define a connection generated from an OpenAPI document (SPEC §4.6)."""

    return ConnectionSpec(
        kind="openapi",
        description=description,
        config={"spec": spec, "base_url": base_url},
        auth=auth,
    )


async def discover_tools(
    specs: tuple[ConnectionSpec, ...], *, principal: Any = None
) -> list[BaseTool]:
    """Discover and namespace the tools for every connection."""

    tools: list[BaseTool] = []
    for spec in specs:
        try:
            if spec.kind == "mcp":
                tools.extend(await _discover_mcp(spec, principal))
            elif spec.kind == "openapi":
                tools.extend(_discover_openapi(spec, principal))
            else:  # pragma: no cover - define_* constructors constrain the kind
                raise LoaderError(f"Unknown connection kind '{spec.kind}'.")
        except LoaderError:
            raise
        except Exception as exc:
            # Name the offending connection instead of leaking an opaque adapter
            # traceback (e.g. an unreachable MCP server).
            raise LoaderError(
                f"Connection '{spec.name}' failed to discover tools: {exc}"
            ) from exc
    return tools


def namespaced(tool: BaseTool, prefix: str) -> BaseTool:
    """Return a copy of ``tool`` with its name prefixed by the connection name."""

    return tool.model_copy(update={"name": f"{prefix}.{tool.name}"})


async def _discover_mcp(spec: ConnectionSpec, principal: Any) -> list[BaseTool]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connection: dict[str, Any] = {
        "transport": spec.config["transport"],
        "url": spec.config["url"],
    }
    # MCP clients bind headers at discovery (build) time, so per-caller MCP tokens
    # are a known limitation: at build there is no caller, and we fail closed
    # rather than bake a shared per-user token. Shared/static tokens still work.
    headers = await _resolve_headers_safe(spec.auth, principal)
    if headers:
        connection["headers"] = headers

    client = MultiServerMCPClient({spec.name: connection})
    raw = await client.get_tools()
    return [namespaced(tool, spec.name) for tool in raw]


def _discover_openapi(spec: ConnectionSpec, principal: Any) -> list[BaseTool]:
    from leve.connections.openapi import build_openapi_tools

    # OpenAPI tools resolve headers per call from the live caller principal
    # (true per-caller credentials, SPEC §5.6) rather than baking one token.
    async def headers_provider() -> dict[str, str] | None:
        from leve.auth import current_principal

        return await _resolve_headers(spec.auth, current_principal())

    return [
        namespaced(tool, spec.name)
        for tool in build_openapi_tools(
            spec.config["spec"],
            base_url=spec.config["base_url"],
            headers_provider=headers_provider,
        )
    ]


async def _resolve_headers(auth: dict[str, Any] | None, principal: Any) -> dict[str, str] | None:
    """Resolve auth into request headers for ``principal``.

    Supports a custom ``get_token(principal)`` callable (may be sync or async) and
    the declarative ``{"broker", "provider"}`` form, which resolves through the
    caller's :class:`~leve.auth.Principal` credential broker. Returns ``None`` (no
    header — fail closed) when no credential can be resolved, never a forged one.
    """

    if not auth:
        return None

    token: str | None = None
    get_token: Callable[[Any], Any] | None = auth.get("get_token")
    if get_token is not None:
        result = get_token(principal)
        token = await result if inspect.isawaitable(result) else result
    elif auth.get("broker") and principal is not None:
        provider = auth.get("provider")
        if provider:
            credential = await principal.credential(provider)
            token = credential.token

    return {"Authorization": f"Bearer {token}"} if token else None


async def _resolve_headers_safe(auth: dict[str, Any] | None, principal: Any) -> dict[str, str] | None:
    """Build-time header resolution that fails *closed* instead of crashing.

    At build there is no caller (``principal`` is ``None``), so a per-caller
    resolver can't run. Rather than bake one identity's token for all callers or
    abort the whole build, we resolve only what works without a principal and
    otherwise return ``None`` (the call then runs unauthenticated → fails closed).
    """

    try:
        return await _resolve_headers(auth, principal)
    except Exception:
        return None
