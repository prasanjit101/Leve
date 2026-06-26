"""Subagents — ``agent/subagents/<name>/`` (SPEC §4.5).

A subagent is the *same directory shape one level down*, compiled into its own
LangGraph subgraph with a fresh state channel (clean context window) and only the
tools in its folder. The parent invokes it like a tool: Leve auto-generates a
``delegate_to_<name>`` tool that runs the subgraph to completion and returns
**only its final message** — the subagent's intermediate turns never enter the
parent's context, which is the whole point of delegating.

Each delegation runs on its own child thread (namespaced under the parent's),
so it is checkpointed/durable without polluting the parent thread.
"""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from leve.security.auth import current_principal
from leve.errors import LoaderError
from leve.core.runtime import LeveContext


class DelegateInput(BaseModel):
    task: str = Field(
        description="A self-contained instruction describing the task to delegate."
    )


def make_delegation_tool(
    name: str, description: str, subgraph: CompiledStateGraph, *, recursion_limit: int
) -> StructuredTool:
    """Build the ``delegate_to_<name>`` tool that runs a subagent subgraph.

    The subagent's full transcript stays on its own child thread; only the final
    message is returned to the parent.
    """

    if not description:
        raise LoaderError(
            f"Subagent '{name}' needs a description (define_agent(description=...)) "
            "— it becomes the delegation tool's description."
        )

    async def delegate(task: str, config: RunnableConfig = None) -> str:
        parent_thread = (config or {}).get("configurable", {}).get("thread_id", "root")
        child_thread = f"{parent_thread}::{name}::{uuid.uuid4().hex}"
        # The subagent inherits the caller's principal (SPEC §5.6). It travels in
        # runtime context, not message state; a parent could pass a narrowed
        # principal here, but delegation can never widen access.
        result = await subgraph.ainvoke(
            {"messages": [HumanMessage(content=task)]},
            config={
                "configurable": {"thread_id": child_thread},
                "recursion_limit": recursion_limit,
            },
            context=LeveContext(principal=current_principal()),
        )
        # If the subagent paused on an interrupt (e.g. an approval gate), the run
        # did not complete. Returning the last message would be misleading, and
        # resuming a nested subagent is not supported in v1 (subagent progress
        # streaming is deferred — SPEC §4.5). Report the pause honestly instead.
        if result.get("__interrupt__"):
            return (
                f"The '{name}' subagent paused awaiting human input and could not "
                "complete the delegated task (nested approvals are not supported "
                "in this version). Handle this directly instead of delegating."
            )
        messages = result.get("messages", [])
        return messages[-1].content if messages else ""

    return StructuredTool.from_function(
        coroutine=delegate,
        name=f"delegate_to_{name}",
        description=description,
        args_schema=DelegateInput,
    )
