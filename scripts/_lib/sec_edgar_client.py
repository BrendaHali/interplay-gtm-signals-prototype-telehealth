"""
SEC EDGAR client for per-account 10-Q and 10-K risk factor detection.

Pulls the most recent 10-Q or 10-K filing for every public account in the
watchlist and uses the EDGAR full-text search API to count mentions of each
telehealth topic keyword inside those filings. The output feeds the blender
as a risk-disclosure multiplier: when an account's own filing names a topic,
opportunities on that (account, topic) pair get a small composite boost.

Sources:
  - https://www.sec.gov/files/company_tickers.json (ticker to CIK map)
  - https://data.sec.gov/submissions/CIK{padded_cik}.json (filings list)
  - https://efts.sec.gov/LATEST/search-index (full-text search)

Auth: none. SEC requires a User-Agent identifying name and contact email.
Set SEC_EDGAR_CONTACT in the environment. Rate limit: 10 req/sec, enforced
via _throttle.

Skips private accounts (ticker is null in account_profiles.json).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts._lib._utils import get_logger, retry_on_transient

log = get_logger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL_TMPL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FULLTEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

# Telehealth topic taxonomy with the search-friendly query strings used against
# EDGAR full-text search. Each query is a phrase the company's own filing
# would use when disclosing the underlying regulatory risk.
TOPIC_QUERIES: dict[str, list[str]] = {
    "compounded_glp1": ['"compounded"', '"semaglutide"', '"tirzepatide"', '"GLP-1"'],
    "asynchronous_prescribing": ['"asynchronous prescribing"', '"in-person visit"'],
    "controlled_substance_telehealth": ['"controlled substance"', '"Ryan Haight"', '"DEA"'],
    "scope_of_practice": ['"scope of practice"', '"nurse practitioner"'],
    "telehealth_licensing": ['"interstate medical licensure"', '"telehealth licensing"', '"cross-border"'],
    "mental_health_telehealth": ['"mental health parity"', '"behavioral health"'],
    "telehealth_reimbursement": ['"telehealth reimbursement"', '"Medicaid telehealth"'],
    "prescription_economics": ['"PBM"', '"pharmacy benefit manager"', '"drug pricing"'],
}

REQ_INTERVAL_SECONDS = 0.12  # buffer above SEC's 10 req/sec cap
_LAST_CALL_TS: float = 0.0


def _ua() -> str:
    """SEC requires a User-Agent identifying the requester."""
    contact = os.environ.get("SEC_EDGAR_CONTACT", "brenda@100xgood.com")
    return f"Interplay GTM Signals (telehealth) {contact}"


def _headers() -> dict[str, str]:
    return {"User-Agent": _ua(), "Accept": "application/json"}


def _throttle() -> None:
    global _LAST_CALL_TS
    elapsed = time.time() - _LAST_CALL_TS
    if elapsed < REQ_INTERVAL_SECONDS:
        time.sleep(REQ_INTERVAL_SECONDS - elapsed)
    _LAST_CALL_TS = time.time()


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_ticker_to_cik() -> dict[str, str]:
    """Returns a dict mapping uppercase ticker symbol to 10-digit padded CIK."""
    _throttle()
    with httpx.Client(timeout=30.0, headers=_headers()) as client:
        resp = client.get(SEC_TICKERS_URL)
        resp.raise_for_status()
        data = resp.json()
    return {
        str(v["ticker"]).upper(): str(v["cik_str"]).zfill(10)
        for v in data.values()
    }


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_latest_filing(cik: str, forms: tuple[str, ...] = ("10-Q", "10-K")) -> dict[str, Any] | None:
    """Fetches the most recent filing for `cik` matching one of `forms`."""
    _throttle()
    url = SEC_SUBMISSIONS_URL_TMPL.format(cik=cik)
    with httpx.Client(timeout=30.0, headers=_headers()) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    recent = data.get("filings", {}).get("recent", {})
    if not recent.get("form"):
        return None
    forms_set = set(forms)
    for i, form in enumerate(recent["form"]):
        if form in forms_set:
            return {
                "form": form,
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "primary_document": recent["primaryDocument"][i],
            }
    return None


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def search_filing_for_phrase(cik: str, phrase: str, since_date: str, forms: tuple[str, ...] = ("10-Q", "10-K")) -> int:
    """Count EDGAR full-text-search hits for a phrase scoped to one CIK + recent filings."""
    _throttle()
    params: list[tuple[str, str]] = [
        ("q", phrase),
        ("forms", ",".join(forms)),
        ("ciks", cik),
        ("dateRange", "custom"),
        ("startdt", since_date),
        ("enddt", datetime.now(timezone.utc).date().isoformat()),
    ]
    with httpx.Client(timeout=30.0, headers=_headers()) as client:
        resp = client.get(SEC_FULLTEXT_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    return int(data.get("hits", {}).get("total", {}).get("value", 0))


def fetch_risk_disclosures(profiles_path: Path = Path("data/account_profiles.json"), since_days: int = 540) -> dict[str, Any]:
    """
    For every public account in `profiles_path`, fetch:
      - CIK (via SEC ticker map)
      - Most recent 10-Q or 10-K filing metadata
      - Per-topic full-text-search hit counts inside that filing window

    Returns a dict keyed by account_id with the per-account topic exposure map.
    """
    profiles = json.loads(profiles_path.read_text()).get("profiles", {})
    public_accounts = [
        (aid, p) for aid, p in profiles.items()
        if p.get("public") and p.get("ticker")
    ]

    if not public_accounts:
        return {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "accounts": {}}

    log.info(f"resolving CIKs for {len(public_accounts)} public accounts")
    ticker_to_cik = fetch_ticker_to_cik()

    since_date = (datetime.now(timezone.utc).date()
                  .replace(year=datetime.now(timezone.utc).year - 1)).isoformat()
    if since_days != 540:
        from datetime import timedelta
        since_date = (datetime.now(timezone.utc).date() - timedelta(days=since_days)).isoformat()

    out_accounts: dict[str, Any] = {}
    for acc_id, profile in public_accounts:
        ticker = profile["ticker"].upper()
        cik = ticker_to_cik.get(ticker)
        if not cik:
            log.warning(f"{acc_id} ({ticker}): CIK not found in SEC ticker map")
            out_accounts[acc_id] = {
                "ticker": ticker, "cik": None,
                "latest_filing": None, "topic_hits": {},
                "error": "cik_not_found",
            }
            continue

        try:
            latest = fetch_latest_filing(cik)
        except Exception as exc:
            log.warning(f"{acc_id} ({ticker}): filings list failed: {exc}")
            out_accounts[acc_id] = {
                "ticker": ticker, "cik": cik,
                "latest_filing": None, "topic_hits": {},
                "error": f"filings_list_failed: {exc}",
            }
            continue

        topic_hits: dict[str, int] = {}
        for topic, queries in TOPIC_QUERIES.items():
            hits = 0
            for q in queries:
                try:
                    hits += search_filing_for_phrase(cik, q, since_date)
                except Exception as exc:
                    log.warning(f"{acc_id} ({ticker}) topic={topic} query={q}: search failed: {exc}")
            topic_hits[topic] = hits

        out_accounts[acc_id] = {
            "ticker": ticker,
            "cik": cik,
            "latest_filing": latest,
            "topic_hits": topic_hits,
        }
        log.info(f"{acc_id} ({ticker}): latest={latest.get('form') if latest else None} {latest.get('filing_date') if latest else None} hits={ {k: v for k, v in topic_hits.items() if v > 0} }")

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since_date": since_date,
        "accounts": out_accounts,
    }


def persist(data: dict[str, Any], out_path: Path = Path("data/sec_filings.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))


def run() -> dict[str, Any]:
    data = fetch_risk_disclosures()
    persist(data)
    accounts = data.get("accounts", {})
    total_hits = sum(sum(a.get("topic_hits", {}).values()) for a in accounts.values())
    return {
        "public_accounts_processed": len(accounts),
        "total_topic_hits": total_hits,
        "since_date": data.get("since_date"),
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
