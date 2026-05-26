"""
LDA Senate Lobbying Disclosures client.

Source: https://lda.senate.gov/api/v1/filings/
Free, public, no authentication. Returns paginated JSON.

LDA filings cover federal-level lobbying activity. State lobbying registries
were considered for Signal 2 and deferred to a post-launch addition because of
schema variance across states (Cal-Access alone is a multi-day parser project).
Federal LDA captures the rival-coordination pattern because the same lobbying
firms file federal disclosures even when working state-level issues, and major
telehealth operators retain federal counsel that mirrors state-level activity.

Reporting cadence: LDA filings are quarterly (Q1 covers Jan-Mar, Q2 covers
Apr-Jun, etc.). The rolling window for Signal 2 detection is 90 days, which
matches the cadence rather than fighting it. The signal is a strategic
alignment marker, not a real-time alert.

Entity name normalization: LDA filings use inconsistent legal-entity strings.
A canonical-ID resolver loads data/account_aliases.json and matches
case-insensitively against any alias.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts._lib._utils import get_logger, retry_on_transient

LDA_BASE = "https://lda.senate.gov/api/v1/filings/"

# Standard issue codes are mapped dynamically via keywords in extract_topics downstream.


def load_aliases(path: Path = Path("data/account_aliases.json")) -> dict[str, str]:
    """
    Returns a dict mapping each alias (lowercased) to its canonical account_id.
    Reverse-index of the account_aliases.json structure for fast lookup.
    """
    data = json.loads(path.read_text())
    reverse: dict[str, str] = {}
    for account_id, aliases in data.get("aliases", {}).items():
        for alias in aliases:
            reverse[alias.lower().strip()] = account_id
    return reverse


def fetch_filings_for_client(
    client_name: str,
    year: int,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Fetch LDA filings for a single client name in a given year.
    Returns the full list across paginated responses.
    """
    params: dict[str, Any] = {
        "client_name": client_name,
        "filing_year": year,
    }
    results: list[dict[str, Any]] = []
    url: str | None = LDA_BASE

    # We define a helper that uses the retry_on_transient decorator
    @retry_on_transient(max_attempts=3, initial_backoff=2.0)
    def _fetch_page(target_url: str, query_params: dict[str, Any] | None) -> dict[str, Any]:
        with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
            resp = client.get(target_url, params=query_params)
            resp.raise_for_status()
            return resp.json()

    first = True
    while url:
        try:
            payload = _fetch_page(url, params if first else None)
            results.extend(payload.get("results", []))
            url = payload.get("next")
        except httpx.HTTPStatusError as exc:
            logger = get_logger("lda_client")
            logger.error(f"HTTPStatusError fetching filings for client '{client_name}': {exc.response.status_code}")
            break
        except Exception as exc:
            logger = get_logger("lda_client")
            logger.error(f"Unexpected error fetching filings for client '{client_name}': {exc}")
            break
        first = False
    return results


def fetch_recent_filings(
    account_aliases: dict[str, list[str]],
    periods: list[Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, list[dict[str, Any]]]:
    """
    Pull recent filings for every account in the watchlist across its alias set.
    Returns a dict keyed by canonical account_id, each value a list of filing rows.
    """
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    now = datetime.now(timezone.utc)
    current_year = now.year
    years = [current_year, current_year - 1]

    for account_id, aliases in account_aliases.items():
        seen_uuids: set[str] = set()
        for alias in aliases:
            for year in years:
                try:
                    rows = fetch_filings_for_client(alias, year, timeout=timeout)
                except Exception:
                    continue
                for row in rows:
                    uuid = row.get("filing_uuid")
                    if uuid and uuid not in seen_uuids:
                        seen_uuids.add(uuid)
                        by_account[account_id].append(row)
                # Polite courtesy delay to prevent rate limits
                time.sleep(0.5)
    return dict(by_account)


def extract_topics(filing: dict[str, Any]) -> set[str]:
    """
    Pull the set of general issue codes and detailed topics from a filing.
    Returns lowercased strings for case-insensitive matching downstream.
    """
    topics: set[str] = set()
    for activity in filing.get("lobbying_activities", []) or []:
        code = activity.get("general_issue_code_display")
        if code:
            topics.add(code.lower())
        desc = activity.get("description")
        if desc:
            topics.add(desc.lower()[:200])
    return topics


_TOPIC_PATTERNS: list[tuple[str, list[str]]] = [
    ("compounded_glp1", [
        r"compounded glp", r"semaglutide", r"tirzepatide", r"pharmacy compounding",
        r"\bglp[- ]?1\b", r"\bpharmacy\b", r"\bpha\b",
    ]),
    ("asynchronous_prescribing", [
        r"\basynchronous\b", r"async prescribing", r"in-person visit",
    ]),
    ("controlled_substance_telehealth", [
        r"controlled substance", r"ryan haight", r"\badhd\b", r"\bstimulant\b",
        r"\badderall\b", r"\bmethylphenidate\b",
    ]),
    ("scope_of_practice", [
        r"scope of practice", r"nurse practitioner", r"physician assistant",
    ]),
    ("telehealth_licensing", [
        r"\blicensure\b", r"\bimlc\b", r"cross-border", r"\bcompact\b",
    ]),
    ("mental_health_telehealth", [
        r"mental health", r"\bpsychology\b", r"\bbehavioral\b",
    ]),
    ("telehealth_reimbursement", [
        r"\bmedicaid\b", r"\breimbursement\b", r"\bparity\b",
    ]),
    ("prescription_economics", [
        r"\bpbm\b", r"drug pricing", r"\bbanking\b", r"financial institutions",
        r"\btaxation\b",
    ]),
    ("telehealth_general", [
        r"health issues", r"\bhcr\b", r"\btelehealth\b", r"\btelemedicine\b",
        r"virtual care", r"\bprescribing\b", r"\bclinical\b",
    ]),
]


def _normalize_topic(text: str) -> str | None:
    """Map free-form lobbying text to a canonical telehealth topic key.

    Uses word-boundary regex matching to avoid false positives from naive
    substring tests (e.g. 'ban' matching 'urban', 'pha' matching 'alphabet').
    LDA general_issue_code_display values such as 'PHA' or 'HCR' arrive as
    three-letter all-caps tokens; lowercased they match the explicit short
    patterns above as standalone tokens, not as embedded trigrams.
    """
    if not text:
        return None
    t = text.lower()
    for topic, patterns in _TOPIC_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, t):
                return topic
    return None


def detect_co_mobilization(
    filings_by_account: dict[str, list[dict[str, Any]]],
    competitor_pairs: dict[str, Any],
    window_days: int = 90,
) -> list[dict[str, Any]]:
    """
    Detect Signal 2 firings: two or more named competitors filing on the
    same issue within the rolling window.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).date()
    events: list[dict[str, Any]] = []

    for pair_set in competitor_pairs.get("pairs", []):
        members = pair_set.get("members", [])
        # topic -> {account_id -> [filing_uuid, ...]}
        topic_index: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for account_id in members:
            for filing in filings_by_account.get(account_id, []):
                posted = filing.get("dt_posted")
                if posted:
                    try:
                        posted_date = datetime.fromisoformat(posted.replace("Z", "+00:00")).date()
                        if posted_date < cutoff:
                            continue
                    except ValueError:
                        pass
                topics = extract_topics(filing)
                for topic in topics:
                    canonical = _normalize_topic(topic)
                    if canonical:
                        topic_index[canonical][account_id].append(filing.get("filing_uuid", ""))

        for topic, account_map in topic_index.items():
            if len(account_map) >= 2:
                events.append({
                    "set_id": pair_set.get("set_id"),
                    "set_label": pair_set.get("label"),
                    "topic": topic,
                    "member_accounts": list(account_map.keys()),
                    "filing_uuids": {acc: uuids for acc, uuids in account_map.items()},
                    "score": _score_event(len(account_map), len(members)),
                })
    return events


def _score_event(triggered_count: int, set_size: int) -> float:
    """
    Threshold-style scoring: a single competitor firing is not a co-mobilization
    signal (returns 0.0). Two or more competitors firing is the signal, with
    score scaling from 0.85 for two up to 1.0 for four or more. Ratio-style
    scoring penalized sets that were correctly broad (Card networks = 4 members)
    when only two actually filed on the issue in a given window; the threshold
    approach captures the real intent of the signal.
    """
    if triggered_count < 2:
        return 0.0
    return min(1.0, 0.85 + 0.05 * (triggered_count - 2))


def persist(
    filings_by_account: dict[str, list[dict[str, Any]]],
    out_path: Path = Path("data/lda_registrations.json"),
) -> None:
    """Write the per-account filing list to disk."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accounts": {
            acc: {
                "filing_count": len(rows),
                "filings": rows,
            }
            for acc, rows in filings_by_account.items()
        },
    }
    out_path.write_text(json.dumps(summary, indent=2))


def run(
    aliases_path: Path = Path("data/account_aliases.json"),
    competitor_pairs_path: Path = Path("data/competitor_pairs.json"),
    out_path: Path = Path("data/lda_registrations.json"),
) -> dict[str, Any]:
    """
    Top-level entry point. Pulls recent filings for every account in the alias
    list, persists them, runs Signal 2 detection against competitor pairs, and
    returns a summary dict.
    """
    aliases_data = json.loads(aliases_path.read_text())
    account_aliases: dict[str, list[str]] = aliases_data.get("aliases", {})
    competitor_pairs: dict[str, Any] = json.loads(competitor_pairs_path.read_text())

    filings_by_account = fetch_recent_filings(account_aliases)
    persist(filings_by_account, out_path)
    events = detect_co_mobilization(filings_by_account, competitor_pairs)

    total_filings = sum(len(v) for v in filings_by_account.values())
    accounts_with_filings = sum(1 for v in filings_by_account.values() if v)
    return {
        "accounts_queried": len(account_aliases),
        "accounts_with_filings": accounts_with_filings,
        "total_filings": total_filings,
        "co_mobilization_events": len(events),
        "events": events,
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    print(f"\nfirst 3 events:")
    for e in summary["events"][:3]:
        print(f"  {e}")
