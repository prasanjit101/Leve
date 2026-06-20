"""Tools — ``agent/tools/<name>.py``.

One file, one tool. Decorating a function with :func:`define_tool` turns it into
a :class:`ToolSpec` carried on the module under the function's name; the loader
collects it *by type* (SPEC §4.3), so the symbol name is irrelevant and the
function name doubles as the tool name.

The decorator returns inert data; :meth:`ToolSpec.build` produces the LangChain
``StructuredTool`` that the graph binds. Splitting *describe* from *build* keeps
discovery pure and lets the graph layer inject cross-cutting behaviour (approval
gates in M2, principal injection in M5) without the tool author wiring any of it.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@dataclass(frozen=True)
class ToolSpec:
    """A described tool: the user's function plus its model-facing metadata.

    ``needs_approval`` is stored here but only *enforced* by the graph builder
    (M2); keeping it on the spec means the tool file is the single source of
    truth for its own approval policy.
    """

    func: Callable[..., Any]
    name: str
    description: str
    input_schema: type[BaseModel] | None
    needs_approval: Callable[..., bool] | None = None

    def build(self) -> StructuredTool:
        """Compile this spec into a LangChain ``StructuredTool``."""

        is_async = inspect.iscoroutinefunction(self.func)
        return StructuredTool.from_function(
            func=None if is_async else self.func,
            coroutine=self.func if is_async else None,
            name=self.name,
            description=self.description,
            args_schema=self.input_schema,
            # When no schema is given, infer it from the function signature so a
            # plain typed function still gets a JSON schema for the model.
            infer_schema=self.input_schema is None,
        )


def define_tool(
    func: Callable[..., Any] | None = None,
    *,
    description: str | None = None,
    input_schema: type[BaseModel] | None = None,
    name: str | None = None,
    needs_approval: Callable[..., bool] | None = None,
) -> Any:
    """Mark a function as the tool exported by its file.

    Usable both as ``@define_tool(...)`` (the documented form) and as a bare
    ``@define_tool`` decorator.

    Args:
        description: What the tool does — shown to the model. Falls back to the
            function's docstring.
        input_schema: A Pydantic model describing the arguments; it generates the
            JSON schema handed to the model. If omitted, the schema is inferred
            from the function signature.
        name: Override the tool name (defaults to the function name).
        needs_approval: Optional predicate ``(tool_input[, principal]) -> bool``;
            when truthy the tool pauses for human approval (enforced in M2).
    """

    def wrap(fn: Callable[..., Any]) -> ToolSpec:
        resolved_description = description or inspect.getdoc(fn) or ""
        if not resolved_description:
            raise ValueError(
                f"Tool '{fn.__name__}' needs a description "
                "(pass description=... or add a docstring)."
            )
        return ToolSpec(
            func=fn,
            name=name or fn.__name__,
            description=resolved_description,
            input_schema=input_schema,
            needs_approval=needs_approval,
        )

    # Support both @define_tool and @define_tool(...).
    if func is not None:
        return wrap(func)
    return wrap


__all__ = ["ToolSpec", "define_tool"]
