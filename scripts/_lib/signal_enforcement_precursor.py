"""
Signal 3 detector: Enforcement Precursor.

Joins regulatory enforcement events (CFPB consent orders, state AG lawsuits,
settlements, CIDs) with state legislative activity on the same subject matter
within a 14-day forward window. The precursor pattern: a high-profile
enforcement action becomes political cover for a state legislator to introduce
or advance a bill codifying the claim.

Inputs (produced by upstream client modules):
  - data/enforcement_news.json: filtered enforcement items from CFPB newsroom
    RSS and Google News RSS
  - data/openstates_bills.json: OpenStates bills matching payments keywords
  - data/openstates_hearings.json (optional): committee hearing schedules

Output:
  - data/signals_enforcement_precursor.json: scored signal events with
    provenance

If hearings data is available it takes precedence (a committee hearing is a
stronger signal than a bill introduction). If hearings are absent, bills serve
as the legislative-response proxy. If BOTH are absent (e.g. OpenStates rate
limit exhausted on the run), enforcement events still surface as
"legislative-response-pending" alerts at a 0.6x score, because federal or
state-AG enforcement against a telehealth operator is itself actionable
intelligence for the GA team regardless of legislative follow-through timing.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


# Map enforcement-news topics to OpenStates bill keywords for cross-reference.
# Telehealth ICP topic taxonomy.
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "compounded_glp1": ["compounded GLP-1", "semaglutide", "tirzepatide", "pharmacy compounding"],
    "asynchronous_prescribing": ["asynchronous prescribing", "telehealth prescribing", "in-person visit requirement"],
    "controlled_substance_telehealth": ["controlled substance telehealth", "Ryan Haight", "ADHD prescribing", "stimulant prescribing"],
    "scope_of_practice": ["scope of practice", "nurse practitioner", "physician assistant authority"],
    "telehealth_licensing": ["telehealth licensing", "Interstate Medical Licensure Compact", "IMLC", "cross-border practice"],
    "mental_health_telehealth": ["mental health telehealth", "psychology compact", "behavioral health"],
    "telehealth_general": ["telehealth", "telemedicine", "virtual care"],
}

US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire",
    "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
}


def load_enforcement_news(path: Path = Path("data/enforcement_news.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("items", [])


def load_bills(path: Path = Path("data/openstates_bills.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("bills", [])


def load_hearings(path: Path = Path("data/openstates_hearings.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("hearings", [])


def classify_topic(text: str) -> str:
    """
    Lightweight topic classifier that maps enforcement-news text to canonical
    telehealth topic keys without requiring an LLM call. Returns the first
    matching topic or 'telehealth_general' as fallback.
    """
    text_lower = text.lower()
    if any(t in text_lower for t in ("compounded glp", "semaglutide", "tirzepatide", "ozempic", "wegovy", "compounded weight loss")):
        return "compounded_glp1"
    if any(t in text_lower for t in ("asynchronous prescribing", "async prescribing", "in-person visit", "without in-person")):
        return "asynchronous_prescribing"
    if any(t in text_lower for t in ("controlled substance", "ryan haight", "adhd prescribing", "stimulant prescribing", "adderall")):
        return "controlled_substance_telehealth"
    if any(t in text_lower for t in ("scope of practice", "nurse practitioner", "physician assistant authority")):
        return "scope_of_practice"
    if any(t in text_lower for t in ("interstate medical licensure", "imlc", "cross-border telehealth", "telehealth licensing")):
        return "telehealth_licensing"
    if any(t in text_lower for t in ("mental health telehealth", "psychology compact", "behavioral health telehealth", "talkspace", "betterhelp", "cerebral")):
        return "mental_health_telehealth"
    return "telehealth_general"


def extract_states(text: str) -> list[str]:
    """
    Extract every US state name mentioned in the enforcement-news title or
    description. Returns a list of lowercase state names (deduplicated, in
    first-seen order). Multi-state coordinated AG actions are the highest-
    conviction enforcement events; the previous single-state implementation
    discarded them.
    """
    text_lower = text.lower()
    found: list[str] = []
    for s in US_STATE_NAMES:
        if re.search(rf"\b{re.escape(s)}\b", text_lower) and s not in found:
            found.append(s)
    return found


def extract_state(text: str) -> str | None:
    """
    Backwards-compatible single-state extractor: returns the first state found,
    or None when none are mentioned. Multi-state cases now return the first
    state instead of None so multi-state coordinated AG actions still surface
    for routing. Callers needing the full list should use extract_states.
    """
    states = extract_states(text)
    return states[0] if states else None


def parse_news_date(pub_date: str) -> datetime | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date)
    except (TypeError, ValueError):
        return None


def detect(
    forward_window_days: int = 14,
) -> list[dict[str, Any]]:
    """
    Cross-reference enforcement actions with state legislative activity.
    Returns scored signal events.
    """
    news = load_enforcement_news()
    bills = load_bills()
    hearings = load_hearings()

    if not news:
        return []

    use_hearings = bool(hearings)

    # Index bills/hearings by (state, normalized topic)
    legislative_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    source_label = "hearing" if use_hearings else "bill"

    if use_hearings:
        for h in hearings:
            state = h.get("jurisdiction", "").lower()
            text_blob = " ".join([
                h.get("name", "") or "",
                h.get("description", "") or "",
                " ".join(h.get("agenda", []) or []),
            ])
            topic = classify_topic(text_blob)
            legislative_index.setdefault((state, topic), []).append({
                "type": "hearing",
                "name": h.get("name"),
                "state": state,
                "date": h.get("start_date"),
                "url": h.get("id"),
            })
    else:
        for b in bills:
            state_name = (b.get("jurisdiction_name") or "").lower()
            text_blob = " ".join([
                b.get("title", "") or "",
                b.get("latest_action_description", "") or "",
                " ".join(b.get("subject", []) or []),
            ])
            topic = classify_topic(text_blob)
            state_abbr = _state_abbr(state_name)
            legislative_index.setdefault((state_abbr, topic), []).append({
                "type": "bill",
                "identifier": b.get("identifier"),
                "title": b.get("title"),
                "state": state_name,
                "date": b.get("latest_action_date"),
                "url": b.get("openstates_url"),
            })

    events: list[dict[str, Any]] = []
    for item in news:
        text_blob = " ".join([item.get("title", ""), item.get("description", "")])
        topic = classify_topic(text_blob)
        state = extract_state(text_blob)
        pub_date = parse_news_date(item.get("pub_date", ""))
        if pub_date is None:
            continue
        pub_date_only = pub_date.date()

        # Try same-state matches first, then cross-state matches
        match_keys: list[tuple[str, str]] = []
        if state:
            match_keys.append((_state_abbr(state), topic))
        # Cross-state matches across all top-10 states for the same topic
        for s in ("ca", "ny", "tx", "il", "fl", "pa", "oh", "ga", "nc", "mi"):
            if (s, topic) in legislative_index and (s, topic) not in match_keys:
                match_keys.append((s, topic))

        matched_legislative: list[dict[str, Any]] = []
        for key in match_keys:
            for entry in legislative_index.get(key, []):
                entry_date_str = entry.get("date")
                if not entry_date_str:
                    continue
                try:
                    entry_date = datetime.fromisoformat(entry_date_str).date()
                except ValueError:
                    continue
                days_after = (entry_date - pub_date_only).days
                if 0 <= days_after <= forward_window_days:
                    matched_legislative.append({**entry, "days_after_enforcement": days_after})

        subject_factor = 0.8 if topic != "telehealth_general" else 0.4
        recency_factor = _recency_decay(pub_date_only, half_life_days=14)

        if not matched_legislative:
            # Decoupled-fire path: enforcement without yet-detected legislative
            # response. Requires a non-generic topic AND a single identifiable
            # US state in the enforcement text so the alert routes to specific
            # accounts. Discount to 0.6x of the cross-referenced score.
            if topic == "telehealth_general" or not state:
                continue
            score = round(subject_factor * recency_factor * 0.6, 3)
            events.append({
                "signal_id": "S3_enforcement_precursor",
                "enforcement_title": item.get("title"),
                "enforcement_source": item.get("source"),
                "enforcement_link": item.get("link"),
                "enforcement_date": pub_date.isoformat(),
                "enforcement_categories": item.get("categories", []),
                "detected_topic": topic,
                "detected_state": state,
                "matched_legislative": [],
                "legislative_source_type": "legislative_response_pending",
                "score": score,
                "score_breakdown": {
                    "subject_factor": subject_factor,
                    "recency_factor": round(recency_factor, 3),
                    "no_legislative_response_discount": 0.6,
                },
            })
            continue

        # Same-state bonus normalizes both sides through _state_abbr so the
        # comparison works whether matched_legislative entries are tagged with
        # full state names (bills path: "new york") or 2-letter abbreviations
        # (hearings path: "ny"). The previous startswith() check silently
        # missed all hearings-path matches because "ny".startswith("new")
        # returns False.
        enforcement_abbr = _state_abbr(state) if state else ""
        same_state_bonus = 0.2 if enforcement_abbr and any(
            _state_abbr((m.get("state") or "").lower()) == enforcement_abbr
            for m in matched_legislative
        ) else 0
        score = round(min(1.0, subject_factor + same_state_bonus) * recency_factor, 3)

        events.append({
            "signal_id": "S3_enforcement_precursor",
            "enforcement_title": item.get("title"),
            "enforcement_source": item.get("source"),
            "enforcement_link": item.get("link"),
            "enforcement_date": pub_date.isoformat(),
            "enforcement_categories": item.get("categories", []),
            "detected_topic": topic,
            "detected_state": state,
            "matched_legislative": matched_legislative,
            "legislative_source_type": source_label,
            "score": score,
            "score_breakdown": {
                "subject_factor": subject_factor,
                "same_state_bonus": same_state_bonus,
                "recency_factor": round(recency_factor, 3),
            },
        })
    return events


def _state_abbr(name: str) -> str:
    mapping = {
        "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
        "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
        "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id",
        "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
        "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
        "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
        "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
        "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
        "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
        "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
        "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
        "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
        "wisconsin": "wi", "wyoming": "wy",
    }
    name_lower = name.lower().strip()
    if name_lower in mapping:
        return mapping[name_lower]
    # Already an abbreviation?
    if len(name_lower) == 2:
        return name_lower
    return name_lower[:2]


def _recency_decay(event_date, half_life_days: int = 14) -> float:
    if isinstance(event_date, datetime):
        event_date = event_date.date()
    days_ago = (datetime.now(timezone.utc).date() - event_date).days
    return 0.5 ** (max(0, days_ago) / half_life_days)


def persist(events: list[dict[str, Any]], out_path: Path = Path("data/signals_enforcement_precursor.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "signal_id": "S3_enforcement_precursor",
        "count": len(events),
        "events": events,
    }, indent=2))


def run() -> dict[str, Any]:
    events = detect()
    persist(events)
    return {
        "signal_id": "S3_enforcement_precursor",
        "event_count": len(events),
        "events": events,
    }


if __name__ == "__main__":
    summary = run()
    print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2))
    for e in summary["events"][:5]:
        print(f"\n  enforcement: {e['enforcement_title'][:90]}")
        print(f"    topic={e['detected_topic']} state={e['detected_state']} score={e['score']}")
        for m in e["matched_legislative"][:2]:
            label = m.get('identifier') or m.get('name') or 'unknown'
            print(f"    matched {m['type']}: {label} ({m['state']}) {m['days_after_enforcement']}d after")
