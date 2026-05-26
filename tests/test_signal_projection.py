"""
Regression tests for _project_signal_to_opportunities in the blender.

Both S1 (drug enforcement cascade) and S2 (rival co-mobilization) project a
single signal event onto each member account's top_state_exposures rather
than only state_footprint[0]. The earlier implementation clustered every
alert in the first state of the placeholder top-10 footprint, which was
why every alert landed in CA or NY regardless of where the signal was
geographically material.

These tests read live data/account_profiles.json (the projection function
hard-loads it). They verify behavior end-to-end with real data rather
than mocking the file system.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts._lib.blender import _project_signal_to_opportunities, load_accounts


def _accounts() -> list[dict]:
    return load_accounts()


def _profiles() -> dict:
    return json.loads(Path("data/account_profiles.json").read_text()).get("profiles", {})


def test_s2_projects_onto_top_state_exposures():
    """An S2 event with one member account should produce one opportunity
    per state in that account's top_state_exposures (not just one CA event)."""
    accounts = _accounts()
    profiles = _profiles()
    hims_profile = profiles.get("acc_001", {})
    hims_top_states = hims_profile.get("top_state_exposures") or []
    assert hims_top_states, "Hims profile must have non-empty top_state_exposures for this test"

    event = {
        "topic": "compounded_glp1",
        "score": 0.85,
        "member_accounts": ["acc_001"],  # Hims & Hers only
        "competitor_set_label": "test set",
    }
    projections = _project_signal_to_opportunities(
        "S2_rival_co_mobilization", event, accounts
    )
    states_projected = {p[1] for p in projections if p[0] == "acc_001"}
    assert states_projected == set(hims_top_states), (
        f"S2 projection for acc_001 should equal top_state_exposures "
        f"({hims_top_states}); got {sorted(states_projected)}"
    )


def test_s2_projects_per_member_account():
    """A multi-member S2 event should produce per-account-per-state projections."""
    accounts = _accounts()
    event = {
        "topic": "compounded_glp1",
        "score": 0.85,
        "member_accounts": ["acc_001", "acc_002"],  # Hims + Ro
        "competitor_set_label": "GLP-1 prescribers",
    }
    projections = _project_signal_to_opportunities(
        "S2_rival_co_mobilization", event, accounts
    )
    acc_ids = {p[0] for p in projections}
    assert "acc_001" in acc_ids
    assert "acc_002" in acc_ids


def test_s2_falls_back_to_state_footprint_when_top_states_absent():
    """Account missing top_state_exposures should fall back to state_footprint[0]."""
    accounts = [{
        "id": "acc_test",
        "name": "Test Account",
        "state_footprint": ["wa"],
        "size_tier": "midmarket",
    }]
    event = {
        "topic": "telehealth_general",
        "score": 0.85,
        "member_accounts": ["acc_test"],
    }
    projections = _project_signal_to_opportunities(
        "S2_rival_co_mobilization", event, accounts
    )
    # No profile for acc_test in account_profiles.json → top_states empty →
    # falls back to state_footprint[0] = "wa".
    states = {p[1] for p in projections}
    assert states == {"wa"}


def test_s1_projects_onto_top_state_exposures():
    """S1 drug enforcement event should project onto top_state_exposures for
    matching accounts (the bug fix that broke the CA-only clustering)."""
    accounts = _accounts()
    profiles = _profiles()

    hims_profile = profiles.get("acc_001", {})
    hims_top_states = set(hims_profile.get("top_state_exposures") or [])
    assert hims_top_states, "Hims profile must have non-empty top_state_exposures"

    event = {
        "drug_category": "glp1",
        "score": 0.85,
        "spike_z_score": 3.0,
        "spike_current_count": 8,
    }
    projections = _project_signal_to_opportunities(
        "S1_drug_enforcement_cascade", event, accounts
    )
    hims_states = {p[1] for p in projections if p[0] == "acc_001"}
    assert hims_states == hims_top_states, (
        f"S1 glp1 projection for Hims should equal top_state_exposures "
        f"({hims_top_states}); got {sorted(hims_states)}"
    )


def test_s3_remains_state_keyed():
    """S3 enforcement precursor stays keyed to its detected_state, not multi-
    projected. The geographic specificity of an enforcement action is the
    point."""
    accounts = _accounts()
    event = {
        "detected_state": "california",
        "detected_topic": "compounded_glp1",
        "score": 0.6,
        "matched_legislative": [],
    }
    projections = _project_signal_to_opportunities(
        "S3_enforcement_precursor", event, accounts
    )
    states = {p[1] for p in projections}
    assert states <= {"ca"}, (
        f"S3 projections should be CA-only (detected_state=california); got {states}"
    )
