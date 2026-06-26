"""Guards the src/leve restructure: every old top-level import path must
resolve to the SAME object as its new canonical subpackage path, so the
re-export shims stay a faithful stable API. See
docs/superpowers/specs/2026-06-26-src-leve-restructure-design.md.
"""
import importlib

import pytest

# (old module path, new canonical module path, [public symbol names])
SECURITY = [
    ("leve.auth", "leve.security.auth",
     ["Credential", "Principal", "anonymous", "with_broker", "app_principal",
      "InjectedPrincipal", "set_current_principal", "reset_current_principal",
      "current_principal"]),
    ("leve.credentials", "leve.security.credentials",
     ["NeedsConsent", "CredentialBroker", "StaticBroker", "OAuthStoreBroker",
      "TokenExchangeBroker", "create_broker"]),
    ("leve.platform_auth", "leve.security.platform_auth",
     ["store_namespace", "make_auth"]),
]
CORE = [
    ("leve.agent", "leve.core.agent",
     ["CompactionConfig", "AgentSpec", "define_agent", "TriggerClause"]),
    ("leve.graph", "leve.core.graph", ["build_graph", "ExtraToolsResolver"]),
    ("leve.models", "leve.core.models", ["build_model"]),
    ("leve.middleware", "leve.core.middleware",
     ["ApprovalMiddleware", "PrincipalMiddleware"]),
    ("leve.instructions", "leve.core.instructions",
     ["render_instructions", "make_prompt_middleware"]),
    ("leve.runtime", "leve.core.runtime", ["LeveContext"]),
    ("leve.skills", "leve.core.skills",
     ["SkillSpec", "parse_skill", "make_load_skill_tool"]),
    ("leve.subagents", "leve.core.subagents",
     ["DelegateInput", "make_delegation_tool"]),
]
SERVING = [
    ("leve.server", "leve.serving.server",
     ["SessionBroker", "SessionManager", "MessageBody", "ResumeBody",
      "create_app", "API_PREFIX"]),
    ("leve.session", "leve.serving.session", ["AgentRuntime", "extract_reply"]),
    ("leve.events", "leve.serving.events",
     ["turn_start", "turn_end", "approval_requested", "error", "EventNormalizer"]),
    ("leve.app", "leve.serving.app",
     ["build_runtime", "inspect_project", "load_evals", "run_evals"]),
    ("leve.cli", "leve.serving.cli", ["app", "channels_app", "connections_app"]),
    ("leve.tui", "leve.serving.tui", ["LeveTUI", "run_tui"]),
]


def _assert_shim(old_path, new_path, symbols):
    old_mod = importlib.import_module(old_path)
    new_mod = importlib.import_module(new_path)
    for name in symbols:
        assert hasattr(old_mod, name), f"{old_path} missing re-export {name!r}"
        assert hasattr(new_mod, name), f"{new_path} missing {name!r}"
        assert getattr(old_mod, name) is getattr(new_mod, name), (
            f"{old_path}.{name} is not the same object as {new_path}.{name}"
        )


@pytest.mark.parametrize("old,new,syms", SECURITY,
                         ids=[r[0] for r in SECURITY])
def test_security_shims(old, new, syms):
    _assert_shim(old, new, syms)


@pytest.mark.parametrize("old,new,syms", CORE, ids=[r[0] for r in CORE])
def test_core_shims(old, new, syms):
    _assert_shim(old, new, syms)


@pytest.mark.parametrize("old,new,syms", SERVING, ids=[r[0] for r in SERVING])
def test_serving_shims(old, new, syms):
    _assert_shim(old, new, syms)


def test_public_api():
    import leve
    from leve import (  # noqa: F401
        AgentSpec, CompactionConfig, Credential, Principal, define_agent,
    )
    assert leve.__version__ == "0.1.0"
    assert set(leve.__all__) == {
        "AgentSpec", "CompactionConfig", "Credential", "Principal",
        "define_agent", "__version__",
    }
