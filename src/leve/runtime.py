"""Per-run runtime context.

``LeveContext`` travels in LangGraph's runtime context (``config.configurable`` /
the typed ``Runtime.context``) — deliberately **not** in the ``messages`` state
the model reads or writes. In M1 it carries non-sensitive template variables and
run metadata; in M5 it gains the caller ``Principal`` (SPEC §5.6), which lives
here precisely so the model can neither read nor forge identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LeveContext:
    """Runtime context threaded into every graph invocation.

    Attributes:
        template_vars: Non-sensitive values available to ``instructions.md``
            templating (e.g. ``channel_name``).
        metadata: Free-form run metadata (channel id, source, …) for tracing.
    """

    template_vars: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
