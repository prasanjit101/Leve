"""Matchers for eval assertions (SPEC §7).

A matcher is a simple ``Callable[[str], bool]`` used with ``t.check(value,
matcher)``. Keeping matchers as plain predicates means model-graded scorers
(LangSmith evaluators) can be dropped in as just another callable later.
"""

from __future__ import annotations

import re
from collections.abc import Callable

Matcher = Callable[[str], bool]


def includes(substring: str) -> Matcher:
    """Match when the value contains ``substring``."""

    return lambda value: substring in (value or "")


def excludes(substring: str) -> Matcher:
    """Match when the value does NOT contain ``substring``."""

    return lambda value: substring not in (value or "")


def equals(expected: str) -> Matcher:
    """Match when the value equals ``expected`` exactly."""

    return lambda value: value == expected


def matches(pattern: str) -> Matcher:
    """Match when the value matches the regular expression ``pattern``."""

    compiled = re.compile(pattern)
    return lambda value: compiled.search(value or "") is not None
