"""
Isolated unit tests for S1 drug recall detector (signal_drug_enforcement.py).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from scripts._lib.signal_drug_enforcement import (
    compute_weekly_volumes,
    detect_spikes,
    detect,
    _recency_decay,
)

def test_compute_weekly_volumes():
    # Mock records inside drug_enforcement.json categories layout
    records_by_category = {
        "glp1": [
            {"report_date": "20260511", "recall_number": "R1"},  # Monday
            {"report_date": "20260512", "recall_number": "R2"},  # Tuesday (same week)
            {"recall_initiation_date": "20260518", "recall_number": "R3"},  # Next Monday
        ]
    }
    volumes = compute_weekly_volumes(records_by_category)
    
    # Verify that R1 and R2 are grouped into Monday 2026-05-11
    assert volumes.get(("glp1", "2026-05-11")) == 2
    # Verify that R3 is grouped into Monday 2026-05-18
    assert volumes.get(("glp1", "2026-05-18")) == 1
    # Verify other Mondays exist with 0 count (due to 365-day gap filling)
    assert ("glp1", "2026-05-04") in volumes
    assert volumes[("glp1", "2026-05-04")] == 0


def test_detect_spikes():
    # Setup a volume time-series with a stable baseline of 1 record/week
    # and a sudden spike of 15 records in the target week.
    # Note: baseline_weeks = 8, recent_windows = 4.
    # We need at least baseline_weeks + recent_windows = 12 weeks of data.
    end_dt = datetime.now(timezone.utc).date()
    # Find preceding Mondays
    mondays = []
    curr = end_dt - timedelta(days=90)
    curr = curr - timedelta(days=curr.weekday()) # alignment
    for _ in range(15):
        mondays.append(curr.isoformat())
        curr += timedelta(days=7)
    
    volumes = {}
    # baseline weeks: 1 count per week
    for m in mondays[:-1]:
        volumes[("glp1", m)] = 1
    # spike week (last week): 15 counts
    spike_week = mondays[-1]
    volumes[("glp1", spike_week)] = 15
    
    spikes = detect_spikes(
        volumes,
        baseline_weeks=8,
        z_threshold=2.0,
        monthly_volume_floor=3,
        recent_windows=4,
    )
    
    assert len(spikes) == 1
    assert spikes[0]["drug_category"] == "glp1"
    assert spikes[0]["week_start"] == spike_week
    assert spikes[0]["current_count"] == 15
    assert spikes[0]["z_score"] > 2.0


def test_detect_forward_only_matching():
    # Test forward-only matching: state bills must occur 0 to 60 days AFTER the spike.
    today = datetime.now(timezone.utc).date()
    # Align Monday for spike date (say 10 days ago)
    spike_date = today - timedelta(days=10)
    spike_date_monday = spike_date - timedelta(days=spike_date.weekday())
    
    # Define a mock FDA recall dataset that produces a spike
    # We will patch `load_records` and `load_bills`
    records_mock = {
        "glp1": [
            # A baseline of 1 per week for 10 weeks
            *[{"report_date": (spike_date_monday - timedelta(days=7 * i)).strftime("%Y%m%d"), "recall_number": f"base_{i}"} for i in range(1, 11)],
            # A spike of 15 records on the spike week
            *[{"report_date": spike_date_monday.strftime("%Y%m%d"), "recall_number": f"spike_{i}"} for i in range(15)]
        ]
    }
    
    # Mock bills:
    # 1. Pre-spike bill: latest_action_date is 5 days BEFORE the spike Monday (should NOT match)
    # 2. Post-spike bill (in window): latest_action_date is 5 days AFTER the spike Monday (should match)
    # 3. Post-spike bill (out of window): latest_action_date is 70 days AFTER the spike Monday (should NOT match)
    bills_mock = [
        {
            "identifier": "PRE_BILL",
            "title": "Compounded semaglutide restrictions",
            "jurisdiction_name": "California",
            "openstates_url": "https://openstates.org/pre",
            "latest_action_date": (spike_date_monday - timedelta(days=5)).isoformat(),
            "matched_keywords": ["compounded GLP-1"]
        },
        {
            "identifier": "MATCHED_BILL",
            "title": "Compounding GLP1 safety regulations",
            "jurisdiction_name": "Texas",
            "openstates_url": "https://openstates.org/matched",
            "latest_action_date": (spike_date_monday + timedelta(days=5)).isoformat(),
            "matched_keywords": ["compounded GLP-1"]
        },
        {
            "identifier": "LATE_BILL",
            "title": "Compounding GLP1 labeling rules",
            "jurisdiction_name": "New York",
            "openstates_url": "https://openstates.org/late",
            "latest_action_date": (spike_date_monday + timedelta(days=70)).isoformat(),
            "matched_keywords": ["compounded GLP-1"]
        }
    ]
    
    with patch("scripts._lib.signal_drug_enforcement.load_records", return_value=records_mock), \
         patch("scripts._lib.signal_drug_enforcement.load_bills", return_value=bills_mock):
        events = detect(
            spike_z_threshold=2.0,
            monthly_volume_floor=3,
            baseline_weeks=8,
            bill_match_window_days=60
        )
    
    assert len(events) == 1
    event = events[0]
    assert event["drug_category"] == "glp1"
    matched_ids = {b["identifier"] for b in event["matched_bills"]}
    assert "MATCHED_BILL" in matched_ids
    assert "PRE_BILL" not in matched_ids
    assert "LATE_BILL" not in matched_ids


def test_no_bill_match_discount():
    # Verify that a spike with no matching bills receives a 0.75x discount
    today = datetime.now(timezone.utc).date()
    spike_date_monday = today - timedelta(days=today.weekday())
    
    records_mock = {
        "glp1": [
            *[{"report_date": (spike_date_monday - timedelta(days=7 * i)).strftime("%Y%m%d"), "recall_number": f"base_{i}"} for i in range(1, 11)],
            *[{"report_date": spike_date_monday.strftime("%Y%m%d"), "recall_number": f"spike_{i}"} for i in range(15)]
        ]
    }
    
    # Empty bills list
    bills_mock = []
    
    with patch("scripts._lib.signal_drug_enforcement.load_records", return_value=records_mock), \
         patch("scripts._lib.signal_drug_enforcement.load_bills", return_value=bills_mock):
        events = detect(
            spike_z_threshold=2.0,
            monthly_volume_floor=3,
            baseline_weeks=8,
            bill_match_window_days=60
        )
    
    assert len(events) == 1
    event = events[0]
    assert event["matched_bills"] == []
    assert event["score_breakdown"]["no_bill_match_discount"] == 0.75
    # Verification of score formula
    spike_factor = min(1.0, event["spike_z_score"] / 3.0)
    recency_factor = _recency_decay(datetime.fromisoformat(event["spike_week_start"]).date(), half_life_days=21)
    expected_score = round(spike_factor * recency_factor * 0.75, 3)
    assert event["score"] == expected_score


def test_recency_decay():
    # Verify recency decay math: decay is exponential based on days elapsed
    base_date = datetime.now(timezone.utc).date()
    
    # 0 days elapsed -> factor should be exactly 1.0
    assert _recency_decay(base_date, half_life_days=21) == 1.0
    
    # 21 days elapsed (1 half life) -> factor should be exactly 0.5
    dt_21 = base_date - timedelta(days=21)
    assert _recency_decay(dt_21, half_life_days=21) == 0.5
    
    # 42 days elapsed (2 half lives) -> factor should be exactly 0.25
    dt_42 = base_date - timedelta(days=42)
    assert _recency_decay(dt_42, half_life_days=21) == 0.25
