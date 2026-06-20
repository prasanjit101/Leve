"""Leve-specific agent middleware (SPEC §4.3, §5.3).

Human-in-the-loop **approval** is expressed as a per-tool ``needs_approval``
predicate on the tool file. Because the predicate is *dynamic* (a function of the
tool input, and in M5 the caller's principal), it cannot be a static
``HumanInTheLoopMiddleware`` config — so Leve ships :class:`ApprovalMiddleware`,
which evaluates the predicate inside the tools node and calls LangGraph
``interrupt()`` *before* execution when approval is required. The pause is free
(checkpointed, idle) and resumes with ``Command(resume={"approved": True})``.

The predicate runs in the trusted harness and reads the principal from runtime
context (not model state), so the model can never talk its way past the gate.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from leve.tools import ToolSpec


@dataclass(frozen=True)
class _Policy:
    predicate: Callable[..., bool]
    input_schema: type | None


class ApprovalMiddleware(AgentMiddleware):
    """Gates tool calls whose ``needs_approval`` predicate returns truthy."""

    def __init__(self, tools: tuple[ToolSpec, ...]):
        super().__init__()
        self._policies: dict[str, _Policy] = {
            tool.name: _Policy(tool.needs_approval, tool.input_schema)
            for tool in tools
            if tool.needs_approval is not None
        }

    @property
    def gated_tools(self) -> set[str]:
        return set(self._policies)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        call = request.tool_call
        policy = self._policies.get(call["name"])

        if policy is not None and self._needs_approval(policy, call, request):
            decision = interrupt(
                {"type": "approval", "tool": call["name"], "input": call.get("args", {})}
            )
            if not _is_approved(decision):
                return ToolMessage(
                    content=f"Tool call '{call['name']}' was denied by a human reviewer.",
                    tool_call_id=call["id"],
                    name=call["name"],
                )

        return await handler(request)

    @staticmethod
    def _needs_approval(policy: _Policy, call: dict, request: ToolCallRequest) -> bool:
        args = call.get("args", {}) or {}

        # The predicate always reads attributes (e.g. tool_input.sql), so give it
        # an attribute-accessible object whether or not a schema is declared.
        if policy.input_schema is not None:
            try:
                tool_input: Any = policy.input_schema(**args)
            except Exception:
                # Malformed args can't run anyway — let the tool node return its
                # standard, recoverable validation error instead of gating a
                # non-executable call (or crashing the gate).
                return False
        else:
            tool_input = SimpleNamespace(**args)

        try:
            params = inspect.signature(policy.predicate).parameters
            if len(params) >= 2:
                principal = _principal_from(request)  # None until M5 populates it
                return bool(policy.predicate(tool_input, principal))
            return bool(policy.predicate(tool_input))
        except Exception:
            # A gate that errors must fail *closed* — require human review rather
            # than silently letting the call through. The model cannot exploit a
            # buggy predicate to skip approval.
            return True


def _is_approved(decision: Any) -> bool:
    """Interpret a resume value as approve/deny.

    Accepts ``{"approved": bool}`` (the documented shape) or a bare boolean.
    """

    if isinstance(decision, dict):
        return bool(decision.get("approved"))
    return bool(decision)


def _principal_from(request: ToolCallRequest) -> Any:
    """Extract the caller principal from runtime context, if present (M5)."""

    context = getattr(getattr(request, "runtime", None), "context", None)
    return getattr(context, "principal", None)
