"""Leve exception hierarchy.

A single base (``LeveError``) lets callers catch every framework-raised error,
while the specific subclasses give precise failure modes for the loader, config,
and runtime layers.
"""

from __future__ import annotations


class LeveError(Exception):
    """Base class for all errors raised by Leve."""


class ConfigError(LeveError):
    """Raised when ``leve.toml`` (or an env override) is malformed or invalid."""


class LoaderError(LeveError):
    """Raised when the agent directory cannot be discovered or compiled."""


class SessionError(LeveError):
    """Raised for invalid session operations (unknown id, bad resume, etc.)."""
