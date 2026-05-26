"""
Slack alert push.

Reads the alerts file produced by the interpreter and posts each alert to the
configured Slack incoming webhook. If SLACK_WEBHOOK_URL is not set, writes
the formatted Slack Block Kit payloads to data/slack_preview.json instead so
the alerts are still inspectable.

Block Kit layout for each alert:
  - Header: account name, state, topic, composite score
  - Body section: narrative body
  - Side-by-side fields: cold first-touch frame and worked-deal revival frame
  - Context: signal contribution breakdown and AE owner
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def load_alerts(path: Path = Path("data/alerts.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("alerts", [])


def build_block_kit_payload(alert: dict[str, Any]) -> dict[str, Any]:
    """Build a Slack Block Kit payload from an alert."""
    narrative = alert.get("narrative", {})
    signal_scores = alert.get("signal_scores", {})
    source_events = alert.get("source_events", {})

    # Collect source-event summaries across signals
    source_lines: list[str] = []
    for sig_key in ("S1", "S2", "S3"):
        for ev in source_events.get(sig_key, [])[:1]:
            source_lines.append(f"  - {ev.get('event_summary', '')}")

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": narrative.get("headline", "Policy Signal")[:150]},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Body*\n{narrative.get('body', '')}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Cold first-touch*\n{narrative.get('cold_first_touch_frame', '')}"},
                {"type": "mrkdwn", "text": f"*Worked-deal revival*\n{narrative.get('worked_deal_revival_frame', '')}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Signal contributions*\n"
                    f"S1 drug enforcement cascade: {signal_scores.get('S1', 0):.2f} | "
                    f"S2 rival co-mob: {signal_scores.get('S2', 0):.2f} | "
                    f"S3 enforcement precursor: {signal_scores.get('S3', 0):.2f}"
                ),
            },
        },
    ]

    if source_lines:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Trigger events*\n" + "\n".join(source_lines)},
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"AE: {alert.get('account_owner_ae', 'unowned')} | engagement: {alert.get('account_engagement_status', 'unknown')} | composite: {alert.get('composite_score')}"},
        ],
    })

    return {"blocks": blocks}


def post_to_slack(payload: dict[str, Any], webhook_url: str, timeout: float = 15.0) -> int:
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(webhook_url, json=payload)
    return resp.status_code


def run(dry_run: bool | None = None) -> dict[str, Any]:
    """
    Post all alerts to Slack. If SLACK_WEBHOOK_URL is unset or dry_run=True,
    writes the Block Kit payloads to data/slack_preview.json instead.
    """
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if dry_run is None:
        dry_run = not bool(webhook)

    alerts = load_alerts()
    payloads = [build_block_kit_payload(a) for a in alerts]

    posted = 0
    errors: list[str] = []
    if not dry_run and webhook:
        for payload in payloads:
            try:
                status = post_to_slack(payload, webhook)
                if 200 <= status < 300:
                    posted += 1
                else:
                    errors.append(f"HTTP {status}")
            except httpx.HTTPError as e:
                errors.append(str(e))

    preview_path = Path("data/slack_preview.json")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "webhook_configured": bool(webhook),
        "alert_count": len(alerts),
        "posted_count": posted,
        "errors": errors,
        "payloads": payloads,
    }, indent=2))

    return {
        "alert_count": len(alerts),
        "posted_count": posted,
        "dry_run": dry_run,
        "errors": errors,
        "preview_written_to": str(preview_path),
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps(summary, indent=2))
