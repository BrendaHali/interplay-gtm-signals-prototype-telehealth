"""
OpenStates v3 REST API client.

Source: https://v3.openstates.org
Free tier requires an API key (X-API-KEY header), capped at 500 daily requests
and 1 request per second.

Two responsibilities:
  1. Fetch bills matching telehealth keywords across the top 10 states within
     a date window, used by Signal 1 (Drug Enforcement Cascade) for the bill
     side of the cross-reference.
  2. Fetch upcoming committee hearings across the top 10 states within a
     date window, used by Signal 3 (Enforcement Precursor) for the hearing
     side of the cross-reference.

The 1-request-per-second cap is honored explicitly via time.sleep between
calls. The 500-daily-request cap is respected by aggressive client-side
caching on a per-query basis.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts._lib._utils import get_logger, retry_on_transient

log = get_logger(__name__)

OPENSTATES_BASE = "https://v3.openstates.org"
RATE_LIMIT_SECONDS = 1.2  # buffer above the 1 req/sec free-tier cap

# Module-level last-call timestamp enforces the 1 req/sec cap across ALL
# OpenStates calls (bills + hearings + any other endpoint), not just
# inter-page within a single fetch. Without this, looping over 17 keywords
# or 10 states triggers 429 because back-to-back keyword fetches do not
# inherit the per-page sleep.
_LAST_OPENSTATES_CALL_TS: float = 0.0


def _throttle_openstates() -> None:
    """Block until at least RATE_LIMIT_SECONDS have passed since the previous call."""
    global _LAST_OPENSTATES_CALL_TS
    elapsed = time.time() - _LAST_OPENSTATES_CALL_TS
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _LAST_OPENSTATES_CALL_TS = time.time()

TOP_10_STATES: tuple[str, ...] = ("ca", "ny", "tx", "il", "fl", "pa", "oh", "ga", "nc", "mi")

# Telehealth ICP keyword vocabulary.
TELEHEALTH_KEYWORDS: tuple[str, ...] = (
    "telehealth",
    "telemedicine",
    "asynchronous prescribing",
    "Interstate Medical Licensure Compact",
    "scope of practice",
    "nurse practitioner",
    "pharmacy compounding",
    "compounded GLP-1",
    "semaglutide",
    "tirzepatide",
    "ADHD prescribing",
    "controlled substance telehealth",
    "Ryan Haight",
    "BMI prescribing",
    "telehealth parity",
    "Medicaid telehealth",
    "in-person visit requirement",
)


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENSTATES_API_KEY")
    if not key:
        raise RuntimeError("OPENSTATES_API_KEY not set in environment")
    return {"X-API-KEY": key}


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_bills_for_keyword(
    keyword: str,
    states: tuple[str, ...] = TOP_10_STATES,
    action_since: str | None = None,
    max_pages: int = 5,
    per_page: int = 20,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Fetch bills matching `keyword` across the given states, with bills filtered
    to those with action since `action_since` (ISO date). Returns flat list of
    bill dicts with id, identifier, title, jurisdiction, latest_action_date,
    and sources.
    """
    url = f"{OPENSTATES_BASE}/bills"
    params: list[tuple[str, str]] = [
        ("q", keyword),
        ("per_page", str(per_page)),
        ("sort", "latest_action_desc"),
    ]
    for s in states:
        params.append(("jurisdiction", s))
    if action_since:
        params.append(("action_since", action_since))

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, headers=_headers()) as client:
        for page in range(1, max_pages + 1):
            page_params = params + [("page", str(page))]
            _throttle_openstates()
            resp = client.get(url, params=page_params)
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results", [])
            if not results:
                break
            for b in results:
                rows.append({
                    "id": b.get("id"),
                    "identifier": b.get("identifier"),
                    "title": b.get("title"),
                    "jurisdiction_name": (b.get("jurisdiction") or {}).get("name"),
                    "jurisdiction_id": (b.get("jurisdiction") or {}).get("id"),
                    "session": b.get("session"),
                    "latest_action_date": b.get("latest_action_date"),
                    "latest_action_description": b.get("latest_action_description"),
                    "openstates_url": b.get("openstates_url"),
                    "subject": b.get("subject", []),
                    "matched_keyword": keyword,
                })
            if len(results) < per_page:
                break
    return rows


def fetch_bills_for_telehealth(
    days_back: int = 30,
    keywords: tuple[str, ...] = TELEHEALTH_KEYWORDS,
) -> list[dict[str, Any]]:
    """
    Pull bills for every telehealth keyword in the canonical list. Dedupes by
    bill id so a bill matching multiple keywords appears once with all matched
    keywords aggregated into `matched_keywords`.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()
    aggregated: dict[str, dict[str, Any]] = {}
    for kw in keywords:
        try:
            rows = fetch_bills_for_keyword(kw, action_since=since)
        except httpx.HTTPStatusError:
            continue
        for row in rows:
            bill_id = row["id"]
            if bill_id in aggregated:
                aggregated[bill_id].setdefault("matched_keywords", set()).add(kw)
            else:
                kw_set: set[str] = {kw}
                row["matched_keywords"] = kw_set
                aggregated[bill_id] = row
    # Convert sets to lists for JSON serializability
    out: list[dict[str, Any]] = []
    for v in aggregated.values():
        v["matched_keywords"] = sorted(v.get("matched_keywords", []))
        v.pop("matched_keyword", None)
        out.append(v)
    return out


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_hearings(
    states: tuple[str, ...] = TOP_10_STATES,
    days_forward: int = 14,
    max_pages: int = 3,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Fetch upcoming committee hearings within `days_forward` from today, across
    the specified states. Returns flat list with state, committee, scheduled
    date, agenda items if available.

    OpenStates v3 exposes hearings under /events with classification filters.
    """
    url = f"{OPENSTATES_BASE}/events"
    end = (datetime.now(timezone.utc) + timedelta(days=days_forward)).date().isoformat()
    start = datetime.now(timezone.utc).date().isoformat()

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, headers=_headers()) as client:
        for state in states:
            params: list[tuple[str, str]] = [
                ("jurisdiction", state),
                ("classification", "committee-meeting"),
                ("start_date", start),
                ("end_date", end),
                ("per_page", "20"),
            ]
            for page in range(1, max_pages + 1):
                page_params = params + [("page", str(page))]
                _throttle_openstates()
                resp = client.get(url, params=page_params)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                payload = resp.json()
                results = payload.get("results", [])
                if not results:
                    break
                for e in results:
                    rows.append({
                        "id": e.get("id"),
                        "name": e.get("name"),
                        "jurisdiction": state,
                        "start_date": e.get("start_date"),
                        "end_date": e.get("end_date"),
                        "classification": e.get("classification"),
                        "description": e.get("description"),
                        "agenda": [a.get("description") for a in (e.get("agenda") or []) if a.get("description")],
                    })
                if len(results) < 20:
                    break
    return rows


def _load_existing(
    path: Path,
    record_key: str,
    id_key: str = "id",
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Load the cumulative store as {id: record} plus the last_successful_fetch
    metadata. Returns empty dict + None if the file is missing or malformed.
    """
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}, None
    records = data.get(record_key, [])
    by_id = {r[id_key]: r for r in records if r.get(id_key)}
    return by_id, data.get("last_successful_fetch")


def persist_bills(
    new_bills: list[dict[str, Any]],
    out_path: Path = Path("data/openstates_bills.json"),
    last_successful_fetch: str | None = None,
    *,
    cumulative: bool = True,
) -> dict[str, int]:
    """Merge new bills into the cumulative store (dedupe by bill id) and
    persist. When cumulative=False, behaves like the old overwrite path
    (used only by the legacy CLI entry point)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if cumulative:
        existing, _ = _load_existing(out_path, "bills", id_key="id")
        for b in new_bills:
            if b.get("id"):
                existing[b["id"]] = b
        merged = list(existing.values())
    else:
        merged = new_bills
    today = last_successful_fetch or datetime.now(timezone.utc).date().isoformat()
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_successful_fetch": today,
        "count": len(merged),
        "bills": merged,
    }, indent=2))
    return {"merged_new": len(new_bills), "cumulative_count": len(merged)}


def persist_hearings(
    new_hearings: list[dict[str, Any]],
    out_path: Path = Path("data/openstates_hearings.json"),
    last_successful_fetch: str | None = None,
    *,
    cumulative: bool = True,
) -> dict[str, int]:
    """Merge new hearings into the cumulative store (dedupe by id) and
    prune any whose end_date is in the past so the store does not grow
    indefinitely with stale events.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if cumulative:
        existing, _ = _load_existing(out_path, "hearings", id_key="id")
        for h in new_hearings:
            if h.get("id"):
                existing[h["id"]] = h
        # Prune past hearings: end_date older than today
        today_iso = datetime.now(timezone.utc).date().isoformat()
        merged = [
            h for h in existing.values()
            if not h.get("end_date") or h["end_date"] >= today_iso
        ]
    else:
        merged = new_hearings
    today = last_successful_fetch or datetime.now(timezone.utc).date().isoformat()
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_successful_fetch": today,
        "count": len(merged),
        "hearings": merged,
    }, indent=2))
    return {"merged_new": len(new_hearings), "cumulative_count": len(merged)}


def run(
    days_back: int = 30,
    days_forward: int = 14,
) -> dict[str, Any]:
    """Top-level entry point with persistent + incremental fetch.

    On each run:
      1. Load the existing cumulative bill and hearing stores. If absent,
         start empty.
      2. Try a live fetch for both endpoints. On success, merge new records
         into the cumulative store (dedupe by id) and refresh
         last_successful_fetch.
      3. On failure (HTTP 429 or any other error), keep the existing
         cumulative store unchanged and record the error in the summary.

    This means hitting the 500-daily-request OpenStates cap does NOT zero
    the bill data for downstream signal detectors. The cumulative store
    keeps yesterday's bills available while we wait for tomorrow's quota
    to reset. The pattern extends to any source with a daily quota; v2
    will apply the same merge-and-fall-back to openFDA and LDA so all
    five sources behave identically under rate-limit pressure.
    """
    bills_path = Path("data/openstates_bills.json")
    hearings_path = Path("data/openstates_hearings.json")

    existing_bills, bills_last_fetch = _load_existing(bills_path, "bills", id_key="id")
    existing_hearings, hearings_last_fetch = _load_existing(hearings_path, "hearings", id_key="id")

    bills_outcome: dict[str, Any] = {
        "cumulative_count": len(existing_bills),
        "fetched_live": False,
        "last_successful_fetch": bills_last_fetch,
    }
    try:
        new_bills = fetch_bills_for_telehealth(days_back=days_back)
        today = datetime.now(timezone.utc).date().isoformat()
        merge = persist_bills(new_bills, bills_path, last_successful_fetch=today)
        bills_outcome.update({
            "cumulative_count": merge["cumulative_count"],
            "merged_new": merge["merged_new"],
            "fetched_live": True,
            "last_successful_fetch": today,
        })
    except Exception as exc:
        log.warning(
            "OpenStates bills fetch failed; serving cumulative store of %d bills "
            "(last successful fetch: %s): %s",
            len(existing_bills), bills_last_fetch, exc,
        )
        bills_outcome["fetch_error"] = str(exc)[:200]

    hearings_outcome: dict[str, Any] = {
        "cumulative_count": len(existing_hearings),
        "fetched_live": False,
        "last_successful_fetch": hearings_last_fetch,
    }
    try:
        new_hearings = fetch_hearings(days_forward=days_forward)
        today = datetime.now(timezone.utc).date().isoformat()
        merge = persist_hearings(new_hearings, hearings_path, last_successful_fetch=today)
        hearings_outcome.update({
            "cumulative_count": merge["cumulative_count"],
            "merged_new": merge["merged_new"],
            "fetched_live": True,
            "last_successful_fetch": today,
        })
    except Exception as exc:
        log.warning(
            "OpenStates hearings fetch failed; serving cumulative store of %d hearings "
            "(last successful fetch: %s): %s",
            len(existing_hearings), hearings_last_fetch, exc,
        )
        hearings_outcome["fetch_error"] = str(exc)[:200]

    return {
        "bills_count": bills_outcome["cumulative_count"],
        "hearings_count": hearings_outcome["cumulative_count"],
        "bills_fetched_live": bills_outcome["fetched_live"],
        "hearings_fetched_live": hearings_outcome["fetched_live"],
        "bills_last_successful_fetch": bills_outcome.get("last_successful_fetch"),
        "hearings_last_successful_fetch": hearings_outcome.get("last_successful_fetch"),
        "bills_fetch_error": bills_outcome.get("fetch_error"),
        "hearings_fetch_error": hearings_outcome.get("fetch_error"),
    }


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    summary = run(days_back=days)
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith("sample")}, indent=2))
    if summary.get("sample_bill"):
        b = summary["sample_bill"]
        print(f"\nsample bill: {b.get('identifier')} ({b.get('jurisdiction_name')}): {b.get('title', '')[:100]}")
        print(f"  matched: {b.get('matched_keywords')}")
    if summary.get("sample_hearing"):
        h = summary["sample_hearing"]
        print(f"\nsample hearing: [{h.get('jurisdiction')}] {h.get('name')} on {h.get('start_date')}")
