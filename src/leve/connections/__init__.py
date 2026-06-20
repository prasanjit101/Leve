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
    headers = _resolve_headers(spec.auth, principal)
    if headers:
        connection["headers"] = headers

    client = MultiServerMCPClient({spec.name: connection})
    raw = await client.get_tools()
    return [namespaced(tool, spec.name) for tool in raw]


def _discover_openapi(spec: ConnectionSpec, principal: Any) -> list[BaseTool]:
    from leve.connections.openapi import build_openapi_tools

    return [
        namespaced(tool, spec.name)
        for tool in build_openapi_tools(
            spec.config["spec"],
            base_url=spec.config["base_url"],
            headers=_resolve_headers(spec.auth, principal),
        )
    ]


def _resolve_headers(auth: dict[str, Any] | None, principal: Any) -> dict[str, str] | None:
    """Resolve auth into request headers.

    M3 supports a custom ``get_token`` callable (the common static/dev case).
    The declarative ``broker`` form resolves through the credential broker in M5;
    until then it is ignored here so discovery still works without credentials.
    """

    if not auth:
        return None
    get_token: Callable[[Any], str] | None = auth.get("get_token")
    if get_token is None:
        return None
    token = get_token(principal)
    return {"Authorization": f"Bearer {token}"} if token else None
