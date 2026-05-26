"""
End-to-end v2 orchestrator for the Interplay GTM Signals.

Pipeline stages, in order:
  1. INGEST     fetch openFDA drug enforcement, LDA filings (top accounts), enforcement
                news (CFPB newsroom RSS + Google News RSS), OpenStates bills
                and hearings (skipped on rate-limit)
  2. DETECT     run all three signal detectors (drug enforcement cascade, rival
                co-mobilization, enforcement precursor)
  3. BLEND      composite blender produces ranked opportunities, applies
                per-AE daily caps
  4. INTERPRET  generate narrative payloads with cold first-touch and
                worked-deal revival frames (Claude Haiku 4.5 if available,
                template fallback otherwise)
  5. ROUTE      push to Slack webhook if configured, otherwise write Block
                Kit payloads to data/slack_preview.json
  6. PUBLISH    write run summary to outputs/ for the GitHub Pages site

Usage:
    python scripts/run_pipeline.py                    # full pipeline
    python scripts/run_pipeline.py --skip-ingest      # use cached data files
    python scripts/run_pipeline.py --skip-llm         # template interpreter only
    python scripts/run_pipeline.py --slack-dry-run    # never post to Slack
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._lib import (
    openfda_client,
    lda_client,
    enforcement_news_client,
    openstates_client,
    sec_edgar_client,
    signal_drug_enforcement,
    signal_co_mobilization,
    signal_enforcement_precursor,
    blender,
    interpreter,
    slack_push,
)


def stage_ingest(skip: bool = False) -> dict:
    if skip:
        return {"skipped": True}
    summary: dict = {}
    print("\n[1/6] INGEST", flush=True)

    # openFDA drug enforcement (replaces CFPB; healthcare has no consumer complaint API equivalent)
    try:
        fda_summary = openfda_client.run(days_back=365)
        summary["openfda"] = {
            "records": fda_summary["total_records"],
            "categories": fda_summary["categories_queried"],
            "spikes_detected": fda_summary["spikes_detected"],
        }
        print(f"  openFDA: {fda_summary['total_records']} drug enforcement records across {fda_summary['categories_queried']} categories")
    except Exception as e:
        summary["openfda"] = {"error": str(e)}
        print(f"  openFDA error: {e}")

    # LDA filings (fetch filings for all accounts in the watchlist)
    try:
        aliases_data = json.loads(Path("data/account_aliases.json").read_text())
        all_aliases = aliases_data["aliases"]
        filings = lda_client.fetch_recent_filings(all_aliases)
        lda_client.persist(filings)
        total = sum(len(v) for v in filings.values())
        summary["lda"] = {"accounts_queried": len(all_aliases), "total_filings": total}
        print(f"  LDA: {total} filings across {len(all_aliases)} accounts")
    except Exception as e:
        summary["lda"] = {"error": str(e)}
        print(f"  LDA error: {e}")

    # Enforcement news
    try:
        news_summary = enforcement_news_client.run()
        summary["enforcement_news"] = {
            "items": news_summary["enforcement_items_after_filter"],
            "raw_cfpb": news_summary["cfpb_items_fetched"],
            "raw_google": news_summary["google_items_fetched"],
        }
        print(f"  Enforcement news: {news_summary['enforcement_items_after_filter']} items after filter")
    except Exception as e:
        summary["enforcement_news"] = {"error": str(e)}
        print(f"  Enforcement news error: {e}")

    # OpenStates bills (allow rate-limit failures to skip)
    try:
        os_summary = openstates_client.run(days_back=30, days_forward=14)
        summary["openstates"] = {
            "bills": os_summary["bills_count"],
            "hearings": os_summary["hearings_count"],
        }
        print(f"  OpenStates: {os_summary['bills_count']} bills, {os_summary['hearings_count']} hearings")
    except Exception as e:
        summary["openstates"] = {"error": str(e)}
        print(f"  OpenStates error (may be rate-limit): {e}")

    # SEC EDGAR 10-Q / 10-K risk factor scan (public accounts only)
    try:
        sec_summary = sec_edgar_client.run()
        summary["sec_edgar"] = sec_summary
        print(f"  SEC EDGAR: {sec_summary['public_accounts_processed']} public accounts, {sec_summary['total_topic_hits']} topic hits across filings since {sec_summary['since_date']}")
    except Exception as e:
        summary["sec_edgar"] = {"error": str(e)}
        print(f"  SEC EDGAR error: {e}")

    return summary


def stage_detect() -> dict:
    print("\n[2/6] DETECT", flush=True)
    summary: dict = {}
    for name, mod in [
        ("S1_drug_enforcement_cascade", signal_drug_enforcement),
        ("S2_rival_co_mobilization", signal_co_mobilization),
        ("S3_enforcement_precursor", signal_enforcement_precursor),
    ]:
        try:
            result = mod.run()
            summary[name] = result["event_count"]
            print(f"  {name}: {result['event_count']} events")
        except Exception as e:
            summary[name] = {"error": str(e)}
            print(f"  {name} error: {e}")
            traceback.print_exc()
    return summary


def stage_blend() -> dict:
    print("\n[3/6] BLEND", flush=True)
    # apply_caps=False in v1: per-AE caps collapse every opportunity under a
    # single _unowned bucket (owner_ae fields were nulled during synthetic
    # cleanup; no real AEs exist yet). The RevOps inspection dashboard wants
    # full signal visibility, not a top-3 slice. Re-enable in v2 when HubSpot
    # writeback supplies real AE assignments.
    result = blender.run(apply_caps=False)
    print(f"  raw opportunities: {result['raw_opportunity_count']}")
    print(f"  routed (no AE cap in v1): {result['after_caps_count']}")
    return {
        "raw_opportunity_count": result["raw_opportunity_count"],
        "after_caps_count": result["after_caps_count"],
        "caps_applied": result.get("caps_applied", False),
    }


def stage_interpret(use_llm: bool = True) -> dict:
    print("\n[4/6] INTERPRET", flush=True)
    result = interpreter.run(use_llm=use_llm)
    print(f"  alerts: {result['alerts_generated']} (source: {result['narrative_source']})")
    return {
        "alerts_generated": result["alerts_generated"],
        "narrative_source": result["narrative_source"],
    }


def stage_route(dry_run: bool | None = None) -> dict:
    print("\n[5/6] ROUTE", flush=True)
    result = slack_push.run(dry_run=dry_run)
    label = "dry-run" if result["dry_run"] else "live"
    print(f"  {label}: {result['posted_count']} posted, {result['alert_count']} total")
    if result["errors"]:
        print(f"  errors: {result['errors']}")
    return result


def stage_publish(run_summary: dict) -> dict:
    print("\n[6/6] PUBLISH", flush=True)
    outputs_dir = Path("outputs")
    outputs_dir.mkdir(exist_ok=True)
    (outputs_dir / "run_summary.json").write_text(json.dumps(run_summary, indent=2))

    # Copy latest alerts to outputs/ for the Pages site
    alerts_path = Path("data/alerts.json")
    if alerts_path.exists():
        (outputs_dir / "alerts.json").write_text(alerts_path.read_text())

    # Copy accounts.json for full watchlist visual directory in dashboard
    accounts_path = Path("data/accounts.json")
    if accounts_path.exists():
        (outputs_dir / "accounts.json").write_text(accounts_path.read_text())

    # Copy watchlist_opportunities.json for below-floor visual captures in dashboard
    watchlist_opps_path = Path("data/watchlist_opportunities.json")
    if watchlist_opps_path.exists():
        (outputs_dir / "watchlist_opportunities.json").write_text(watchlist_opps_path.read_text())

    # Per-account aggregator runs here (not in generate_site.py) so any
    # invocation of run_pipeline.py leaves the dashboard-consumed rollup
    # outputs/accounts_with_signals.json fresh against the latest alerts.
    # generate_site.py stays focused on copying static assets into site/.
    try:
        from scripts._lib.aggregator import run as run_aggregator
        agg_summary = run_aggregator()
        print(f"  aggregator: {agg_summary['accounts_surfaced']} accounts surfaced, "
              f"{agg_summary['multistate_accounts']} multistate")
    except Exception as exc:
        print(f"  aggregator skipped: {exc}")

    print(f"  written: outputs/run_summary.json, outputs/alerts.json, outputs/accounts.json, outputs/watchlist_opportunities.json, outputs/accounts_with_signals.json")
    return {"published_to": str(outputs_dir)}



class Tee:
    """Stream wrapper that duplicates writes to an original stream and a file."""
    def __init__(self, original_stream, file_stream):
        self.original_stream = original_stream
        self.file_stream = file_stream

    def write(self, data):
        self.original_stream.write(data)
        self.file_stream.write(data)
        self.file_stream.flush()

    def flush(self):
        self.original_stream.flush()
        self.file_stream.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ingest", action="store_true", help="Reuse cached data files instead of refetching")
    parser.add_argument("--skip-llm", action="store_true", help="Force template-based interpreter")
    parser.add_argument("--slack-dry-run", action="store_true", help="Never post to Slack; write preview file only")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    
    # Initialize file logging in outputs/
    log_dir = Path("outputs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "pipeline.log"
    log_f = open(log_file, "a", encoding="utf-8")
    
    log_f.write(f"\n=== Pipeline Run Started: {started_at} ===\n")
    log_f.flush()
    
    # Save original streams and wrap them with Tee
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    
    sys.stdout = Tee(orig_stdout, log_f)
    sys.stderr = Tee(orig_stderr, log_f)

    try:
        summary = {"started_at": started_at, "stages": {}}

        summary["stages"]["ingest"] = stage_ingest(skip=args.skip_ingest)
        summary["stages"]["detect"] = stage_detect()

        summary["stages"]["blend"] = stage_blend()
        summary["stages"]["interpret"] = stage_interpret(use_llm=not args.skip_llm)
        summary["stages"]["route"] = stage_route(dry_run=args.slack_dry_run or None)
        summary["completed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        summary["stages"]["publish"] = stage_publish(summary)

        print(f"\n=== Run complete at {summary['completed_at']} ===")
    except Exception as e:
        traceback.print_exc()
        raise e
    finally:
        # Restore streams and close log file
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        log_f.write(f"=== Pipeline Run Finished ===\n")
        log_f.close()


if __name__ == "__main__":
    main()
