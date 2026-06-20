"""Tests for connections: spec building, namespacing, discovery, OpenAPI gen."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from leve.connections import (
    ConnectionSpec,
    define_mcp_connection,
    define_openapi_connection,
    discover_tools,
    namespaced,
)
from leve.connections.openapi import build_openapi_tools
from leve.loader import load_project

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""


def test_define_mcp_connection():
    conn = define_mcp_connection(url="https://mcp.linear.app/sse", transport="sse", description="Linear")
    assert conn.kind == "mcp"
    assert conn.config == {"url": "https://mcp.linear.app/sse", "transport": "sse"}


def test_define_openapi_connection():
    conn = define_openapi_connection(spec={"paths": {}}, base_url="https://api.x", description="X")
    assert conn.kind == "openapi"
    assert conn.config["base_url"] == "https://api.x"


def test_namespacing_is_non_mutating():
    class In(BaseModel):
        pass

    tool = StructuredTool.from_function(func=lambda: "x", name="create_issue", description="d", args_schema=In)
    nt = namespaced(tool, "linear")
    assert nt.name == "linear.create_issue"
    assert tool.name == "create_issue"  # original untouched


def test_openapi_tool_generation():
    spec = {
        "paths": {
            "/items/{id}": {
                "get": {
                    "operationId": "get_item",
                    "summary": "Get an item",
                    "parameters": [
                        {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
                    ],
                }
            }
        }
    }
    tools = build_openapi_tools(spec, base_url="https://api.x")
    assert [t.name for t in tools] == ["get_item"]
    props = tools[0].args_schema.model_json_schema()["properties"]
    assert "id" in props and "verbose" in props


def test_openapi_resolves_ref_parameters():
    spec = {
        "components": {
            "parameters": {
                "Id": {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
            }
        },
        "paths": {
            "/items/{id}": {
                "get": {"operationId": "get_item", "parameters": [{"$ref": "#/components/parameters/Id"}]}
            }
        },
    }
    tools = build_openapi_tools(spec, base_url="https://api.x")
    assert "id" in tools[0].args_schema.model_json_schema()["properties"]


def test_openapi_header_param_is_routed_and_required():
    spec = {
        "paths": {
            "/x": {
                "get": {
                    "operationId": "getx",
                    "parameters": [
                        {"name": "X-Key", "in": "header", "required": True, "schema": {"type": "string"}}
                    ],
                }
            }
        }
    }
    schema = build_openapi_tools(spec, base_url="https://api.x")[0].args_schema.model_json_schema()
    assert "X-Key" in schema["properties"]
    assert "X-Key" in schema.get("required", [])


def test_loader_discovers_connections(tmp_path, write_project):
    conn = """\
        from leve.connections import define_mcp_connection
        connection = define_mcp_connection(
            url="https://mcp.linear.app/sse", transport="sse", description="Linear"
        )
    """
    config = write_project(
        tmp_path, agent_py=AGENT_PY, extra_files={"agent/connections/linear.py": conn}
    )
    loaded = load_project(config)
    assert [c.name for c in loaded.connections] == ["linear"]
    assert loaded.connections[0].kind == "mcp"


async def test_discover_mcp_namespaces_tools(monkeypatch):
    class In(BaseModel):
        pass

    fake_tool = StructuredTool.from_function(
        func=lambda: "x", name="create_issue", description="d", args_schema=In
    )

    class FakeClient:
        def __init__(self, connections):
            self.connections = connections

        async def get_tools(self):
            return [fake_tool]

    import langchain_mcp_adapters.client as client_mod

    monkeypatch.setattr(client_mod, "MultiServerMCPClient", FakeClient)

    spec = ConnectionSpec(
        kind="mcp", description="d", config={"url": "x", "transport": "sse"}, name="linear"
    )
    tools = await discover_tools((spec,))
    assert [t.name for t in tools] == ["linear.create_issue"]
