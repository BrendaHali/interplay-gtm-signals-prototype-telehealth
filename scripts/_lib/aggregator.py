"""
Per-account aggregator.

The interpreter produces per-(account, state, topic) alert records in
outputs/alerts.json. This module rolls those up into per-account records
suitable for sales prioritization, because the buying decision is at the
account level, not at the (account, state, topic) tuple. A single account
firing on three states and two topics produces one aggregated row with
states_affected = [...], topics_affected = [...], and multistate_indicator
flagged, not six dashboard rows that misrepresent six independent
opportunities.

The aggregator preserves the original alert payloads as
evidence_drilldown so reviewers can drill back to the per-state-topic
signal evidence when needed. outputs/alerts.json stays in place as the
canonical attribution layer; outputs/accounts_with_signals.json becomes
the prioritization layer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def aggregate_alerts_to_accounts(
    alerts: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Roll up per-(account, state, topic) alerts to per-account records.

    Returns a list of account-level dicts ordered by top_composite_score
    descending. Same account_id collapses to one record even if the alert
    fires across multiple states or topics.

    The optional `profiles` argument is the account_profiles.json profiles
    dict; when supplied, the rollup attaches watchlist directory fields
    (hq_city, hq_state, website, product_one_liner, founded_year, employee
    band, funding stage, etc.) per account so the dashboard's full-watchlist
    view has every relevant attribute without a second lookup. When None,
    falls back to fields embedded in each alert.
    """
    if profiles is None:
        profiles_path = Path("data/account_profiles.json")
        if profiles_path.exists():
            profiles = json.loads(profiles_path.read_text()).get("profiles", {})
        else:
            profiles = {}

    by_account: dict[str, dict[str, Any]] = {}

    for alert in alerts:
        acc_id = alert.get("account_id")
        if not acc_id:
            continue
        if acc_id not in by_account:
            profile = profiles.get(acc_id, {})
            by_account[acc_id] = {
                "account_id": acc_id,
                "account_name": alert.get("account_name"),
                "account_size_tier": alert.get("account_size_tier"),
                "account_target_tier": alert.get("account_target_tier"),
                "account_segment": alert.get("account_segment"),
                "account_employee_band": alert.get("account_employee_band") or profile.get("employee_count_band"),
                "account_funding_stage": alert.get("account_funding_stage") or profile.get("funding_stage"),
                "account_revenue_band": profile.get("revenue_band"),
                "account_parent": alert.get("account_parent"),
                "account_tracking_status": alert.get("account_tracking_status"),
                "routing_destination": alert.get("routing_destination"),
                # Watchlist directory fields sourced from account_profiles.json.
                # These do not depend on this run's signals; they describe the
                # account itself for full-watchlist coverage display.
                "hq_city": profile.get("hq_city"),
                "hq_state": profile.get("hq_state"),
                "website": profile.get("website"),
                "product_one_liner": profile.get("product_one_liner"),
                "founded_year": profile.get("founded_year"),
                "topic_exposure_full": profile.get("topic_exposure", []),
                "public_company": profile.get("public", False),
                "ticker": profile.get("ticker"),
                "disclosure_source": profile.get("disclosure_source"),
                "acquired_note": profile.get("acquired_note"),
                # Rollup fields populated below from per-(state, topic) alerts.
                "states_affected": [],
                "topics_affected": [],
                "top_composite_score": 0.0,
                "signals_summary": {"S1": 0.0, "S2": 0.0, "S3": 0.0},
                "total_routed_alerts": 0,
                "all_source_events": {"S1": [], "S2": [], "S3": []},
                "evidence_drilldown": [],
                "best_narrative": None,
                "best_narrative_source": None,
                "best_opportunity_id": None,
            }
        row = by_account[acc_id]

        state = alert.get("state")
        topic = alert.get("topic")
        if state and state not in row["states_affected"]:
            row["states_affected"].append(state)
        if topic and topic not in row["topics_affected"]:
            row["topics_affected"].append(topic)
        row["total_routed_alerts"] += 1

        # Track max signal scores per signal across all this account's firings.
        signal_scores = alert.get("signal_scores", {})
        for sig_key in ("S1", "S2", "S3"):
            score = signal_scores.get(sig_key, 0) or 0
            if score > row["signals_summary"][sig_key]:
                row["signals_summary"][sig_key] = score

        # Track the highest-composite firing as the canonical narrative for
        # display. Sales reps see one narrative per account, drawn from the
        # opportunity with the strongest multi-signal convergence.
        composite = alert.get("composite_score", 0) or 0
        if composite > row["top_composite_score"]:
            row["top_composite_score"] = composite
            row["best_narrative"] = alert.get("narrative")
            row["best_narrative_source"] = alert.get("narrative_source")
            row["best_opportunity_id"] = alert.get("opportunity_id")

        # Union of source events across all (state, topic) firings.
        for sig_key, events in (alert.get("source_events") or {}).items():
            if sig_key in row["all_source_events"]:
                row["all_source_events"][sig_key].extend(events)

        # Evidence drilldown: preserve the per-(state, topic) record so a
        # reviewer can drill back into the original signal-attribution layer.
        row["evidence_drilldown"].append({
            "opportunity_id": alert.get("opportunity_id"),
            "state": state,
            "topic": topic,
            "composite_score": composite,
            "signal_scores": signal_scores,
            "signals_fired": alert.get("signals_fired"),
            "source_events": alert.get("source_events", {}),
            "narrative_headline": (alert.get("narrative") or {}).get("headline"),
        })

    # Derived flags + source-event dedup.
    for row in by_account.values():
        # multistate_indicator: at least one topic fires on two or more states
        # for this account. Distinguishes single-state exposure from a
        # multistate regulatory pattern, which is the credibility signal a
        # buyer's GA team cares about.
        topic_to_states: dict[str, set[str]] = {}
        for ev in row["evidence_drilldown"]:
            t = ev.get("topic")
            s = ev.get("state")
            if t and s:
                topic_to_states.setdefault(t, set()).add(s)
        row["multistate_indicator"] = any(len(states) > 1 for states in topic_to_states.values())
        row["state_count_affected"] = len(row["states_affected"])
        row["topic_count_affected"] = len(row["topics_affected"])

        # Dedup source events by (event_summary, score). The same federal
        # FDA recall can project onto multiple states for the same account
        # and produce duplicate evidence entries; dedup so the union view
        # is honest.
        for sig_key, events in row["all_source_events"].items():
            seen: set[tuple[str, str]] = set()
            unique: list[dict[str, Any]] = []
            for ev in events:
                key = (ev.get("event_summary", ""), str(ev.get("score", "")))
                if key not in seen:
                    seen.add(key)
                    unique.append(ev)
            row["all_source_events"][sig_key] = unique

        # triggering_events_summary: flat, deduped list of actual event
        # summary strings across S1, S2, S3 for direct dashboard rendering.
        # Replaces the previous "Outreach Trigger" generic signal-type label
        # with the specific events that fired (e.g. "FDA spike: glp1 (z=14.7)").
        triggering: list[str] = []
        seen_summaries: set[str] = set()
        for sig_key in ("S1", "S2", "S3"):
            for ev in row["all_source_events"].get(sig_key, []):
                summary = ev.get("event_summary")
                if summary and summary not in seen_summaries:
                    seen_summaries.add(summary)
                    triggering.append(summary)
        row["triggering_events_summary"] = triggering

        # latest_signal_event_date: most recent date across all source events.
        # Replaces the misleading "Active < 1h ago" dashboard label (which
        # reflected pipeline run time, not signal event time). Pulls from
        # the date fields the detector emits per signal type:
        #   S1: spike_week_start
        #   S2: filing_dt_posted or dt_posted
        #   S3: enforcement_date / matched_legislative.date / pub_date
        # Falls back to None when no usable date is present in evidence.
        date_candidates: list[str] = []
        for sig_key, events in row["all_source_events"].items():
            for ev in events:
                for key in ("event_date", "spike_week_start", "enforcement_date",
                            "filing_dt_posted", "dt_posted", "pub_date", "date"):
                    val = ev.get(key)
                    if isinstance(val, str) and val:
                        date_candidates.append(val[:10])
                        break
        row["latest_signal_event_date"] = max(date_candidates) if date_candidates else None

    return sorted(by_account.values(), key=lambda r: -r["top_composite_score"])


def persist(
    accounts: list[dict[str, Any]],
    out_path: Path = Path("outputs/accounts_with_signals.json"),
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(accounts),
        "schema_note": (
            "Per-account rollup of outputs/alerts.json. One record per "
            "account_id even when signals fire across multiple states and "
            "topics. evidence_drilldown preserves the original per-(state, "
            "topic) attribution for click-through inspection."
        ),
        "accounts": accounts,
    }, indent=2))


def run(
    alerts_path: Path = Path("outputs/alerts.json"),
    watchlist_path: Path = Path("outputs/watchlist_opportunities.json"),
    out_path: Path = Path("outputs/accounts_with_signals.json"),
) -> dict[str, Any]:
    """Read alerts.json + watchlist_opportunities.json and write the
    per-account rollup. Combines both inputs into one unified view so the
    dashboard shows every account with any signal activity (enterprise +
    midmarket + startup) instead of only AE-routed alerts. Each rollup
    record carries routing_destination so the dashboard can visually
    differentiate routed alerts from below-floor / watchlist captures
    without splitting them into separate tables.
    """
    combined: list[dict[str, Any]] = []
    if alerts_path.exists():
        combined.extend(json.loads(alerts_path.read_text()).get("alerts", []))
    if watchlist_path.exists():
        combined.extend(json.loads(watchlist_path.read_text()).get("opportunities", []))

    rollup = aggregate_alerts_to_accounts(combined)
    persist(rollup, out_path)

    by_routing: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for r in rollup:
        by_routing[r.get("routing_destination") or "unknown"] = by_routing.get(r.get("routing_destination") or "unknown", 0) + 1
        by_tier[r.get("account_size_tier") or "unknown"] = by_tier.get(r.get("account_size_tier") or "unknown", 0) + 1

    return {
        "accounts_surfaced": len(rollup),
        "total_routed_alerts": sum(r["total_routed_alerts"] for r in rollup),
        "multistate_accounts": sum(1 for r in rollup if r["multistate_indicator"]),
        "accounts_by_routing": by_routing,
        "accounts_by_size_tier": by_tier,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
