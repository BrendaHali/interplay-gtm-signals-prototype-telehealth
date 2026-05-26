"""
Signal 2 detector: Rival Co-Mobilization.

Wraps lda_client.detect_co_mobilization to produce a signal events JSON file in
the same format as Signal 1 and Signal 3 outputs. The detection logic lives in
lda_client because it is tightly coupled to LDA's data shape; this module
handles the persistence and scoring contract.

Output:
  - data/signals_co_mobilization.json: scored signal events with provenance

Per-event score is the ratio of competitors that fired within the rolling
90-day window over the set size, capped at 1.0. A set of two competitors
where both fired scores 1.0; a set of four where two fired scores 0.5.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts._lib.lda_client import detect_co_mobilization


def load_filings(path: Path = Path("data/lda_registrations.json")) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {acc: info.get("filings", []) for acc, info in data.get("accounts", {}).items()}


def load_competitor_pairs(path: Path = Path("data/competitor_pairs.json")) -> dict[str, Any]:
    return json.loads(path.read_text())


def detect() -> list[dict[str, Any]]:
    filings = load_filings()
    pairs = load_competitor_pairs()
    raw_events = detect_co_mobilization(filings, pairs)

    # Build a flat UUID -> dt_posted index so each S2 event can attach
    # latest_filing_dt_posted. The dashboard's latest_signal_event_date
    # falls back to None without a date on the event itself; aggregator
    # already recognizes dt_posted as a valid date field.
    uuid_to_date: dict[str, str] = {}
    for acc_filings in filings.values():
        for f in acc_filings:
            uuid = f.get("filing_uuid")
            dt = f.get("dt_posted")
            if uuid and isinstance(dt, str):
                uuid_to_date[uuid] = dt[:10]

    events: list[dict[str, Any]] = []
    for e in raw_events:
        filing_uuids = e.get("filing_uuids", {})
        all_uuids = [u for uuids in filing_uuids.values() for u in uuids]
        dates = [uuid_to_date[u] for u in all_uuids if u in uuid_to_date]
        latest_filing = max(dates) if dates else None
        events.append({
            "signal_id": "S2_rival_co_mobilization",
            "competitor_set_id": e.get("set_id"),
            "competitor_set_label": e.get("set_label"),
            "topic": e.get("topic"),
            "member_accounts": e.get("member_accounts", []),
            "filing_uuids_by_account": filing_uuids,
            "score": e.get("score", 0),
            "dt_posted": latest_filing,
        })
    return events


def persist(events: list[dict[str, Any]], out_path: Path = Path("data/signals_co_mobilization.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": "S2_rival_co_mobilization",
        "count": len(events),
        "events": events,
    }, indent=2))


def run() -> dict[str, Any]:
    events = detect()
    persist(events)
    return {
        "signal_id": "S2_rival_co_mobilization",
        "event_count": len(events),
        "events": events,
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    for e in summary["events"][:5]:
        print(f"  {e['competitor_set_label']} on '{e['topic']}': {len(e['member_accounts'])} members, score {e['score']}")
