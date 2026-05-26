"""
Composite blender.

Reads the three signal output files, identifies the (account, state, issue)
opportunities each event maps to, and combines signals that target the same
opportunity into a single composite score. Multiple signals firing on the
same opportunity is the high-conviction case the founder named on the call
as the interplay pattern.

Composite formula (matches the implementation at the bottom of blend()):
    composite = (w_s1 * s1_score + w_s2 * s2_score + w_s3 * s3_score)
              * business_model_exposure_weight   (from account_profiles.json)
              * engagement_multiplier            (from scoring_config.yaml)
              * risk_disclosure_multiplier       (capped, from data/sec_filings.json)

Per-signal recency decay is applied at the detector layer, not at the
composite layer. Weights default to (0.35, 0.30, 0.35) and are configurable
via scoring_config.yaml. Per-size-tier composite floors live in
scoring_config.yaml:size_tier_orchestration. Opportunities below the
applicable floor are dropped (or routed to data/watchlist_opportunities.json
when the tier's routes_to is watchlist_only).

The blender does not produce alerts; it produces a ranked queue of
opportunities each with full signal contribution breakdown for the
interpretation layer.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SIGNAL_FILES = {
    "S1_drug_enforcement_cascade": Path("data/signals_drug_enforcement.json"),
    "S2_rival_co_mobilization": Path("data/signals_co_mobilization.json"),
    "S3_enforcement_precursor": Path("data/signals_enforcement_precursor.json"),
}


def load_config(path: Path = Path("data/scoring_config.yaml")) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def load_account_profiles(path: Path = Path("data/account_profiles.json")) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text()).get("profiles", {})


def load_accounts(path: Path = Path("data/accounts.json")) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def load_sec_topic_hits(path: Path = Path("data/sec_filings.json")) -> dict[str, dict[str, int]]:
    """Returns {account_id: {topic: hit_count}} from the most recent SEC ingest.

    Used by the blender to apply a risk_disclosure_multiplier: when an account's
    own 10-Q / 10-K mentions the topic, opportunities on that (account, topic)
    pair get a small composite boost.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {
        acc_id: a.get("topic_hits", {})
        for acc_id, a in data.get("accounts", {}).items()
    }


def compute_risk_disclosure_multiplier(hits: int) -> float:
    """Map raw EDGAR full-text search hit count to a composite multiplier.

    Capped at 1.3x to prevent SEC mentions from dominating the score. A single
    mention adds 5% boost; saturation at six or more mentions.
    """
    if hits <= 0:
        return 1.0
    return min(1.3, 1.0 + 0.05 * hits)


# speculative ACV estimator removed in v5 due to high margin of assumption in seed state


def load_signal_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("events", [])


def _normalize_topic(text: str) -> str:
    """Map free-form text to a canonical topic key for cross-signal joining."""
    if not text:
        return "general"
    t = text.lower()
    if any(k in t for k in ("compounded glp", "semaglutide", "tirzepatide", "ozempic", "wegovy", "compounded weight loss", "glp1", "glp-1")):
        return "compounded_glp1"
    if any(k in t for k in ("asynchronous prescribing", "async prescribing", "in-person visit")):
        return "asynchronous_prescribing"
    if any(k in t for k in ("controlled substance", "ryan haight", "adhd prescribing", "stimulant", "adderall", "methylphenidate")):
        return "controlled_substance_telehealth"
    if any(k in t for k in ("scope of practice", "nurse practitioner", "physician assistant")):
        return "scope_of_practice"
    if any(k in t for k in ("interstate medical licensure", "imlc", "cross-border", "telehealth licensing")):
        return "telehealth_licensing"
    if any(k in t for k in ("mental health", "psychology compact", "behavioral health", "talkspace", "cerebral")):
        return "mental_health_telehealth"
    if any(k in t for k in ("medicaid telehealth", "telehealth parity", "reimbursement")):
        return "telehealth_reimbursement"
    if any(k in t for k in ("pbm regulation", "prescription pricing", "drug pricing")):
        return "prescription_economics"
    if any(k in t for k in ("hormone", "testosterone", "estrogen")):
        return "hormone_therapy"
    return "telehealth_general"


def _project_signal_to_opportunities(
    signal_id: str,
    event: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> list[tuple[str, str, str, float, dict[str, Any]]]:
    """
    Project a signal event onto one or more (account_id, state, topic) opportunities.
    Returns list of (account_id, state, topic, raw_signal_score, source_event) tuples.
    """
    out: list[tuple[str, str, str, float, dict[str, Any]]] = []

    if signal_id == "S1_drug_enforcement_cascade":
        # openFDA drug enforcement is federal, not state-keyed. Topic = drug category.
        # Fan out to accounts whose named disclosed risks or segment match the drug category,
        # using each account's primary state for the opportunity key.
        drug_category = event.get("drug_category", "")
        topic = _normalize_topic(drug_category)
        score = event.get("score", 0)
        profiles_data = json.loads(Path("data/account_profiles.json").read_text()).get("profiles", {})
        for acc in accounts:
            profile = profiles_data.get(acc["id"], {})
            segment = (profile.get("segment") or "").lower()
            risks = " ".join(profile.get("named_disclosed_risks", [])).lower()
            # Match drug category to account exposure
            relevant = False
            if drug_category == "glp1" and ("glp" in segment or "glp" in risks or "weight" in segment or "compounded" in risks):
                relevant = True
            elif drug_category == "adhd" and ("adhd" in segment or "adhd" in risks or "controlled-substance" in risks or "controlled substance" in risks):
                relevant = True
            elif drug_category == "hormones" and ("hormone" in risks or "men's health" in segment or "specialty" in segment or "multi_category" in segment):
                relevant = True
            elif drug_category == "ssri_snri" and ("mental" in segment or "behavioral" in risks):
                relevant = True
            elif drug_category in ("hair", "weight_loss_other") and ("multi_category" in segment or "specialty" in segment or "glp" in segment):
                relevant = True
            if relevant:
                top_states = profile.get("top_state_exposures") or [acc.get("state_footprint", ["us"])[0]]
                for state in top_states:
                    out.append((acc["id"], state, topic, score, event))


    elif signal_id == "S2_rival_co_mobilization":
        topic = _normalize_topic(event.get("topic", ""))
        score = event.get("score", 0)
        # Co-mobilization is federal lobbying; project onto each member account's
        # top_state_exposures so the regulatory exposure surfaces across the
        # account's most-material states (not just state_footprint[0]).
        profiles_data = json.loads(Path("data/account_profiles.json").read_text()).get("profiles", {})
        for acc_id in event.get("member_accounts", []):
            acc = next((a for a in accounts if a["id"] == acc_id), None)
            if not acc:
                continue
            profile = profiles_data.get(acc_id, {})
            top_states = profile.get("top_state_exposures") or [
                (acc.get("state_footprint") or ["us"])[0]
            ]
            for state in top_states:
                out.append((acc_id, state, topic, score, event))

    elif signal_id == "S3_enforcement_precursor":
        topic = _normalize_topic(event.get("detected_topic", ""))
        score = event.get("score", 0)
        # Use the detected state if present, else the state of the first matched
        # legislative item. State name → 2-letter abbr conversion uses the full
        # 50-state map from signal_enforcement_precursor (deduplicated).
        from scripts._lib.signal_enforcement_precursor import _state_abbr
        detected_state = (event.get("detected_state") or "").lower()
        state = _state_abbr(detected_state) if detected_state else ""
        if not state and event.get("matched_legislative"):
            ml = event["matched_legislative"][0]
            ml_state = (ml.get("state") or "").lower()
            state = _state_abbr(ml_state) if ml_state else ""
        if state:
            # Narrow to accounts whose named disclosed risks mention the topic AND whose footprint includes the state.
            # Falls back to footprint-only matching if no risk-topic matches found, so the signal still surfaces.
            profiles_data = json.loads(Path("data/account_profiles.json").read_text()).get("profiles", {})
            topic_relevant = []
            for acc in accounts:
                if state not in acc.get("state_footprint", []):
                    continue
                profile = profiles_data.get(acc["id"], {})
                risks = " ".join(profile.get("named_disclosed_risks", [])).lower()
                segment = (profile.get("segment") or "").lower()
                # Telehealth topic to account exposure mapping
                if topic == "compounded_glp1" and ("compounded" in risks or "glp" in risks or "glp" in segment or "weight" in segment):
                    topic_relevant.append(acc)
                elif topic == "asynchronous_prescribing" and ("asynchronous" in risks or "prescribing" in risks or "multi_category" in segment):
                    topic_relevant.append(acc)
                elif topic == "controlled_substance_telehealth" and ("controlled-substance" in risks or "controlled substance" in risks or "adhd" in segment or "stimulant" in risks):
                    topic_relevant.append(acc)
                elif topic == "scope_of_practice" and ("professional licensure" in risks or "scope" in risks or "virtual_care" in segment):
                    topic_relevant.append(acc)
                elif topic == "telehealth_licensing" and ("licensure" in risks or "cross-border" in risks or "compact" in risks):
                    topic_relevant.append(acc)
                elif topic == "mental_health_telehealth" and ("mental" in segment or "behavioral" in risks or "adhd" in segment):
                    topic_relevant.append(acc)
                elif topic == "telehealth_reimbursement" and ("medicaid" in risks or "reimbursement" in risks or "virtual_care" in segment):
                    topic_relevant.append(acc)
                elif topic == "telehealth_general":
                    topic_relevant.append(acc)
            target_accounts = topic_relevant if topic_relevant else accounts
            for acc in target_accounts:
                if state in acc.get("state_footprint", []):
                    out.append((acc["id"], state, topic, score, event))

    return out


def blend() -> list[dict[str, Any]]:
    """Run the composite blender. Returns ranked list of opportunity dicts."""
    config = load_config()
    weights = config.get("composite_weights", {
        "s1_drug_enforcement_cascade": 0.35,
        "s2_rival_co_mobilization": 0.30,
        "s3_enforcement_precursor": 0.35,
    })
    composite_floor = config.get("composite_score_floor", 0.0)

    accounts = load_accounts()
    profiles = load_account_profiles()
    sec_topic_hits = load_sec_topic_hits()

    # Aggregate signals per opportunity
    # Key: (account_id, state, topic). Value: dict with per-signal scores and source events.
    opps: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(lambda: {
        "signal_scores": {"S1": 0.0, "S2": 0.0, "S3": 0.0},
        "source_events": {"S1": [], "S2": [], "S3": []},
    })

    for signal_id, path in SIGNAL_FILES.items():
        events = load_signal_events(path)
        signal_key = signal_id.split("_")[0]  # S1, S2, S3
        for event in events:
            projections = _project_signal_to_opportunities(signal_id, event, accounts)
            for acc_id, state, topic, score, src in projections:
                key = (acc_id, state, topic)
                # Keep the highest signal score if multiple events project to same opp
                if score > opps[key]["signal_scores"][signal_key]:
                    opps[key]["signal_scores"][signal_key] = score
                opps[key]["source_events"][signal_key].append({
                    "signal_id": signal_id,
                    "score": score,
                    "event_summary": _summarize_event(signal_id, src),
                    **_preserve_event_dates(signal_id, src),
                })

    # Size-tier orchestration: every account is in the watchlist for signal
    # capture, but routing is gated by size_tier and tracking_status so
    # midmarket and startup accounts do not crowd out enterprise alerts
    # before per-tier attribution data is built. The per-tier composite
    # floor and routing destination are read from scoring_config.yaml's
    # size_tier_orchestration block, with sensible defaults if absent.
    size_tier_config = config.get("size_tier_orchestration", {})
    tier_defaults = {
        "enterprise": {"composite_floor": composite_floor, "routes_to": "alerts"},
        "midmarket": {"composite_floor": max(composite_floor, 0.40), "routes_to": "alerts"},
        "startup": {"composite_floor": max(composite_floor, 0.60), "routes_to": "watchlist_only"},
    }

    # Compute composite scores
    ranked: list[dict[str, Any]] = []
    watchlist: list[dict[str, Any]] = []
    for (acc_id, state, topic), data in opps.items():
        s1 = data["signal_scores"]["S1"]
        s2 = data["signal_scores"]["S2"]
        s3 = data["signal_scores"]["S3"]
        base = (
            weights.get("s1_drug_enforcement_cascade", 0.35) * s1
            + weights.get("s2_rival_co_mobilization", 0.30) * s2
            + weights.get("s3_enforcement_precursor", 0.35) * s3
        )
        # No event_date on aggregated opp; skip freshness decay here (per-signal already decayed)
        bm_weight = profiles.get(acc_id, {}).get("business_model_exposure_weight", 1.0)
        account = next((a for a in accounts if a["id"] == acc_id), {})

        # Risk-disclosure multiplier: when the account's own most recent 10-Q
        # or 10-K mentions this topic, boost the composite. Private accounts
        # contribute zero hits and the multiplier remains 1.0. The catch-all
        # topic 'telehealth_general' sums hits across every specific telehealth
        # topic because the company's filing language for generic exposure
        # cuts across the entire taxonomy.
        topic_hits_for_account = sec_topic_hits.get(acc_id, {})
        if topic == "telehealth_general":
            risk_disclosure_hit_count = sum(topic_hits_for_account.values())
        else:
            risk_disclosure_hit_count = topic_hits_for_account.get(topic, 0)
        risk_disclosure_multiplier = compute_risk_disclosure_multiplier(risk_disclosure_hit_count)

        # Composite: three real multipliers only. The synthetic
        # engagement_multiplier was dropped in v1 because the underlying
        # engagement_status field was a placeholder (no real CRM connection).
        # Re-introduce once HubSpot writeback yields live engagement stages.
        composite = round(base * bm_weight * risk_disclosure_multiplier, 3)

        size_tier = account.get("size_tier", "midmarket")
        tracking_status = account.get("tracking_status", "active_alert")
        tier_cfg = size_tier_config.get(size_tier, tier_defaults.get(size_tier, tier_defaults["midmarket"]))
        tier_floor = tier_cfg.get("composite_floor", composite_floor)
        routes_to = tier_cfg.get("routes_to", "alerts")

        is_below_floor = composite < tier_floor
        if is_below_floor:
            routes_to = "watchlist_only"
        elif tracking_status == "track_only":
            routes_to = "watchlist_only"

        profile_full = profiles.get(acc_id, {})
        record = {
            "opportunity_id": f"{acc_id}|{state}|{topic}",
            "account_id": acc_id,
            "account_name": account.get("name") or profile_full.get("name", "unknown"),
            "account_segment": profile_full.get("segment"),
            "account_size_tier": size_tier,
            "account_tracking_status": "below_floor" if is_below_floor else tracking_status,
            "account_parent": account.get("parent_account"),
            "account_topic_count": len(profile_full.get("topic_exposure") or []),
            "account_employee_band": profile_full.get("employee_count_band"),
            "account_funding_stage": profile_full.get("funding_stage"),
            "account_target_tier": profile_full.get("target_tier"),
            "state": state,
            "topic": topic,
            "composite_score": composite,
            "composite_floor_applied": tier_floor,
            "signal_scores": data["signal_scores"],
            "business_model_exposure_weight": bm_weight,
            "risk_disclosure_hit_count": risk_disclosure_hit_count,
            "risk_disclosure_multiplier": risk_disclosure_multiplier,
            "signals_fired": sum(1 for v in data["signal_scores"].values() if v > 0),
            "source_events": data["source_events"],
            "routing_destination": routes_to,
        }
        if routes_to == "alerts":
            ranked.append(record)
        else:
            watchlist.append(record)

    ranked.sort(key=lambda x: -x["composite_score"])
    watchlist.sort(key=lambda x: -x["composite_score"])
    # Persist the watchlist alongside the alert-bound opportunities so
    # downstream consumers (and the dashboard) can see the full signal-
    # capture surface, not just the routed alerts.
    Path("outputs/watchlist_opportunities.json").write_text(json.dumps({
        "count": len(watchlist),
        "opportunities": watchlist,
    }, indent=2))
    return ranked


def _preserve_event_dates(signal_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Lift date fields from raw signal events into the alert source_events
    payload so the aggregator can compute latest_signal_event_date. Without
    this, the aggregator sees only signal_id/score/event_summary and returns
    None for every account's latest signal date."""
    dates: dict[str, Any] = {}
    if signal_id == "S1_drug_enforcement_cascade":
        if event.get("spike_week_start"):
            dates["spike_week_start"] = event["spike_week_start"]
    elif signal_id == "S2_rival_co_mobilization":
        if event.get("dt_posted"):
            dates["dt_posted"] = event["dt_posted"]
    elif signal_id == "S3_enforcement_precursor":
        if event.get("enforcement_date"):
            dates["enforcement_date"] = event["enforcement_date"]
    return dates


def _summarize_event(signal_id: str, event: dict[str, Any]) -> str:
    """Human-readable summary of a signal event for the alert payload.

    Reviewers read these strings directly in the dashboard's "Triggering
    events" lists, so each summary names the underlying mechanism (FDA
    spike, competitor convergence, peer enforcement), the population
    affected (drug category, competitor set + count, target peer), and
    the date when available."""
    if signal_id == "S1_drug_enforcement_cascade":
        category = (event.get("drug_category") or "").replace("_", " ")
        z = event.get("spike_z_score")
        n = event.get("spike_current_count")
        week = event.get("spike_week_start")
        head = f"FDA enforcement spike on {category}"
        detail = f"{n} actions in week of {week}" if week else f"{n} actions"
        stat = f"z-score {z}" if z is not None else ""
        return f"{head}: {detail}" + (f" ({stat})" if stat else "")
    if signal_id == "S2_rival_co_mobilization":
        label = event.get("competitor_set_label") or "competitor set"
        topic = (event.get("topic") or "").replace("_", " ")
        members = event.get("member_accounts") or []
        latest = event.get("dt_posted")
        topic_phrase = "telehealth (any topic)" if topic == "telehealth general" else topic
        head = f"{label}: {len(members)} competitors filed federal lobbying on {topic_phrase}"
        return head + (f" (latest filing {latest})" if latest else "")
    if signal_id == "S3_enforcement_precursor":
        title = (event.get("enforcement_title") or "").strip()
        date = (event.get("enforcement_date") or "")[:10]
        head = f"Peer enforcement: {title[:100]}" if title else "Peer enforcement action"
        return head + (f" ({date})" if date else "")
    return str(event)[:100]


def apply_per_ae_caps(
    opportunities: list[dict[str, Any]],
    cap_per_ae: int | None = None,
) -> list[dict[str, Any]]:
    """Apply per-AE daily cap. Keeps top N opportunities per AE by composite score.

    Cap is read from scoring_config.yaml daily_caps.slack_alert when not
    explicitly provided. Midmarket pool counts collectively under
    midmarket_pool. Track-only accounts are already excluded upstream by
    routing_destination.
    """
    if cap_per_ae is None:
        try:
            cap_per_ae = int(load_config().get("daily_caps", {}).get("slack_alert", 3))
        except Exception:
            cap_per_ae = 3
    by_ae: dict[str, int] = defaultdict(int)
    kept: list[dict[str, Any]] = []
    for opp in opportunities:
        ae = opp.get("account_owner_ae") or "_unowned"
        if by_ae[ae] < cap_per_ae:
            kept.append(opp)
            by_ae[ae] += 1
    return kept


def persist(opportunities: list[dict[str, Any]], out_path: Path = Path("data/opportunities.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(opportunities),
        "opportunities": opportunities,
    }, indent=2))


def run(apply_caps: bool = False) -> dict[str, Any]:
    """Top-level blender entry point.

    apply_caps defaults to False in v1: the per-AE cap mechanism caps each
    account_owner_ae at three opportunities, but v1 has no real AEs (the
    owner_ae field was nulled during synthetic cleanup). Capping under a
    single _unowned bucket collapses every surfaced opportunity to just the
    global top 3, hiding the rest from the RevOps inspection dashboard.
    Re-enable in v2 when HubSpot writeback supplies real AE assignments.
    """
    ranked = blend()
    final = apply_per_ae_caps(ranked) if apply_caps else ranked
    persist(final)
    return {
        "raw_opportunity_count": len(ranked),
        "after_caps_count": len(final),
        "caps_applied": apply_caps,
        "top_5": final[:5],
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "top_5"}, indent=2))
    print("\nTop 5 opportunities:")
    for opp in summary["top_5"]:
        print(f"\n  {opp['account_name']} | {opp['state']} | {opp['topic']} | composite {opp['composite_score']}")
        print(f"    signals fired: {opp['signals_fired']}/3 (S1={opp['signal_scores']['S1']}, S2={opp['signal_scores']['S2']}, S3={opp['signal_scores']['S3']})")
        for sig_key, events in opp['source_events'].items():
            for ev in events[:2]:
                print(f"      {ev['event_summary']}")
