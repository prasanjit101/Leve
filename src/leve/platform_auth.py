"""Backward-compatible shim — canonical home is :mod:`leve.security.platform_auth`."""
from leve.security.platform_auth import *  # noqa: F401,F403
from leve.security.platform_auth import (  # noqa: F401  explicit public re-exports
    make_auth,
    store_namespace,
)
