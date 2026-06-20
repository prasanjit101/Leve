"""Tests for the @define_tool decorator and ToolSpec.build()."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from leve.tools import ToolSpec, define_tool


class _Input(BaseModel):
    text: str = Field(description="Some text.")


def test_define_tool_basic():
    @define_tool(description="Echo text.", input_schema=_Input)
    def echo(text: str) -> str:
        return text

    assert isinstance(echo, ToolSpec)
    assert echo.name == "echo"
    assert echo.description == "Echo text."

    structured = echo.build()
    assert structured.name == "echo"
    assert structured.description == "Echo text."
    assert structured.args_schema is _Input


def test_name_override():
    @define_tool(description="x", input_schema=_Input, name="custom")
    def whatever(text: str) -> str:
        return text

    assert whatever.name == "custom"
    assert whatever.build().name == "custom"


def test_description_falls_back_to_docstring():
    @define_tool(input_schema=_Input)
    def echo(text: str) -> str:
        """Docstring description."""
        return text

    assert echo.description == "Docstring description."


def test_missing_description_raises():
    with pytest.raises(ValueError):

        @define_tool(input_schema=_Input)
        def echo(text: str) -> str:
            return text


def test_bare_decorator_form():
    @define_tool
    def echo(text: str) -> str:
        """Bare form."""
        return text

    assert isinstance(echo, ToolSpec)
    assert echo.name == "echo"


async def test_async_tool_builds_with_coroutine():
    @define_tool(description="async echo", input_schema=_Input)
    async def echo(text: str) -> str:
        return text

    structured = echo.build()
    result = await structured.ainvoke({"text": "hi"})
    assert result == "hi"
