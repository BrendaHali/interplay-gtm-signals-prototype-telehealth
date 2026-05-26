"""
Enforcement news client for Signal 3 (regulatory enforcement to legislative response).

Primary source: CFPB Newsroom RSS feed (consumerfinance.gov/about-us/newsroom/feed/).
Public, no auth, tagged with categories that allow precise filtering for enforcement
items on payments-relevant topics.

Supplementary source: Google News RSS, scoped to state-AG plus fintech keywords.
Used as a backup channel for state-level AG actions that do not appear in the CFPB
feed. Verification in May 2026 found Google News RSS returns sparse results for AG
queries, so the CFPB feed carries the primary detection weight.

Two sources NOT used and the reasons why:
  - NMLS Consumer Access (nmlsconsumeraccess.org) for money transmitter licensing:
    Cloudflare bot protection returns 403 to all automated requests.
  - NAAG news feed (naag.org/news): Cloudflare bot protection returns 403.

Both could be added with browser-automation scrapers in a future iteration.

The detector relies on a Python regex pre-filter that requires action-oriented
language (sued, settled, restitution, enforcement, investigation, consent order,
civil investigative demand) before any item passes downstream to the LLM.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from scripts._lib._utils import retry_on_transient

CFPB_NEWSROOM_RSS = "https://www.consumerfinance.gov/about-us/newsroom/feed/"
GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search"


def _polite_headers() -> dict[str, str]:
    """
    CFPB newsroom requires a contact-email UA pattern and returns 403 to bare
    User-Agent strings. The polite-bot pattern is also accepted by SEC EDGAR
    and most other federal sources.
    """
    contact = os.environ.get("SEC_EDGAR_CONTACT", "contact@example.com")
    return {
        "User-Agent": f"interplay-gtm-signals/0.1 ({contact})",
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }

ACTION_VERB_PATTERN = re.compile(
    r"\b(sued|sues|settled|settles|settlement|restitution|enforcement|"
    r"investigation|investigates|consent order|civil investigative demand|"
    r"lawsuit|filed suit|complaint filed|cease and desist|fined|fines|"
    r"penalty|penalties|charged|charges|fraud action)\b",
    flags=re.IGNORECASE,
)

PAYMENTS_KEYWORD_PATTERN = re.compile(
    r"\b(telehealth|telemedicine|tele.?health|virtual care|asynchronous (care|prescribing)|"
    r"prescribing|prescriber|pharmacy compounding|compounded|"
    r"GLP-?1|semaglutide|tirzepatide|ozempic|wegovy|mounjaro|zepbound|"
    r"ADHD|stimulant|adderall|methylphenidate|"
    r"controlled substance|Ryan Haight|"
    r"Hims|Ro Health|LifeMD|Teladoc|Talkspace|Cerebral|Done Global|Calibrate|Noom|GoodRx|"
    r"state medical board|state pharmacy board|"
    r"Interstate Medical Licensure Compact|IMLC|scope of practice|nurse practitioner)\b",
    flags=re.IGNORECASE,
)


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_cfpb_newsroom(timeout: float = 30.0) -> list[dict[str, Any]]:
    """
    Fetch the CFPB Newsroom RSS feed and return parsed items.
    Each item carries title, link, description, pub_date, and categories.
    """
    with httpx.Client(http2=False, timeout=timeout) as client:
        resp = client.get(CFPB_NEWSROOM_RSS, headers=_polite_headers())
        resp.raise_for_status()
    return _parse_rss(resp.text, source="cfpb_newsroom")


@retry_on_transient(max_attempts=3, initial_backoff=2.0)
def fetch_google_news(query: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """
    Fetch a Google News RSS query. Use the standard query syntax with operators
    like AND, OR, quotes, site:, and when:Nd for time windows.
    """
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    with httpx.Client(http2=False, timeout=timeout) as client:
        resp = client.get(GOOGLE_NEWS_RSS_BASE, params=params, headers=_polite_headers())
        resp.raise_for_status()
    return _parse_rss(resp.text, source="google_news")


def _parse_rss(xml_text: str, source: str) -> list[dict[str, Any]]:
    """Parse RSS 2.0 XML into a normalized list of items."""
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        categories = [c.text.strip() for c in item.findall("category") if c.text]
        items.append({
            "source": source,
            "title": title,
            "link": link,
            "description": description,
            "pub_date": pub_date,
            "categories": categories,
        })
    return items


def is_enforcement_action(item: dict[str, Any]) -> bool:
    """
    Apply the regex pre-filter. Item passes only if title plus description plus
    categories contain at least one action verb and at least one payments keyword.
    """
    blob = " ".join([
        item.get("title", ""),
        item.get("description", ""),
        " ".join(item.get("categories", [])),
    ])
    if not ACTION_VERB_PATTERN.search(blob):
        return False
    if not PAYMENTS_KEYWORD_PATTERN.search(blob):
        return False
    return True


def filter_enforcement_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only items that pass the action-verb plus payments-keyword filter."""
    return [item for item in items if is_enforcement_action(item)]


def persist(items: list[dict[str, Any]], out_path: Path = Path("data/enforcement_news.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }, indent=2))


def run(
    google_queries: list[str] | None = None,
    out_path: Path = Path("data/enforcement_news.json"),
) -> dict[str, Any]:
    """
    Top-level entry point. Pulls the CFPB newsroom RSS, applies the enforcement
    filter, then pulls a small number of Google News RSS queries as supplementary
    coverage. Persists the combined filtered list.
    """
    if google_queries is None:
        google_queries = [
            '"attorney general" (sued OR settled OR enforcement OR fines) (telehealth OR telemedicine OR prescribing OR pharmacy OR compounded) when:90d',
            'FTC (settles OR sues OR consent order) (telehealth OR Hims OR Cerebral OR Done OR GoodRx) when:90d',
            'FDA warning letter compounded (semaglutide OR tirzepatide OR GLP-1) when:90d',
            'DEA enforcement telehealth (Adderall OR controlled substance) when:90d',
        ]

    cfpb_items = fetch_cfpb_newsroom()
    google_items: list[dict[str, Any]] = []
    for q in google_queries:
        try:
            google_items.extend(fetch_google_news(q))
        except httpx.HTTPError:
            continue

    all_items = cfpb_items + google_items
    enforcement = filter_enforcement_items(all_items)
    persist(enforcement, out_path)

    return {
        "cfpb_items_fetched": len(cfpb_items),
        "google_items_fetched": len(google_items),
        "enforcement_items_after_filter": len(enforcement),
        "items": enforcement,
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "items"}, indent=2))
    print(f"\nfirst 5 enforcement items:")
    for item in summary["items"][:5]:
        print(f"  [{item['source']}] {item['title']}")
        if item.get('categories'):
            print(f"      categories: {item['categories']}")
