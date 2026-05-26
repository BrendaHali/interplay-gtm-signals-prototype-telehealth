"""
Source verification script for the telehealth ICP.

Runs live queries against every data source the pipeline depends on and reports
current volume counts. Reviewers can execute this script to confirm the
pipeline's data plane works at the moment of evaluation, independent of any
volume claims in the README.

Usage:
    python scripts/verify_sources.py            # default window, all sources
    python scripts/verify_sources.py --json     # JSON output only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._lib.enforcement_news_client import (
    fetch_cfpb_newsroom,
    fetch_google_news,
    filter_enforcement_items,
)


TOP_10_STATES = ("CA", "NY", "TX", "IL", "FL", "PA", "OH", "GA", "NC", "MI")


def verify_openfda() -> dict:
    """openFDA drug enforcement for telehealth-prescribed drug categories."""
    test_drugs = {"glp1": "semaglutide", "adhd": "methylphenidate", "hormones": "testosterone"}
    per_drug = {}
    total = 0
    try:
        for category, drug in test_drugs.items():
            resp = httpx.get(
                "https://api.fda.gov/drug/enforcement.json",
                params={"search": f'product_description:"{drug}"', "limit": 1},
                timeout=20,
                headers={"User-Agent": "interplay-gtm-signals/0.1"},
            )
            if resp.status_code == 200:
                count = resp.json().get("meta", {}).get("results", {}).get("total", 0)
                per_drug[f"{category} ({drug})"] = count
                total += count
            elif resp.status_code == 404:
                per_drug[f"{category} ({drug})"] = 0
        return {
            "source": "openFDA Drug Enforcement",
            "test_drug_count": len(test_drugs),
            "total_records_sample": total,
            "per_drug_sample": per_drug,
            "status": "ok",
        }
    except Exception as exc:
        return {"source": "openFDA Drug Enforcement", "status": "error", "error": str(exc)}


def verify_openstates(days_back: int) -> dict:
    """OpenStates bills filtered to telehealth keywords across top 10 states."""
    key = os.environ.get("OPENSTATES_API_KEY")
    if not key:
        return {"source": "OpenStates", "status": "no_api_key"}
    try:
        from datetime import timedelta
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days_back)
        url = "https://v3.openstates.org/bills"
        with httpx.Client(timeout=30, headers={"X-API-KEY": key}) as client:
            telehealth_terms = ["telehealth", "telemedicine", "prescribing", "scope of practice", "Interstate Medical Licensure Compact", "compounded"]
            total = 0
            per_term: dict[str, int] = {}
            for term in telehealth_terms:
                params = [
                    ("q", term),
                    ("action_since", start.isoformat()),
                    ("per_page", "1"),
                    ("page", "1"),
                ]
                for state in TOP_10_STATES:
                    params.append(("jurisdiction", state.lower()))
                resp = client.get(url, params=params)
                resp.raise_for_status()
                count = resp.json().get("pagination", {}).get("total_items", 0)
                per_term[term] = count
                total += count
        return {
            "source": "OpenStates",
            "window_days": days_back,
            "total_bills_across_queries": total,
            "per_term": per_term,
            "status": "ok",
        }
    except Exception as exc:
        return {"source": "OpenStates", "status": "error", "error": str(exc)}


def verify_lda() -> dict:
    """Sample LDA filings for a telehealth account."""
    try:
        with httpx.Client(timeout=30, headers={"Accept": "application/json"}) as client:
            resp = client.get(
                "https://lda.senate.gov/api/v1/filings/",
                params={"client_name": "teladoc", "filing_year": datetime.now(timezone.utc).year},
            )
            resp.raise_for_status()
            payload = resp.json()
        return {
            "source": "LDA Senate Lobbying Disclosures",
            "test_client": "teladoc (current year)",
            "filing_count": payload.get("count", 0),
            "status": "ok",
        }
    except Exception as exc:
        return {"source": "LDA Senate Lobbying Disclosures", "status": "error", "error": str(exc)}


def verify_sec_edgar() -> dict:
    """SEC EDGAR ticker map + filings index + full-text search.

    Lightweight probe: confirm the ticker map and a single account's most-recent
    10-Q are reachable, and that the full-text search endpoint returns a hit
    count. Uses Hims & Hers (HIMS) as the test account because its 10-Q
    references the entire telehealth topic taxonomy in volume.
    """
    try:
        from scripts._lib.sec_edgar_client import (
            fetch_ticker_to_cik,
            fetch_latest_filing,
            search_filing_for_phrase,
        )
        from datetime import timedelta as _td
        ticker_map = fetch_ticker_to_cik()
        if "HIMS" not in ticker_map:
            return {
                "source": "SEC EDGAR full-text search + filings index",
                "status": "error",
                "error": "HIMS ticker not in SEC ticker map",
            }
        hims_cik = ticker_map["HIMS"]
        latest = fetch_latest_filing(hims_cik)
        since_date = (datetime.now(timezone.utc).date() - _td(days=540)).isoformat()
        compounded_hits = search_filing_for_phrase(hims_cik, '"compounded"', since_date)
        return {
            "source": "SEC EDGAR full-text search + filings index",
            "ticker_map_size": len(ticker_map),
            "test_account": f"HIMS / CIK {hims_cik}",
            "latest_filing": (
                f"{latest['form']} {latest['filing_date']}"
                if latest else "none-found"
            ),
            "compounded_keyword_hits_540d": compounded_hits,
            "status": "ok",
        }
    except Exception as exc:
        return {"source": "SEC EDGAR full-text search + filings index", "status": "error", "error": str(exc)}


def verify_enforcement_news() -> dict:
    """CFPB newsroom RSS plus Google News telehealth-scoped queries."""
    try:
        cfpb = fetch_cfpb_newsroom()
        queries = [
            '"attorney general" (sued OR settled OR enforcement OR fines) (telehealth OR telemedicine OR prescribing OR pharmacy OR compounded) when:90d',
            'FTC (settles OR sues OR consent order) (telehealth OR Hims OR Cerebral OR Done OR GoodRx) when:90d',
            'FDA warning letter compounded (semaglutide OR tirzepatide OR GLP-1) when:90d',
            'DEA enforcement telehealth (Adderall OR controlled substance) when:90d',
        ]
        google_total = 0
        for q in queries:
            try:
                items = fetch_google_news(q)
                google_total += len(items)
            except httpx.HTTPError:
                pass
        # filter the combined CFPB + sample Google query
        sample_google = []
        try:
            sample_google = fetch_google_news(queries[0])
        except httpx.HTTPError:
            pass
        filtered = filter_enforcement_items(cfpb + sample_google)
        return {
            "source": "Enforcement News (CFPB newsroom RSS plus Google News telehealth-scoped)",
            "cfpb_items": len(cfpb),
            "google_items_total_across_4_queries": google_total,
            "after_action_verb_filter_sample": len(filtered),
            "sample_titles": [item["title"][:120] for item in filtered[:5]],
            "status": "ok",
        }
    except Exception as exc:
        return {"source": "Enforcement News", "status": "error", "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_days": args.days,
        "sources": [
            verify_openfda(),
            verify_openstates(args.days),
            verify_lda(),
            verify_enforcement_news(),
            verify_sec_edgar(),
        ],
    }

    if args.json:
        print(json.dumps(results, indent=2))
        return

    print(f"Source verification at {results['generated_at']}")
    print(f"Window: last {args.days} days where applicable")
    print()
    for src in results["sources"]:
        status = src.pop("status")
        name = src.pop("source")
        if status == "ok":
            print(f"[OK]    {name}")
        elif status in ("no_api_key", "no_contact_email"):
            print(f"[SKIP]  {name} ({status})")
        else:
            print(f"[ERROR] {name}: {src.get('error')}")
        for k, v in src.items():
            print(f"          {k}: {v}")
        print()


if __name__ == "__main__":
    main()
