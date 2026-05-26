"""
Tests for the LLM JSON payload extractor in the interpreter.

The LLM occasionally wraps its JSON output in prose ("Here is the alert:")
or in ```json fences. The extractor must tolerate both. These cases
previously caused template_fallback events.
"""
from __future__ import annotations

from scripts._lib.interpreter import _extract_json_payload


def test_extract_json_payload_clean_json():
    text = '{"headline": "test", "body": "x"}'
    out = _extract_json_payload(text)
    assert out == {"headline": "test", "body": "x"}


def test_extract_json_payload_with_prose_before():
    text = 'Here is the alert payload:\n{"headline": "test", "body": "x"}'
    out = _extract_json_payload(text)
    assert out == {"headline": "test", "body": "x"}


def test_extract_json_payload_with_prose_after():
    text = '{"headline": "test", "body": "x"}\n\nHope this helps.'
    out = _extract_json_payload(text)
    assert out == {"headline": "test", "body": "x"}


def test_extract_json_payload_with_fenced_block():
    text = '```json\n{"headline": "test", "body": "x"}\n```'
    out = _extract_json_payload(text)
    assert out == {"headline": "test", "body": "x"}


def test_extract_json_payload_with_unfenced_block():
    text = '```\n{"headline": "test", "body": "x"}\n```'
    out = _extract_json_payload(text)
    assert out == {"headline": "test", "body": "x"}


def test_extract_json_payload_empty_input():
    assert _extract_json_payload("") is None
    assert _extract_json_payload("   ") is None


def test_extract_json_payload_malformed_json():
    # Unmatched braces, trailing commas, etc.
    assert _extract_json_payload("{not valid json") is None
    # Trailing comma is technically invalid JSON
    assert _extract_json_payload('{"a": 1, "b": 2,}') is None


def test_extract_json_payload_no_braces():
    text = "Just a plain sentence with no JSON object at all."
    assert _extract_json_payload(text) is None
