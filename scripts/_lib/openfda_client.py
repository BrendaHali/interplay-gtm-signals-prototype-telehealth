"""
openFDA Drug Enforcement client.

Source: https://api.fda.gov/drug/enforcement.json
Free, public, no authentication required.

Replaces the CFPB Consumer Complaint Database used by the payments engine.
CFPB has no equivalent for healthcare services; openFDA drug enforcement is
the closest structured public source for the telehealth ICP because:

1. Telehealth companies prescribe a defined set of drugs (GLP-1s, ADHD
   stimulants, hormones, hair-loss, mental health). Enforcement actions on
   these drug categories indicate regulatory pressure on the underlying
   prescribing model.

2. Compounding pharmacy enforcement is particularly relevant. Most
   telehealth GLP-1 supply (Hims, Ro, LifeMD, Calibrate, Noom, WW Sequence)
   currently runs through compounding pharmacies. FDA enforcement on
   compounded semaglutide or tirzepatide directly threatens those models.

3. Drug enforcement spikes precede state legislative attention. State
   pharmacy boards and legislatures respond to compounding pharmacy
   enforcement with new prescribing rules.

Outputs land at data/drug_enforcement.json for downstream Signal 1 detection.
"""
from __future__ import annotations

import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts._lib._utils import get_logger, retry_on_transient

log = get_logger(__name__)

OPENFDA_BASE = "https://api.fda.gov/drug/enforcement.json"


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_enforcement_for_drug(
    drug_name: str,
    days_back: int = 365,
    limit: int = 100,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Fetch openFDA drug enforcement records mentioning `drug_name` in product
    description, recall reason, or recalling firm name. Returns the parsed
    record list. openFDA's search syntax uses field:"value" with URL encoding
    handled by httpx.
    """
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)
    # openFDA uses YYYYMMDD format for date fields
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    query = f'product_description:"{drug_name}" AND report_date:[{start_str} TO {end_str}]'
    params: dict[str, Any] = {
        "search": query,
        "limit": min(limit, 1000),
    }
    # Authenticated requests raise the openFDA rate limit from 40 / hour and
    # 1,000 / day per IP to 120 / minute and 120,000 / day. Critical on shared
    # GitHub Actions runner IPs where unauthenticated quota is contended.
    api_key = os.environ.get("API_DATA_GOV_KEY") or os.environ.get("OPENFDA_API_KEY")
    if api_key:
        params["api_key"] = api_key
    with httpx.Client(timeout=timeout, headers={"User-Agent": "interplay-gtm-signals/0.1"}) as client:
        resp = client.get(OPENFDA_BASE, params=params)
        if resp.status_code == 404:
            # openFDA returns 404 when no results match
            return []
        resp.raise_for_status()
        payload = resp.json()
    results = payload.get("results", [])
    return results


def fetch_all_telehealth_drugs(
    drug_categories: dict[str, list[str]],
    days_back: int = 365,
) -> dict[str, list[dict[str, Any]]]:
    """
    Pull enforcement records for every drug in every category. Returns dict
    keyed by category name, value is the combined list across drugs in that
    category (each record tagged with the matching drug name).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for category, drugs in drug_categories.items():
        category_records: list[dict[str, Any]] = []
        for drug in drugs:
            try:
                records = fetch_enforcement_for_drug(drug, days_back=days_back)
                for r in records:
                    r["_matched_drug"] = drug
                    r["_drug_category"] = category
                category_records.extend(records)
                log.info(f"openFDA {drug}: {len(records)} records")
            except httpx.HTTPError as exc:
                log.warning(f"openFDA fetch failed for {drug}: {exc}")
                continue
        # Dedupe by recall_number
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for r in category_records:
            rid = r.get("recall_number") or r.get("event_id") or ""
            if rid and rid not in seen:
                seen.add(rid)
                deduped.append(r)
        out[category] = deduped
    return out


def compute_weekly_volumes(
    records_by_category: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], int]:
    """
    Aggregate enforcement records into (category, week_starting_monday) -> count.
    Ensures a contiguous time-series by populating zero counts for all calendar
    weeks in the 365-day tracking window, preventing baseline distortions.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for category, records in records_by_category.items():
        for r in records:
            date_str = r.get("report_date") or r.get("recall_initiation_date") or ""
            if not date_str or len(date_str) < 8:
                continue
            try:
                dt = datetime.strptime(date_str[:8], "%Y%m%d")
            except ValueError:
                continue
            week_start = dt - timedelta(days=dt.weekday())
            counts[(category, week_start.date().isoformat())] += 1

    # Fill in gaps with 0 for all Mondays in the 365-day range
    if not records_by_category:
        return dict(counts)
    
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=365)
    current = start_date + timedelta(days=(7 - start_date.weekday()) % 7)
    mondays: list[str] = []
    while current <= end_date:
        mondays.append(current.isoformat())
        current += timedelta(days=7)
    
    for category in records_by_category.keys():
        for monday in mondays:
            key = (category, monday)
            if key not in counts:
                counts[key] = 0

    return dict(counts)


def detect_spikes(
    volumes: dict[tuple[str, str], int],
    baseline_weeks: int = 12,
    z_threshold: float = 2.0,
    monthly_volume_floor: int = 3,
    recent_windows: int = 4,
) -> list[dict[str, Any]]:
    """
    Detect statistically significant spikes per drug category against a trailing
    baseline. Evaluates each of the most recent `recent_windows` weeks as a
    candidate spike week (not only the very last week), so a spike that occurred
    2 or 3 weeks ago still surfaces.

    Two-test approach calibrated for sparse Poisson-like enforcement counts:
      - Z-score against baseline mean and stdev (with stdev floor at 1)
      - Poisson upper-tail probability against baseline mean as lambda

    A week qualifies as a spike when EITHER test passes the threshold. Z-score
    keeps the existing semantics; Poisson catches low-baseline drugs where
    z-score is unstable.

    Volume floor uses the four weeks preceding the candidate week so very low
    activity drugs do not generate spurious spikes.
    """
    series: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (category, week_start), count in volumes.items():
        series[category].append((week_start, count))

    spikes: list[dict[str, Any]] = []
    for category, weeks in series.items():
        weeks.sort(key=lambda x: x[0])
        if len(weeks) < baseline_weeks + recent_windows:
            continue
        # Each of the last `recent_windows` weeks is a candidate spike week.
        # Iterate newest first; emit one spike per category at most (the most
        # recent qualifying week).
        for offset in range(recent_windows):
            idx = -1 - offset
            current_week, current_count = weeks[idx]
            baseline_slice = weeks[idx - baseline_weeks: idx]
            baseline = [c for _, c in baseline_slice]
            trailing_4 = [c for _, c in weeks[idx - 4: idx]]
            trailing_4_avg = sum(trailing_4) / 4 if len(trailing_4) == 4 else 0
            if trailing_4_avg * (30.0 / 7.0) < monthly_volume_floor:
                continue
            mean = statistics.mean(baseline) if baseline else 0
            stdev = statistics.stdev(baseline) if len(baseline) > 1 and statistics.stdev(baseline) > 0 else 1
            z = (current_count - mean) / stdev
            poisson_p = _poisson_upper_tail(current_count, mean) if mean > 0 else 1.0
            z_passes = z >= z_threshold
            poisson_passes = poisson_p <= 0.05 and current_count >= mean * 1.5
            if z_passes or poisson_passes:
                spikes.append({
                    "drug_category": category,
                    "week_start": current_week,
                    "current_count": current_count,
                    "baseline_mean": round(mean, 2),
                    "baseline_stdev": round(stdev, 2),
                    "trailing_4_week_avg": round(trailing_4_avg, 2),
                    "z_score": round(z, 2),
                    "poisson_upper_tail_p": round(poisson_p, 4),
                    "weeks_ago": offset,
                })
                break
    return spikes


def _poisson_upper_tail(k: int, lam: float) -> float:
    """P(X >= k) where X ~ Poisson(lam). Closed-form sum, no scipy dependency."""
    if lam <= 0:
        return 0.0 if k > 0 else 1.0
    import math
    # P(X >= k) = 1 - P(X <= k-1)
    cumulative = 0.0
    for i in range(int(k)):
        cumulative += (lam ** i) * math.exp(-lam) / math.factorial(i)
    return max(0.0, min(1.0, 1.0 - cumulative))


def persist(
    records_by_category: dict[str, list[dict[str, Any]]],
    out_path: Path = Path("data/drug_enforcement.json"),
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": {cat: {"count": len(recs), "records": recs} for cat, recs in records_by_category.items()},
    }, indent=2))


def run(days_back: int = 365) -> dict[str, Any]:
    """
    Top-level entry point. Pulls last `days_back` days of openFDA enforcement
    for every telehealth-prescribed drug category, persists raw records, runs
    spike detection at the category level, and returns a summary.
    """
    import yaml
    config = yaml.safe_load(Path("data/scoring_config.yaml").read_text())
    drug_categories = config.get("telehealth_drug_categories", {})
    records_by_category = fetch_all_telehealth_drugs(drug_categories, days_back=days_back)
    persist(records_by_category)
    volumes = compute_weekly_volumes(records_by_category)
    spikes = detect_spikes(volumes)
    return {
        "categories_queried": len(drug_categories),
        "total_records": sum(len(v) for v in records_by_category.values()),
        "weekly_tuples": len(volumes),
        "spikes_detected": len(spikes),
        "spikes": spikes,
    }


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    summary = run(days_back=days)
    print(json.dumps({k: v for k, v in summary.items() if k != "spikes"}, indent=2))
    for s in summary["spikes"][:5]:
        print(f"  spike: {s}")
