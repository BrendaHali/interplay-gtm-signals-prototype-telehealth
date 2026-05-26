"""
Tests for the per-account aggregator.

Covers the rollup logic that converts per-(account, state, topic) alerts
into per-account records: dedupe, multistate_indicator, top_composite
selection, signal-score max aggregation, evidence_drilldown preservation,
and the empty-input edge case.
"""
from __future__ import annotations

from scripts._lib.aggregator import aggregate_alerts_to_accounts


def _alert(account_id, account_name, state, topic, composite, s1=0.0, s2=0.0, s3=0.0,
           headline="test"):
    return {
        "opportunity_id": f"{account_id}|{state}|{topic}",
        "account_id": account_id,
        "account_name": account_name,
        "account_size_tier": "enterprise",
        "account_target_tier": "enterprise",
        "state": state,
        "topic": topic,
        "composite_score": composite,
        "signal_scores": {"S1": s1, "S2": s2, "S3": s3},
        "signals_fired": sum(1 for s in (s1, s2, s3) if s > 0),
        "source_events": {
            "S1": [{"event_summary": f"S1 ev for {topic}", "score": s1}] if s1 > 0 else [],
            "S2": [{"event_summary": f"S2 ev for {topic}", "score": s2}] if s2 > 0 else [],
            "S3": [{"event_summary": f"S3 ev for {topic}", "score": s3}] if s3 > 0 else [],
        },
        "narrative": {"headline": headline, "body": f"body {state}", "cold_first_touch_frame": "", "worked_deal_revival_frame": ""},
        "narrative_source": "llm",
    }


def test_single_alert_produces_single_account_row():
    alerts = [_alert("acc_001", "Hims", "ca", "compounded_glp1", 0.6, s1=0.8, s2=0.85)]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert len(rollup) == 1
    row = rollup[0]
    assert row["account_id"] == "acc_001"
    assert row["states_affected"] == ["ca"]
    assert row["topics_affected"] == ["compounded_glp1"]
    assert row["total_routed_alerts"] == 1
    assert row["multistate_indicator"] is False
    assert row["state_count_affected"] == 1
    assert row["top_composite_score"] == 0.6


def test_multiple_alerts_same_account_collapse_to_one_row():
    """Hims firing on CA + NY + FL for the same topic should produce ONE row
    with three states, not three rows. The dashboard bug we're fixing."""
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.6, s1=0.8, s2=0.85),
        _alert("acc_001", "Hims", "ny", "compounded_glp1", 0.55, s1=0.8, s2=0.85),
        _alert("acc_001", "Hims", "fl", "compounded_glp1", 0.83, s1=0.8, s2=0.85),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert len(rollup) == 1
    row = rollup[0]
    assert set(row["states_affected"]) == {"ca", "ny", "fl"}
    assert row["topics_affected"] == ["compounded_glp1"]
    assert row["total_routed_alerts"] == 3
    assert row["multistate_indicator"] is True
    assert row["state_count_affected"] == 3
    # top_composite is the FL alert (0.83) which has the strongest convergence
    assert row["top_composite_score"] == 0.83
    # Best narrative comes from the top-composite alert
    assert row["best_opportunity_id"] == "acc_001|fl|compounded_glp1"


def test_multiple_topics_same_account_collapse_with_topic_list():
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85),
        _alert("acc_001", "Hims", "ca", "asynchronous_prescribing", 0.65, s1=0.9),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert len(rollup) == 1
    row = rollup[0]
    assert set(row["topics_affected"]) == {"compounded_glp1", "asynchronous_prescribing"}
    assert row["topic_count_affected"] == 2
    assert row["states_affected"] == ["ca"]
    # multistate_indicator is False: each topic only fires on one state.
    assert row["multistate_indicator"] is False
    # signals_summary takes max per signal across both alerts.
    assert row["signals_summary"]["S1"] == 0.9
    assert row["signals_summary"]["S2"] == 0.85


def test_different_accounts_produce_different_rows():
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.6, s2=0.85),
        _alert("acc_002", "Ro", "ca", "compounded_glp1", 0.55, s2=0.85),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert len(rollup) == 2
    acc_ids = {r["account_id"] for r in rollup}
    assert acc_ids == {"acc_001", "acc_002"}


def test_rollup_sorted_by_top_composite_descending():
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85),
        _alert("acc_002", "Ro", "ca", "compounded_glp1", 0.90, s1=0.95, s2=0.95),
        _alert("acc_003", "LifeMD", "fl", "compounded_glp1", 0.45, s2=0.85),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert [r["account_id"] for r in rollup] == ["acc_002", "acc_001", "acc_003"]


def test_signals_summary_takes_max_across_firings():
    """A single account can fire S1=0.5 on one (state, topic) and S1=0.9
    on another. The account-level signals_summary should report 0.9."""
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.4, s1=0.5),
        _alert("acc_001", "Hims", "fl", "compounded_glp1", 0.8, s1=0.9),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert rollup[0]["signals_summary"]["S1"] == 0.9


def test_evidence_drilldown_preserves_per_state_topic_records():
    """The rollup must keep per-(state, topic) detail so reviewers can drill
    back from the account view to the original signal evidence."""
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85, headline="CA headline"),
        _alert("acc_001", "Hims", "ny", "compounded_glp1", 0.60, s2=0.85, headline="NY headline"),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    drilldown = rollup[0]["evidence_drilldown"]
    assert len(drilldown) == 2
    headlines = {d["narrative_headline"] for d in drilldown}
    assert headlines == {"CA headline", "NY headline"}
    states = {d["state"] for d in drilldown}
    assert states == {"ca", "ny"}


def test_empty_alerts_returns_empty_rollup():
    assert aggregate_alerts_to_accounts([]) == []


def test_alert_with_missing_account_id_skipped():
    alerts = [
        {"account_id": "", "account_name": "x", "state": "ca", "topic": "x",
         "composite_score": 0.5, "signal_scores": {"S1": 0, "S2": 0, "S3": 0}},
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.6, s2=0.85),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    assert len(rollup) == 1
    assert rollup[0]["account_id"] == "acc_001"


def test_source_events_deduplicated_across_state_projections():
    """The same federal S2 LDA filing can project onto multiple states for
    the same account, producing duplicate source-event entries. The rollup
    should dedupe so the all_source_events union is honest."""
    same_event = {"signal_id": "S2_rival_co_mobilization", "event_summary": "LDA filing X", "score": 0.85}
    alert_ca = _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85)
    alert_ny = _alert("acc_001", "Hims", "ny", "compounded_glp1", 0.55, s2=0.85)
    alert_ca["source_events"]["S2"] = [same_event]
    alert_ny["source_events"]["S2"] = [same_event]
    rollup = aggregate_alerts_to_accounts([alert_ca, alert_ny])
    assert len(rollup[0]["all_source_events"]["S2"]) == 1


def test_triggering_events_summary_is_flat_deduped_list():
    """triggering_events_summary should be a flat list of unique event
    summary strings across S1/S2/S3, suitable for direct dashboard
    rendering (replacing the previous generic 'Outreach Trigger' label)."""
    alert = _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s1=0.8, s2=0.85, s3=0.7)
    rollup = aggregate_alerts_to_accounts([alert])
    triggers = rollup[0]["triggering_events_summary"]
    assert isinstance(triggers, list)
    assert "S1 ev for compounded_glp1" in triggers
    assert "S2 ev for compounded_glp1" in triggers
    assert "S3 ev for compounded_glp1" in triggers
    # No duplicates
    assert len(triggers) == len(set(triggers))


def test_triggering_events_summary_dedupes_across_multistate_firings():
    """When the same federal event projects across CA + NY + FL for one
    account, triggering_events_summary should list the event once."""
    alerts = [
        _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s1=0.85),
        _alert("acc_001", "Hims", "ny", "compounded_glp1", 0.55, s1=0.85),
        _alert("acc_001", "Hims", "fl", "compounded_glp1", 0.55, s1=0.85),
    ]
    rollup = aggregate_alerts_to_accounts(alerts)
    triggers = rollup[0]["triggering_events_summary"]
    # All three alerts share the same S1 event summary, so it appears once
    s1_count = sum(1 for t in triggers if t == "S1 ev for compounded_glp1")
    assert s1_count == 1


def test_latest_signal_event_date_uses_most_recent():
    """latest_signal_event_date should pull the most recent date from
    source events across the account's firings (used by the dashboard to
    replace the misleading 'Active < 1h ago' label)."""
    alert = _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85)
    alert["source_events"]["S2"] = [
        {"event_summary": "LDA early", "score": 0.85, "dt_posted": "2024-01-15"},
        {"event_summary": "LDA recent", "score": 0.85, "dt_posted": "2026-04-30"},
    ]
    rollup = aggregate_alerts_to_accounts([alert])
    assert rollup[0]["latest_signal_event_date"] == "2026-04-30"


def test_latest_signal_event_date_handles_missing_dates():
    """When no event has a usable date field, latest_signal_event_date
    falls back to None rather than crashing."""
    alert = _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85)
    # _alert helper produces events without date fields
    rollup = aggregate_alerts_to_accounts([alert])
    assert rollup[0]["latest_signal_event_date"] is None


def test_routing_destination_propagates_to_rollup():
    """Each rollup row should carry routing_destination from the underlying
    alert so the dashboard can differentiate routed alerts (alerts) from
    capture-only watchlist entries (watchlist_only)."""
    a1 = _alert("acc_001", "Hims", "ca", "compounded_glp1", 0.55, s2=0.85)
    a1["routing_destination"] = "alerts"
    a2 = _alert("acc_002", "Wisp", "ca", "asynchronous_prescribing", 0.25, s2=0.7)
    a2["routing_destination"] = "watchlist_only"
    rollup = aggregate_alerts_to_accounts([a1, a2])
    routings = {r["account_id"]: r.get("routing_destination") for r in rollup}
    assert routings["acc_001"] == "alerts"
    assert routings["acc_002"] == "watchlist_only"
