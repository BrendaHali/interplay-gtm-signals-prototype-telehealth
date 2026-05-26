"""
Tests for the S3 Enforcement Precursor detector.

Covers: state extraction (single + multi-state), topic classification, and
the _state_abbr canonical conversion that the blender now imports from this
module rather than carrying a truncated duplicate.
"""
from __future__ import annotations

from scripts._lib.signal_enforcement_precursor import (
    extract_state,
    extract_states,
    classify_topic,
    _state_abbr,
)


def test_extract_states_single_state():
    text = "California AG sues telehealth provider over async prescribing"
    assert extract_states(text) == ["california"]
    assert extract_state(text) == "california"


def test_extract_states_multi_state_returns_all():
    text = (
        "Attorneys general of California, Texas, and New York announced a coordinated "
        "settlement with the compounding pharmacy"
    )
    states = extract_states(text)
    assert "california" in states
    assert "texas" in states
    assert "new york" in states
    # Backwards-compat: single-state API returns the first match instead of
    # silently discarding multi-state coordinated AG actions.
    assert extract_state(text) is not None


def test_extract_states_zero_matches():
    assert extract_states("Federal enforcement action against compounding pharmacy") == []
    assert extract_state("Federal enforcement action against compounding pharmacy") is None


def test_extract_states_word_boundary_safety():
    # Should NOT match "indiana" inside other words; we use \b boundaries
    text = "Kansas City pharmacy faces FDA scrutiny"
    states = extract_states(text)
    assert "kansas" in states  # Real match
    # Should NOT match "ohio" inside random punctuation contexts; spot check
    text2 = "iohio inc files compounding registration"  # i-ohio is not Ohio
    assert "ohio" not in extract_states(text2)


def test_classify_topic_telehealth_taxonomy():
    assert classify_topic("compounded GLP-1 sterility issues") == "compounded_glp1"
    assert classify_topic("semaglutide warning letter") == "compounded_glp1"
    assert classify_topic("controlled substance Ryan Haight rules") == "controlled_substance_telehealth"
    assert classify_topic("asynchronous prescribing without in-person visit") == "asynchronous_prescribing"
    assert classify_topic("scope of practice nurse practitioner") == "scope_of_practice"
    assert classify_topic("Interstate Medical Licensure Compact") == "telehealth_licensing"
    assert classify_topic("mental health telehealth Talkspace") == "mental_health_telehealth"
    assert classify_topic("random article about lawn mowers") == "telehealth_general"


def test_state_abbr_full_50_state_coverage():
    # Verify the 14 states the truncated blender map was missing all resolve correctly.
    assert _state_abbr("hawaii") == "hi"
    assert _state_abbr("idaho") == "id"
    assert _state_abbr("iowa") == "ia"
    assert _state_abbr("kansas") == "ks"
    assert _state_abbr("maine") == "me"
    assert _state_abbr("mississippi") == "ms"
    assert _state_abbr("montana") == "mt"
    assert _state_abbr("nebraska") == "ne"
    assert _state_abbr("new hampshire") == "nh"
    assert _state_abbr("north dakota") == "nd"
    assert _state_abbr("rhode island") == "ri"
    assert _state_abbr("south dakota") == "sd"
    assert _state_abbr("vermont") == "vt"
    assert _state_abbr("wyoming") == "wy"


def test_state_abbr_does_not_misroute_new_hampshire():
    # The bug we just fixed: blender's truncated map fell back to
    # detected_state[:2] = "ne" (Nebraska's abbr) for "new hampshire".
    # With the canonical _state_abbr, it now correctly returns "nh".
    assert _state_abbr("new hampshire") == "nh"
    assert _state_abbr("nebraska") == "ne"
    assert _state_abbr("new hampshire") != _state_abbr("nebraska")


def test_state_abbr_handles_abbreviations_and_unknown():
    assert _state_abbr("ca") == "ca"
    assert _state_abbr("NY") == "ny"
    assert _state_abbr("") == ""
    assert _state_abbr("not-a-state") == "no"  # falls back to first two chars (legacy behavior, no crash)
