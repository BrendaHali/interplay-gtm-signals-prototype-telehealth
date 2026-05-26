"""
Tests for LDA client co-mobilization detection (telehealth vertical).
External HTTP is not exercised; the fetch functions are tested
in integration via scripts/verify_sources.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

from scripts._lib.lda_client import detect_co_mobilization, extract_topics


def _filing(uuid: str, days_ago: int, activities: list[dict]) -> dict:
    dt = datetime.now(timezone.utc)
    posted = dt.replace(day=max(1, dt.day - min(days_ago, 27))).isoformat()
    return {
        "filing_uuid": uuid,
        "dt_posted": posted,
        "lobbying_activities": activities,
    }


def test_extract_topics_pulls_general_issue_code_and_description():
    filing = {
        "lobbying_activities": [
            {"general_issue_code_display": "Health Issues", "description": "Telehealth prescribing of compounded GLP-1"},
            {"general_issue_code_display": "Pharmacy", "description": "compounding rules"},
        ],
    }
    topics = extract_topics(filing)
    assert "health issues" in topics
    assert "pharmacy" in topics
    assert any("telehealth" in t for t in topics)


def test_detect_co_mobilization_fires_when_two_competitors_share_topic():
    filings_by_account = {
        "acc_001": [_filing("u1", 10, [{"general_issue_code_display": "Health Issues"}])],
        "acc_002": [_filing("u2", 20, [{"general_issue_code_display": "Health Issues"}])],
    }
    pairs = {
        "pairs": [
            {"set_id": "telehealth_cohort", "label": "Telehealth prescribers", "members": ["acc_001", "acc_002"]},
        ],
    }
    events = detect_co_mobilization(filings_by_account, pairs, window_days=90)
    assert any(e["topic"] == "telehealth_general" for e in events)


def test_detect_co_mobilization_filters_out_solo_topics():
    filings_by_account = {
        "acc_001": [_filing("u1", 10, [{"general_issue_code_display": "Health Issues"}])],
        "acc_002": [_filing("u2", 20, [{"general_issue_code_display": "Pharmacy"}])],
    }
    pairs = {
        "pairs": [
            {"set_id": "telehealth_cohort", "label": "Telehealth prescribers", "members": ["acc_001", "acc_002"]},
        ],
    }
    events = detect_co_mobilization(filings_by_account, pairs, window_days=90)
    assert events == []


def test_detect_co_mobilization_score_scales_with_member_count():
    filings_by_account = {
        "acc_001": [_filing("u1", 5, [{"general_issue_code_display": "Health Issues"}])],
        "acc_002": [_filing("u2", 5, [{"general_issue_code_display": "Health Issues"}])],
        "acc_003": [_filing("u3", 5, [{"general_issue_code_display": "Health Issues"}])],
    }
    pairs = {
        "pairs": [
            {"set_id": "telehealth_cohort", "label": "Telehealth prescribers", "members": ["acc_001", "acc_002", "acc_003", "acc_004"]},
        ],
    }
    events = detect_co_mobilization(filings_by_account, pairs, window_days=90)
    assert len(events) >= 1
    # Threshold-style scoring: 2 fires = 0.85, 3 = 0.90, 4+ = 1.0
    # Three of four members fired so score is 0.90
    score = next(e["score"] for e in events if e["topic"] == "telehealth_general")
    assert 0.85 <= score <= 0.95


def test_detect_co_mobilization_single_member_does_not_fire():
    filings_by_account = {
        "acc_001": [_filing("u1", 5, [{"general_issue_code_display": "Health Issues"}])],
    }
    pairs = {
        "pairs": [
            {"set_id": "telehealth_cohort", "label": "Telehealth prescribers", "members": ["acc_001", "acc_002"]},
        ],
    }
    events = detect_co_mobilization(filings_by_account, pairs, window_days=90)
    # Single competitor firing is not co-mobilization; no event should produce
    assert events == []
