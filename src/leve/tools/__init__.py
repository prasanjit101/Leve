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
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, create_model

from leve.security.auth import InjectedPrincipal, current_principal


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
        """Compile this spec into a LangChain ``StructuredTool``.

        Parameters defaulted with :class:`~leve.security.auth.InjectedPrincipal` are
        stripped from the model-facing schema and filled from the trusted runtime
        context at execution — the model never sees or sets them (SPEC §5.6).
        """

        injected = self._injected_params()
        is_async = inspect.iscoroutinefunction(self.func)
        func = self._wrap_injection(injected, is_async) if injected else self.func

        args_schema = self.input_schema
        if args_schema is not None:
            # An explicit schema must not declare an injected param — that would
            # expose it to the model (the value is overwritten anyway). Fail loudly.
            leaked = [name for name in injected if name in args_schema.model_fields]
            if leaked:
                raise ValueError(
                    f"Tool '{self.name}' input_schema must not declare injected "
                    f"parameter(s) {leaked}; they are filled from runtime context."
                )
        elif injected:
            args_schema = self._schema_excluding(injected)

        return StructuredTool.from_function(
            func=None if is_async else func,
            coroutine=func if is_async else None,
            name=self.name,
            description=self.description,
            args_schema=args_schema,
            infer_schema=args_schema is None,
        )

    def _injected_params(self) -> list[str]:
        return [
            name
            for name, param in inspect.signature(self.func).parameters.items()
            if isinstance(param.default, InjectedPrincipal)
        ]

    def _wrap_injection(
        self, injected: list[str], is_async: bool
    ) -> Callable[..., Any]:
        func = self.func

        if is_async:

            async def awrapper(**kwargs: Any) -> Any:
                for name in injected:
                    kwargs[name] = current_principal()
                return await func(**kwargs)

            return awrapper

        def wrapper(**kwargs: Any) -> Any:
            for name in injected:
                kwargs[name] = current_principal()
            return func(**kwargs)

        return wrapper

    def _schema_excluding(self, injected: list[str]) -> type[BaseModel]:
        """Build an args schema from the function's non-injected parameters."""

        fields: dict[str, Any] = {}
        for name, param in inspect.signature(self.func).parameters.items():
            if name in injected or name in ("self", "cls"):
                continue
            annotation = (
                param.annotation
                if param.annotation is not inspect.Parameter.empty
                else Any
            )
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[name] = (annotation, default)
        return create_model(f"{self.name}_Input", **fields)


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


# Re-exported so tools can do `from leve.tools import define_tool, InjectedPrincipal`.
__all__ = ["ToolSpec", "define_tool", "InjectedPrincipal"]
