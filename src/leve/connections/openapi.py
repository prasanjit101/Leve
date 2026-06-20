"""OpenAPI → tools generator (SPEC §4.6).

Generates one tool per operation in an OpenAPI document. Each tool collects the
operation's path/query/header parameters and (optionally) a JSON body, then
performs the HTTP call with ``httpx``. Tool *generation* is pure and
offline-testable; only execution touches the network.

This is deliberately compact — it targets the common REST shape — but it does
resolve ``$ref`` parameters, merge path-level shared parameters, route header
parameters, and de-duplicate tool names so realistic specs don't crash or
silently misbehave.
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def build_openapi_tools(
    spec: dict[str, Any], *, base_url: str, headers: dict[str, str] | None = None
) -> list[BaseTool]:
    """Build a tool for each operation in the OpenAPI ``spec``."""

    tools: list[BaseTool] = []
    seen: set[str] = set()
    for path, path_item in spec.get("paths", {}).items():
        shared = [_resolve(p, spec) for p in path_item.get("parameters", [])]
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not operation:
                continue
            params = shared + [_resolve(p, spec) for p in operation.get("parameters", [])]
            tool = _build_tool(path, method, operation, params, base_url, headers)
            tool = _dedupe(tool, seen)
            tools.append(tool)
    return tools


def _build_tool(
    path: str,
    method: str,
    operation: dict,
    params: list[dict],
    base_url: str,
    headers: dict[str, str] | None,
) -> BaseTool:
    name = operation.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
    description = operation.get("summary") or operation.get("description") or name
    has_body = "requestBody" in operation

    by_loc = {loc: {p["name"] for p in params if p.get("in") == loc and "name" in p}
              for loc in ("path", "query", "header")}
    args_model = _args_model(name, params, has_body)

    async def call(**kwargs: Any) -> dict:
        url = base_url.rstrip("/") + _format_path(path, kwargs, by_loc["path"])
        query = {k: kwargs[k] for k in by_loc["query"] if kwargs.get(k) is not None}
        req_headers = dict(headers or {})
        req_headers.update(
            {k: str(kwargs[k]) for k in by_loc["header"] if kwargs.get(k) is not None}
        )
        body = kwargs.get("body")
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method.upper(), url, params=query, json=body, headers=req_headers or None
            )
            return {"status": response.status_code, "body": _safe_json(response)}

    return StructuredTool.from_function(
        coroutine=call, name=name, description=description, args_schema=args_model
    )


def _args_model(name: str, params: list[dict], has_body: bool) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for param in params:
        if "name" not in param:
            continue  # unresolvable/$ref-only entry — skip rather than crash
        py_type = _TYPE_MAP.get((param.get("schema") or {}).get("type"), str)
        # Path params are always required for a valid URL, regardless of the spec.
        required = param.get("required", False) or param.get("in") == "path"
        if required:
            fields[param["name"]] = (py_type, Field(..., description=param.get("description", "")))
        else:
            fields[param["name"]] = (
                py_type | None,
                Field(None, description=param.get("description", "")),
            )
    if has_body:
        fields["body"] = (dict | None, Field(None, description="JSON request body."))
    return create_model(f"{name}_Input", **fields)


def _resolve(node: Any, spec: dict[str, Any]) -> dict:
    """Resolve a possible ``{"$ref": "#/..."}`` against the document root."""

    if isinstance(node, dict) and "$ref" in node:
        target: Any = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return target if isinstance(target, dict) else {}
    return node if isinstance(node, dict) else {}


def _dedupe(tool: BaseTool, seen: set[str]) -> BaseTool:
    if tool.name not in seen:
        seen.add(tool.name)
        return tool
    suffix = 2
    while f"{tool.name}_{suffix}" in seen:
        suffix += 1
    new_name = f"{tool.name}_{suffix}"
    seen.add(new_name)
    return tool.model_copy(update={"name": new_name})


def _format_path(path: str, kwargs: dict, path_params: set[str]) -> str:
    for key in path_params:
        path = path.replace(f"{{{key}}}", str(kwargs[key]))
    return path


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
