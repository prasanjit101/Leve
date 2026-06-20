"""Tests for channel adapters and discovery."""

from __future__ import annotations

import hashlib
import hmac
import time

from leve.channels.slack import slack_adapter
from leve.loader import load_project

AGENT_PY = """\
from leve import define_agent
from leve.testing import FakeChatModel
agent = define_agent(model=FakeChatModel(responses=["hi"]))
"""


def _slack_sig(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_slack_verify_accepts_valid_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    adapter = slack_adapter(signing_secret_env="SLACK_SIGNING_SECRET")
    body = b'{"hello": 1}'
    ts = str(int(time.time()))
    headers = {"x-slack-request-timestamp": ts, "x-slack-signature": _slack_sig("shhh", ts, body)}
    assert adapter.verify(headers, body)


def test_slack_verify_rejects_tampered_and_stale(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    adapter = slack_adapter(signing_secret_env="SLACK_SIGNING_SECRET")
    body = b"{}"
    ts = str(int(time.time()))
    assert not adapter.verify({"x-slack-request-timestamp": ts, "x-slack-signature": "v0=bad"}, body)
    old = str(int(time.time()) - 10_000)
    assert not adapter.verify(
        {"x-slack-request-timestamp": old, "x-slack-signature": _slack_sig("shhh", old, body)}, body
    )


def test_slack_handshake_and_parse():
    adapter = slack_adapter(signing_secret_env="X")
    assert adapter.handshake_response({"type": "url_verification", "challenge": "abc"}) == {"challenge": "abc"}
    assert adapter.handshake_response({"type": "event_callback"}) is None

    msg = adapter.parse({"event": {"type": "message", "text": "hi", "channel": "C1", "ts": "12.3"}})
    assert msg.text == "hi" and msg.session_key == "slack:C1:12.3"
    # The bot's own messages are ignored to avoid loops.
    assert adapter.parse({"event": {"type": "message", "text": "x", "bot_id": "B1"}}) is None


async def test_channel_turn_runs_and_delivers(make_loaded):
    from leve.channels import IncomingMessage
    from leve.server import SessionManager
    from leve.testing import FakeChatModel
    from tests.conftest import runtime_for

    delivered: dict = {}

    class FakeAdapter:
        async def deliver(self, target, text):
            delivered["target"], delivered["text"] = target, text

    loaded = make_loaded(FakeChatModel(responses=["channel reply"]))
    async with runtime_for(loaded) as rt:
        manager = SessionManager(rt)
        incoming = IncomingMessage(session_key="slack:C:1", text="hi", target={"channel": "C"})
        await manager.run_channel_turn(FakeAdapter(), incoming)

    assert delivered == {"target": {"channel": "C"}, "text": "channel reply"}


def test_schedule_secret_enforced(monkeypatch):
    import pytest
    from fastapi import HTTPException

    from leve.server import _verify_schedule_secret

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    monkeypatch.delenv("LEVE_SCHEDULE_SECRET", raising=False)
    _verify_schedule_secret(_Req({}))  # no secret configured → open (dev)

    monkeypatch.setenv("LEVE_SCHEDULE_SECRET", "s3cret")
    with pytest.raises(HTTPException):
        _verify_schedule_secret(_Req({}))
    _verify_schedule_secret(_Req({"x-leve-schedule-secret": "s3cret"}))  # matches → ok


def test_channel_discovery(tmp_path, write_project):
    channel = """\
        from leve.channels import define_channel
        from leve.channels.slack import slack_adapter
        channel = define_channel(slack_adapter(signing_secret_env="SLACK_SIGNING_SECRET"))
    """
    config = write_project(
        tmp_path, agent_py=AGENT_PY, extra_files={"agent/channels/slack.py": channel}
    )
    loaded = load_project(config)
    assert [c.name for c in loaded.channels] == ["slack"]
