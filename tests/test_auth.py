"""Tests for the Principal, credential brokers, and tool/principal injection."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from leve.auth import (
    InjectedPrincipal,
    Principal,
    anonymous,
    app_principal,
    with_broker,
)
from leve.credentials import (
    NeedsConsent,
    OAuthStoreBroker,
    StaticBroker,
    TokenExchangeBroker,
)
from leve.tools import define_tool
from tests.conftest import collect, runtime_for


# --- Principal + brokers ---------------------------------------------------


async def test_static_broker(monkeypatch):
    monkeypatch.setenv("LEVE_CRED_WAREHOUSE", "secret-token")
    principal = Principal(subject="u1", broker=StaticBroker())
    cred = await principal.credential("warehouse")
    assert cred.token == "secret-token"


async def test_credential_without_broker_denied():
    with pytest.raises(PermissionError):
        await Principal(subject="u1").credential("warehouse")


def test_narrow_cannot_add_new_key():
    principal = Principal(subject="u1", claims={"roles": ["reader"]})
    narrowed = principal.narrow(claims={"roles": ["reader"], "admin": True})
    assert "admin" not in narrowed.claims  # cannot introduce a new claim
    assert narrowed.claims == {"roles": ["reader"]}


def test_narrow_cannot_widen_existing_value():
    principal = Principal(subject="u1", claims={"roles": ["reader"]})
    # Requesting a broader value must NOT grant it — values can only shrink.
    narrowed = principal.narrow(claims={"roles": ["reader", "admin", "superuser"]})
    assert narrowed.claims["roles"] == ["reader"]


def test_narrow_cannot_replace_scalar():
    principal = Principal(subject="u1", claims={"tier": "basic"})
    assert principal.narrow(claims={"tier": "enterprise"}).claims == {}  # mismatch dropped


async def test_oauth_store_consent_then_resolve():
    broker = OAuthStoreBroker()
    principal = Principal(subject="u1", tenant="acme", broker=broker)
    with pytest.raises(NeedsConsent) as exc:
        await principal.credential("linear")
    assert exc.value.provider == "linear"

    broker.put(principal, "linear", "granted-token")
    cred = await principal.credential("linear")
    assert cred.token == "granted-token"


async def test_token_exchange_reads_private_secrets():
    # Secret tokens live in the private `secrets` field, never in `claims`.
    principal = Principal(subject="u1", secrets={"audience_tokens": {"warehouse": "wh-tok"}},
                          broker=TokenExchangeBroker())
    assert (await principal.credential("warehouse")).token == "wh-tok"
    assert "wh-tok" not in repr(principal)  # not exposed via repr/tracing


async def test_static_broker_missing_secret_raises(monkeypatch):
    monkeypatch.delenv("LEVE_CRED_WAREHOUSE", raising=False)
    with pytest.raises(PermissionError):
        await Principal(subject="u1", broker=StaticBroker()).credential("warehouse")


def test_injected_param_in_explicit_schema_raises():
    from pydantic import Field

    class Bad(BaseModel):
        sql: str
        principal: str = Field(default="")  # collides with the injected name

    @define_tool(description="x", input_schema=Bad)
    async def run(sql: str, principal: Principal = InjectedPrincipal()) -> str:
        return sql

    with pytest.raises(ValueError, match="injected"):
        run.build()


def test_app_principal_is_distinct():
    app = app_principal("scheduler", scopes=("read",))
    assert app.claims["app"] is True and app.subject == "scheduler"


def test_with_broker_is_noop_when_already_set():
    b1, b2 = StaticBroker(), StaticBroker()
    p = Principal(subject="u", broker=b1)
    assert with_broker(p, b2).broker is b1


# --- Injected-principal tools ----------------------------------------------


class _Empty(BaseModel):
    pass


def _whoami_tool():
    @define_tool(description="Return the caller's id.", input_schema=_Empty)
    async def whoami(principal: Principal = InjectedPrincipal()) -> str:
        return principal.subject if principal else "none"

    return whoami


def test_injected_principal_stripped_from_schema():
    tool = _whoami_tool().build()
    # The model-facing schema must not expose the principal argument.
    assert "principal" not in tool.args_schema.model_json_schema().get("properties", {})


async def test_tool_receives_injected_principal(make_loaded):
    from leve.runtime import LeveContext
    from leve.testing import FakeChatModel

    model = FakeChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "whoami", "args": {}, "id": "c1"}]),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(_whoami_tool(),))
    async with runtime_for(loaded) as rt:
        events = await collect(
            rt.run(rt.new_session_id(), "who am i", context=LeveContext(principal=Principal(subject="alice")))
        )
    results = [e for e in events if e["type"] == "tool.result"]
    assert results and results[0]["output"] == "alice"  # filled from context, not the model


async def test_consent_interrupt_then_resume(make_loaded):
    from leve.runtime import LeveContext
    from leve.testing import FakeChatModel

    broker = OAuthStoreBroker()

    @define_tool(description="Create a Linear issue.", input_schema=_Empty)
    async def create_issue(principal: Principal = InjectedPrincipal()) -> str:
        cred = await principal.credential("linear")
        return f"created:{cred.token}"

    model = FakeChatModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "create_issue", "args": {}, "id": "c1"}]),
            "done",
        ]
    )
    loaded = make_loaded(model, tools=(create_issue,))
    principal = Principal(subject="u1", tenant="acme", broker=broker)
    ctx = LeveContext(principal=principal)

    async with runtime_for(loaded) as rt:
        sid = rt.new_session_id()
        events = await collect(rt.run(sid, "make issue", context=ctx))
        approvals = [e for e in events if e["type"] == "approval.requested"]
        assert approvals and approvals[0]["interrupt"]["value"]["type"] == "consent"
        assert events[-1]["interrupted"] is True

        broker.put(principal, "linear", "tok123")  # user authorizes
        resumed = await collect(rt.resume(sid, {"approved": True}, context=ctx))
        results = [e for e in resumed if e["type"] == "tool.result"]
        assert results and results[0]["output"] == "created:tok123"


async def test_subagent_inherits_parent_principal(make_loaded):
    from pathlib import Path

    from leve.agent import define_agent
    from leve.loader import LoadedAgent
    from leve.runtime import LeveContext
    from leve.testing import FakeChatModel

    seen: list = []

    @define_tool(description="Record the caller.", input_schema=_Empty)
    async def capture(principal: Principal = InjectedPrincipal()) -> str:
        seen.append(principal.subject if principal else None)
        return "ok"

    sub = LoadedAgent(
        name="helper",
        path=Path("."),
        spec=define_agent(
            model=FakeChatModel(responses=[
                AIMessage(content="", tool_calls=[{"name": "capture", "args": {}, "id": "s1"}]),
                "sub done",
            ]),
            description="Helps.",
        ),
        tools=(capture,),
    )
    parent = LoadedAgent(
        name="root",
        path=Path("."),
        spec=define_agent(model=FakeChatModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "delegate_to_helper", "args": {"task": "who"}, "id": "c1"}]),
            "parent done",
        ])),
        subagents=(sub,),
    )

    async with runtime_for(parent) as rt:
        await collect(rt.run(rt.new_session_id(), "go", context=LeveContext(principal=Principal(subject="bob"))))

    assert seen == ["bob"]  # the subagent's tool received the parent's principal
