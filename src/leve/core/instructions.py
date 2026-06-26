"""Instructions — ``agent/instructions.md`` (SPEC §4.2).

Plain markdown that becomes the system prompt prepended to every model call.
Lightweight ``{{ ... }}`` templating resolves **non-sensitive** runtime values
(current date, channel name, …) from the run's context. The caller's identity is
deliberately *never* exposed here — it lives in runtime context, not the prompt
(SPEC §5.6).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from langchain.agents.middleware import AgentMiddleware, dynamic_prompt

from leve.core.runtime import LeveContext

# Matches ``{{ name }}`` / ``{{name}}`` with dotted keys allowed.
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def render_instructions(text: str, variables: dict[str, Any]) -> str:
    """Substitute ``{{ key }}`` placeholders from ``variables``.

    Unknown placeholders are left untouched rather than raising, so a missing
    optional value (e.g. no channel in a CLI run) never breaks a prompt.
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)  # leave the original placeholder in place

    return _PLACEHOLDER.sub(_replace, text)


def _default_variables() -> dict[str, Any]:
    """Runtime values Leve always provides to templating."""

    return {"current_date": date.today().isoformat()}


def make_prompt_middleware(text: str) -> AgentMiddleware:
    """Build a ``dynamic_prompt`` middleware that renders instructions per call.

    Templating is resolved on every model request (so ``current_date`` and
    context-supplied values are always fresh) and returned as the system prompt.
    """

    def prompt(request: Any) -> str:
        variables = _default_variables()
        context = getattr(getattr(request, "runtime", None), "context", None)
        if isinstance(context, LeveContext):
            variables.update(context.template_vars)
        return render_instructions(text, variables).strip()

    return dynamic_prompt(prompt)
