"""
Tests for the buyer-brand-name post-processing scrub filter and the voice
scrubber.

The LLM is told in the system prompt to avoid naming the buyer or its
product. The brand scrub is the belt-and-suspenders backstop: if the LLM
still organically writes the buyer's brand name (e.g., as a generic
corporate-role title), the scrub rewrites it to 'government affairs' so
no alert payload ever leaks the term.

The voice scrubber enforces project voice rules: no em dashes / en dashes
and no banned filler words.

Test inputs assemble the brand name from concatenated string parts so the
buyer reading this test file does not see the brand name repeated.
"""
from __future__ import annotations

from scripts._lib.interpreter import _scrub_buyer_brand, _scrub_voice


# Brand parts are kept separate so the literal full brand string never
# appears in this source file. The scrubber still catches every joined
# form when these are concatenated at runtime.
_PART_A = "state"
_PART_B = "affairs"
_LOWER = _PART_A + " " + _PART_B
_CAPS = _PART_A.capitalize() + " " + _PART_B.capitalize()
_HYPHEN = _PART_A + "-" + _PART_B
_UNDER = _PART_A + "_" + _PART_B
_CONCAT = _PART_A + _PART_B
_UPPER = (_PART_A + " " + _PART_B).upper()


def test_scrub_rewrites_lowercase_lead_phrasing():
    payload = {
        "headline": "Hims: California GLP-1 Compounding Mobilization",
        "body": f"Reopen with Hims's {_LOWER} lead using the trigger event.",
    }
    out = _scrub_buyer_brand(payload)
    assert _LOWER not in out["body"].lower()
    assert "government affairs lead" in out["body"]


def test_scrub_rewrites_capitalized_phrasing():
    payload = {
        "body": f"Engage {_CAPS} head this week.",
    }
    out = _scrub_buyer_brand(payload)
    assert _CAPS not in out["body"]
    assert "government affairs" in out["body"]


def test_scrub_rewrites_hyphenated_variant():
    payload = {
        "body": f"Talk to the {_HYPHEN} team about the licensing exposure.",
    }
    out = _scrub_buyer_brand(payload)
    assert _HYPHEN not in out["body"].lower()
    assert "government affairs team" in out["body"]


def test_scrub_leaves_clean_payloads_unchanged():
    payload = {
        "headline": "Hims: California GLP-1 Cascade Triggers Outreach",
        "body": "Engage the government affairs team this week.",
        "cold_first_touch_frame": "Worth a 20-minute call.",
        "worked_deal_revival_frame": "Reopen the conversation now.",
    }
    out = _scrub_buyer_brand(payload)
    assert out == payload


def test_scrub_preserves_non_string_values():
    payload = {
        "headline": "test",
        "body": f"{_LOWER} mention",
        "_source": "llm",
        "score": 0.95,
    }
    out = _scrub_buyer_brand(payload)
    assert out["_source"] == "llm"
    assert out["score"] == 0.95
    assert _LOWER not in out["body"].lower()


def test_scrub_handles_multiple_occurrences():
    payload = {
        "body": f"The {_LOWER} lead and the {_LOWER} team both need this.",
    }
    out = _scrub_buyer_brand(payload)
    assert _LOWER not in out["body"].lower()
    # Should produce "government affairs lead" + "government affairs team"
    assert out["body"].count("government affairs") == 2


def test_scrubber_catches_concatenated_form():
    """The regex must catch all five surface forms of the brand name.

    Inputs are assembled from parts so the test source file never contains
    the literal full brand string.
    """
    variants = [_CAPS, _HYPHEN, _UNDER, _CONCAT, _UPPER]
    for variant in variants:
        payload = {"body": f"Contact {variant} for follow-up."}
        out = _scrub_buyer_brand(payload)
        assert variant.lower() not in out["body"].lower(), (
            f"variant {variant!r} not scrubbed: {out['body']!r}"
        )
        assert "government affairs" in out["body"].lower(), (
            f"variant {variant!r} did not rewrite to 'government affairs': "
            f"{out['body']!r}"
        )


def test_voice_scrub_em_dash():
    assert _scrub_voice("Hello—world") == "Hello, world"


def test_voice_scrub_banned_words():
    out = _scrub_voice("I actually think we should leverage this")
    assert "actually" not in out.lower()
    assert "leverage" not in out.lower()
    # No double spaces left behind after word removal.
    assert "  " not in out
