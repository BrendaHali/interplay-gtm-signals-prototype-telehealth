"""
Tests for the composite blender (telehealth ICP).
"""
from __future__ import annotations

import pytest

from scripts._lib.blender import _normalize_topic, apply_per_ae_caps


def test_normalize_topic_maps_telehealth_synonyms():
    assert _normalize_topic("compounded GLP-1 sourcing") == "compounded_glp1"
    assert _normalize_topic("semaglutide enforcement") == "compounded_glp1"
    assert _normalize_topic("ADHD prescribing restrictions") == "controlled_substance_telehealth"
    assert _normalize_topic("Adderall supply") == "controlled_substance_telehealth"
    assert _normalize_topic("asynchronous prescribing rule change") == "asynchronous_prescribing"
    assert _normalize_topic("Interstate Medical Licensure Compact expansion") == "telehealth_licensing"
    assert _normalize_topic("scope of practice nurse practitioner") == "scope_of_practice"
    assert _normalize_topic("mental health telehealth") == "mental_health_telehealth"
    assert _normalize_topic("Medicaid telehealth reimbursement") == "telehealth_reimbursement"
    assert _normalize_topic("") == "general"
    assert _normalize_topic("unrelated topic") == "telehealth_general"


def test_apply_per_ae_caps_keeps_top_n_per_ae():
    opps = [
        {"account_id": "a1", "account_owner_ae": "ae_x", "composite_score": 0.9},
        {"account_id": "a2", "account_owner_ae": "ae_x", "composite_score": 0.85},
        {"account_id": "a3", "account_owner_ae": "ae_x", "composite_score": 0.8},
        {"account_id": "a4", "account_owner_ae": "ae_x", "composite_score": 0.7},
        {"account_id": "a5", "account_owner_ae": "ae_y", "composite_score": 0.6},
    ]
    kept = apply_per_ae_caps(opps, cap_per_ae=3)
    assert len(kept) == 4
    ae_x_kept = [o for o in kept if o["account_owner_ae"] == "ae_x"]
    assert len(ae_x_kept) == 3
    assert all(o["composite_score"] >= 0.8 for o in ae_x_kept)


def test_apply_per_ae_caps_handles_unowned_accounts():
    opps = [
        {"account_id": "a1", "composite_score": 0.9},
        {"account_id": "a2", "composite_score": 0.8},
    ]
    kept = apply_per_ae_caps(opps, cap_per_ae=1)
    assert len(kept) == 1
