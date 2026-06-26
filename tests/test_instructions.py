"""Tests for instructions templating."""

from __future__ import annotations

from leve.core.instructions import render_instructions


def test_substitutes_known_placeholders():
    out = render_instructions(
        "Hello {{ name }}, day {{ day }}.", {"name": "Ada", "day": 1}
    )
    assert out == "Hello Ada, day 1."


def test_leaves_unknown_placeholders_intact():
    out = render_instructions("Hi {{ missing }}.", {})
    assert out == "Hi {{ missing }}."


def test_handles_no_whitespace():
    assert render_instructions("{{x}}", {"x": "y"}) == "y"


def test_dotted_keys():
    assert render_instructions("{{ a.b }}", {"a.b": "ok"}) == "ok"
