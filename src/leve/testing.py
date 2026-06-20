"""Test helpers — deterministic models for tests and evals.

A real provider call is non-deterministic and needs credentials, which is wrong
for unit tests and for the eval harness (SPEC §7) that runs an agent in-process.
:class:`FakeChatModel` replays scripted responses and accepts ``bind_tools`` so
it can drive the full tool-calling loop without a provider. It is part of the
public API precisely so users can test *their* agents the same way.
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr


class FakeChatModel(BaseChatModel):
    """Replays a fixed list of responses, one per model call.

    Responses may be plain strings (final answers) or ``AIMessage`` objects
    (use these to script ``tool_calls``). Once the list is exhausted the last
    response repeats, so an agent that loops one extra time degrades gracefully
    instead of raising.
    """

    responses: list[Any]
    _index: int = PrivateAttr(default=0)
    _calls: list = PrivateAttr(default_factory=list)

    @property
    def calls(self) -> list:
        """The message lists seen on each call (for asserting the prompt sent)."""

        return self._calls

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeChatModel":
        # Tool binding is a no-op: scripted responses already encode tool calls.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if not self.responses:
            raise ValueError("FakeChatModel was given no responses.")
        self._calls.append(list(messages))
        raw = self.responses[min(self._index, len(self.responses) - 1)]
        self._index += 1
        message = AIMessage(content=raw) if isinstance(raw, str) else raw
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "leve-fake"


__all__ = ["FakeChatModel"]
