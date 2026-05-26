"""
Tests for the interpretation layer template fallback (telehealth vertical).
The LLM path is exercised in integration during full pipeline runs.
"""
from __future__ import annotations

from scripts._lib.interpreter import _template_narrative


def _sample_opp() -> dict:
    return {
        "account_id": "acc_001",
        "account_name": "Hims & Hers",
        "state": "ca",
        "topic": "compounded_glp1",
        "composite_score": 0.78,
        "signals_fired": 2,
        "signal_scores": {"S1": 0.6, "S2": 0.8, "S3": 0.0},
        "source_events": {
            "S1": [{"event_summary": "FDA spike: glp1 (z=3.1, n=12)"}],
            "S2": [{"event_summary": "LDA co-mob: GLP-1 weight-loss on 'compounded'"}],
            "S3": [],
        },
    }


def _sample_profile() -> dict:
    return {
        "ticker": "HIMS",
        "segment": "consumer_telehealth_multi_category",
        "public": True,
        "disclosure_source": "10K",
        "named_disclosed_risks": [
            "state pharmacy compounding regulations",
            "GLP-1 compounded availability",
            "state asynchronous prescribing rules",
        ],
    }


def _sample_private_profile() -> dict:
    return {
        "ticker": None,
        "segment": "consumer_telehealth_multi_category",
        "public": False,
        "disclosure_source": "industry_general",
        "named_disclosed_risks": [
            "state asynchronous prescribing",
            "compounded GLP-1 supply",
        ],
    }


def test_template_narrative_produces_all_four_keys():
    n = _template_narrative(_sample_opp(), _sample_profile())
    assert set(n.keys()) == {"headline", "body", "cold_first_touch_frame", "worked_deal_revival_frame"}


def test_template_headline_includes_account_state_topic_composite():
    n = _template_narrative(_sample_opp(), _sample_profile())
    headline = n["headline"]
    assert "Hims & Hers" in headline
    assert "CA" in headline
    assert "0.78" in headline


def test_template_body_references_named_risks():
    n = _template_narrative(_sample_opp(), _sample_profile())
    body = n["body"]
    assert "state pharmacy compounding regulations" in body or "GLP-1 compounded availability" in body


def test_template_cold_frame_proposes_conversation():
    n = _template_narrative(_sample_opp(), _sample_profile())
    cold = n["cold_first_touch_frame"]
    assert "Hims & Hers" in cold
    assert "minutes" in cold or "conversation" in cold


def test_template_worked_deal_references_prior_engagement():
    n = _template_narrative(_sample_opp(), _sample_profile())
    worked = n["worked_deal_revival_frame"]
    assert "Hims & Hers" in worked
    assert "Following" in worked or "prior" in worked.lower()
