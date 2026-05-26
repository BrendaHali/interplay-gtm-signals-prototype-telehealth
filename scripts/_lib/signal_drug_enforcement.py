"""
Signal 1 detector for telehealth ICP: Drug Enforcement Cascade.

Joins openFDA drug enforcement spikes (on telehealth-prescribed drug categories)
with state legislative activity on telehealth prescribing, pharmacy compounding,
or scope-of-practice topics. The cascade pattern: FDA enforcement against a
drug category (compounded GLP-1 sterility failures, controlled-substance
violations) precedes state legislative attention by 30 to 90 days because state
pharmacy boards and legislators respond to federal enforcement signals.

Inputs:
  - data/drug_enforcement.json (produced by openfda_client)
  - data/openstates_bills.json (produced by openstates_client with telehealth keywords)

Output:
  - data/signals_drug_enforcement.json: scored signal events with provenance
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts._lib.openfda_client import compute_weekly_volumes, detect_spikes


# Map drug category to OpenStates bill keywords. When a drug category spikes,
# we look for state bills on these topics.
CATEGORY_TO_BILL_KEYWORDS: dict[str, list[str]] = {
    "glp1": ["compounded GLP-1", "semaglutide", "tirzepatide", "pharmacy compounding", "asynchronous prescribing", "BMI prescribing"],
    "adhd": ["ADHD prescribing", "controlled substance telehealth", "stimulant prescribing", "Ryan Haight"],
    "hormones": ["hormone therapy", "testosterone prescribing", "compounded hormones"],
    "hair": ["finasteride", "telehealth prescribing"],
    "ssri_snri": ["mental health telehealth", "psychiatric prescribing"],
    "weight_loss_other": ["weight loss medication", "phentermine"],
}


def load_records(path: Path = Path("data/drug_enforcement.json")) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {cat: info.get("records", []) for cat, info in data.get("categories", {}).items()}


def load_bills(path: Path = Path("data/openstates_bills.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("bills", [])


def detect(
    spike_z_threshold: float = 2.0,
    monthly_volume_floor: int = 3,
    baseline_weeks: int = 8,
    bill_match_window_days: int = 60,
) -> list[dict[str, Any]]:
    """
    Cross-reference drug enforcement spikes with OpenStates bills. Returns
    scored signal events.
    """
    records = load_records()
    bills = load_bills()
    if not records:
        return []

    volumes = compute_weekly_volumes(records)
    spikes = detect_spikes(
        volumes,
        baseline_weeks=baseline_weeks,
        z_threshold=spike_z_threshold,
        monthly_volume_floor=monthly_volume_floor,
    )

    events: list[dict[str, Any]] = []
    for spike in spikes:
        category = spike["drug_category"]
        keywords = CATEGORY_TO_BILL_KEYWORDS.get(category, [])
        spike_date = datetime.fromisoformat(spike["week_start"]).date()

        matched_bills: list[dict[str, Any]] = []
        for bill in bills:
            bill_kws = [k.lower() for k in bill.get("matched_keywords", [])]
            if not any(any(target.lower() in k or k in target.lower() for k in bill_kws) for target in keywords):
                continue
            latest = bill.get("latest_action_date")
            if not latest:
                continue
            try:
                bill_date = datetime.fromisoformat(latest).date()
            except ValueError:
                continue
            # Forward-only matching: spec is "spike followed within 60 days by
            # state bill activity." Pre-spike bills are not causally linked.
            days_after_spike = (bill_date - spike_date).days
            if 0 <= days_after_spike <= bill_match_window_days:
                matched_bills.append({
                    "identifier": bill.get("identifier"),
                    "title": bill.get("title"),
                    "jurisdiction": bill.get("jurisdiction_name"),
                    "openstates_url": bill.get("openstates_url"),
                    "latest_action_date": latest,
                    "days_from_spike": days_after_spike,
                })

        if not matched_bills:
            # Spike with no bill match still has independent signal value:
            # federal enforcement against a telehealth-prescribed drug category
            # is itself a leading indicator. Score at 0.75x of bill-matched.
            spike_factor = min(1.0, spike["z_score"] / 3.0) if spike["z_score"] > 0 else 0.7
            recency_factor = _recency_decay(spike_date, half_life_days=21)
            score = round(spike_factor * recency_factor * 0.75, 3)
            events.append({
                "signal_id": "S1_drug_enforcement_cascade",
                "drug_category": category,
                "spike_week_start": spike["week_start"],
                "spike_z_score": spike["z_score"],
                "spike_current_count": spike["current_count"],
                "spike_baseline_mean": spike["baseline_mean"],
                "matched_bills": [],
                "score": score,
                "score_breakdown": {
                    "spike_factor": round(spike_factor, 3),
                    "recency_factor": round(recency_factor, 3),
                    "no_bill_match_discount": 0.75,
                },
            })
            continue

        spike_factor = min(1.0, spike["z_score"] / 3.0)
        match_factor = min(1.0, len(matched_bills) / 2.0)
        recency_factor = _recency_decay(spike_date, half_life_days=21)
        score = round(spike_factor * match_factor * recency_factor, 3)

        events.append({
            "signal_id": "S1_drug_enforcement_cascade",
            "drug_category": category,
            "spike_week_start": spike["week_start"],
            "spike_z_score": spike["z_score"],
            "spike_current_count": spike["current_count"],
            "spike_baseline_mean": spike["baseline_mean"],
            "matched_bills": matched_bills,
            "score": score,
            "score_breakdown": {
                "spike_factor": round(spike_factor, 3),
                "match_factor": round(match_factor, 3),
                "recency_factor": round(recency_factor, 3),
            },
        })
    return events


def _recency_decay(event_date, half_life_days: int = 21) -> float:
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    days_ago = (datetime.now(timezone.utc).date() - event_date).days
    return 0.5 ** (max(0, days_ago) / half_life_days)


def persist(events: list[dict[str, Any]], out_path: Path = Path("data/signals_drug_enforcement.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": "S1_drug_enforcement_cascade",
        "count": len(events),
        "events": events,
    }, indent=2))


def run() -> dict[str, Any]:
    events = detect()
    persist(events)
    return {
        "signal_id": "S1_drug_enforcement_cascade",
        "event_count": len(events),
        "events": events,
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    for e in summary["events"][:5]:
        print(f"\n  {e['drug_category']} z={e['spike_z_score']} score={e['score']}")
        for b in e.get("matched_bills", []):
            print(f"    bill: {b['identifier']} ({b['jurisdiction']}): {(b.get('title') or '')[:80]}")
