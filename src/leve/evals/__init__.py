"""Evals — ``evals/<name>.eval.py`` (SPEC §7).

An eval is a scored test that drives the project's agent in-process and asserts
on the normalized event stream. ``define_eval`` marks an async function as an
eval; the loader collects them and ``run_evals`` executes each against a fresh
runtime (so model state and threads don't leak between evals). Assertions raise
``AssertionError`` on failure; a failing suite is the CI deploy gate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from leve.evals.expect import Matcher
from leve.serving.session import AgentRuntime, extract_reply

__all__ = ["EvalContext", "EvalSpec", "EvalResult", "define_eval", "run_eval"]


@dataclass(frozen=True)
class EvalSpec:
    """A described eval: name, description, and the async test body."""

    name: str
    description: str
    func: Callable[[EvalContext], Awaitable[None]]


@dataclass(frozen=True)
class EvalResult:
    """The outcome of running one eval."""

    name: str
    passed: bool
    error: str | None = None


def define_eval(*, description: str = "", name: str | None = None):
    """Mark an async function as an eval (SPEC §7)."""

    def wrap(func: Callable[[EvalContext], Awaitable[None]]) -> EvalSpec:
        return EvalSpec(name=name or func.__name__, description=description, func=func)

    return wrap


class EvalContext:
    """The ``t`` handle passed to an eval: drive the agent and assert on it."""

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        self._session_id = runtime.new_session_id()
        self._turn: list[dict[str, Any]] = []

    async def send(self, message: str) -> EvalContext:
        """Send a message and capture the resulting turn's events."""

        self._turn = [
            event async for event in self._runtime.run(self._session_id, message)
        ]
        return self

    @property
    def reply(self) -> str:
        """The assistant's final text reply for the last turn."""

        return extract_reply(self._turn)

    def completed(self) -> None:
        """Assert the last turn finished without interrupting or erroring."""

        ends = [e for e in self._turn if e["type"] == "turn.end"]
        assert ends, "no turn.end event"
        assert not ends[-1]["interrupted"], "turn paused on an interrupt"
        assert not ends[-1]["errored"], "turn ended with an error"

    def called_tool(self, name: str) -> None:
        """Assert a tool named ``name`` was called in the last turn."""

        assert any(
            e["type"] == "tool.call" and e["tool"] == name for e in self._turn
        ), f"tool '{name}' was not called"

    def loaded_skill(self, name: str) -> None:
        """Assert the skill ``name`` was loaded via the load_skill tool."""

        assert any(
            e["type"] == "tool.call"
            and e["tool"] == "load_skill"
            and (e.get("input") or {}).get("name") == name
            for e in self._turn
        ), f"skill '{name}' was not loaded"

    def no_errors(self) -> None:
        """Assert no error events occurred in the last turn."""

        assert not any(e["type"] == "error" for e in self._turn), (
            "turn produced an error"
        )

    def check(self, value: str, matcher: Matcher) -> None:
        """Assert ``matcher(value)`` holds."""

        assert matcher(value), f"check failed for value: {value!r}"


async def run_eval(spec: EvalSpec, runtime: AgentRuntime) -> EvalResult:
    """Run one eval against a runtime, capturing pass/fail."""

    try:
        await spec.func(EvalContext(runtime))
    except AssertionError as exc:
        return EvalResult(
            name=spec.name, passed=False, error=str(exc) or "assertion failed"
        )
    except Exception as exc:  # an unexpected error is also a failure
        return EvalResult(
            name=spec.name, passed=False, error=f"{type(exc).__name__}: {exc}"
        )
    return EvalResult(name=spec.name, passed=True)
