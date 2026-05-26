"""
Interpretation layer.

Reads opportunities produced by the blender and generates narrative alert
payloads that include cold first-touch and worked-deal revival frames side
by side. Uses Claude Haiku 4.5 if ANTHROPIC_API_KEY is set; otherwise
falls back to a template-based generator that produces structured payloads
without an LLM call.

The template fallback exists so the pipeline always produces inspectable
output, with or without API credentials. Production runs would always use
the LLM path for richer narrative quality.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_account_profiles(path: Path = Path("data/account_profiles.json")) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text()).get("profiles", {})


def load_opportunities(path: Path = Path("data/opportunities.json")) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("opportunities", [])


def _template_narrative(opp: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    """Template-based narrative used when Anthropic API is unavailable."""
    account = opp["account_name"]
    state = opp["state"].upper()
    topic = opp["topic"].replace("_", " ").title()
    composite = opp["composite_score"]
    signals_fired = opp["signals_fired"]

    # Pull the most informative source event for the body
    primary_event_summary = ""
    for sig_key in ("S3", "S1", "S2"):
        events = opp["source_events"].get(sig_key, [])
        if events:
            primary_event_summary = events[0]["event_summary"]
            break

    signal_breakdown = ", ".join([
        f"S1 (drug enforcement cascade)={opp['signal_scores']['S1']:.2f}",
        f"S2 (rival co-mob)={opp['signal_scores']['S2']:.2f}",
        f"S3 (enforcement precursor)={opp['signal_scores']['S3']:.2f}",
    ])

    risks = ", ".join(profile.get("named_disclosed_risks", [])[:3])
    segment = profile.get("segment", "")
    is_public = profile.get("public", False)
    ticker = profile.get("ticker") or "private"
    if is_public and risks:
        risk_sentence = f"{account}'s 10-K names {risks} as material regulatory risks. "
    elif risks:
        risk_sentence = (
            f"Industry-standard regulatory exposures for this segment include {risks}. "
        )
    else:
        risk_sentence = ""

    headline = f"{account} ({ticker}) | {state} | {topic} | composite {composite}"

    body = (
        f"{signals_fired} of 3 signals converged on this opportunity. {signal_breakdown}. "
        f"Trigger: {primary_event_summary}. "
        f"{risk_sentence}"
        f"{account} operates in {state} as a {segment.replace('_', ' ')} account."
    )

    cold_frame = (
        f"Subject: {account} / {state} {topic} regulatory alert\n\n"
        f"Dear Public Policy Leadership,\n\n"
        f"My name is Brenda Hali. I am reaching out because our GTM intelligence signals just converged on {account}'s {topic} exposure in {state} (composite score: {composite:.2f}). "
        f"Specifically, we detected: {primary_event_summary}. "
        f"{risk_sentence}We mapped this state-specific trigger to your footprint, and most corporate GA teams face a 30 to 60 day radar gap on this shift. "
        f"Do you have 10 minutes this week for a call to compare our statehouse telemetry against your compliance roadmap?\n\n"
        f"Best,\n"
        f"Brenda Hali"
    )

    worked_deal_frame = (
        f"BDR: Hi there, this is Brenda Hali. I am following up on our prior discussion regarding {account}'s {topic} exposure in {state}. "
        f"Our statehouse policy radar just flagged {primary_event_summary}, which directly impacts your regulatory footprint.\n\n"
        f"Prospect: Thanks, we are already tracking local bills through generalist services.\n\n"
        f"BDR Pivot: Understood. However, generalist trackers don't catch the FDA spike co-mobilization window before the bill is filed. "
        f"I wanted to share our localized statehouse impact assessment. Do you have 10 minutes on Thursday to sync?"
    )

    return {
        "headline": headline,
        "body": body,
        "cold_first_touch_frame": cold_frame,
        "worked_deal_revival_frame": worked_deal_frame,
    }


def _llm_narrative(opp: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    """Claude Haiku 4.5 narrative path. Falls back to template on any error."""
    try:
        import anthropic
    except ImportError:
        return _template_narrative(opp, profile)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _template_narrative(opp, profile)

    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You write alert payloads for an enterprise sales rep selling state-policy intelligence to multistate telehealth operators. "
        "Voice: institutional, third-person, no staccato dramatic chains, no fake contrast (no 'not X, it is Y'), no intensifiers. "
        "Sentences 15 to 25 words. Output as strict JSON with exactly these four keys, "
        "nothing else: headline, body, cold_first_touch_frame, worked_deal_revival_frame.\n\n"
        "Voice rules (strict):\n"
        "- No em dashes. Use commas, colons, or periods instead.\n"
        "- Banned words: actually, honestly, leverage, unlock, supercharge, empower, seamless, robust, elevate. Use plain alternatives.\n"
        "- No sycophantic openers ('Great question', 'Absolutely', 'I'll happily'). Lead with the substance.\n\n"
        "PROSPECT TEAM REFERENCES: when describing the prospect's internal policy or "
        "regulatory function, use 'government affairs team', 'regulatory affairs lead', "
        "'public policy head', 'GA team', or 'compliance leadership'. Do not use the buyer's "
        "or any vendor's brand name. Do not name the policy-intelligence platform itself in "
        "any form. Stick to functional role descriptions.\n\n"
        "HEADLINE: STRICT 10 word maximum. Count the words before returning. Title Case. "
        "Format: '[Account]: [Topic in 2 to 3 words] [Verb] [State or Geography] [Trigger in 2 to 3 words]'. "
        "Examples (each exactly 10 words or fewer): "
        "'Coinbase: NY AG Crypto Yield Settlement Tightens Custody Disclosure Standards' (10 words). "
        "'Hims: California Pharmacy Board Targets Compounded GLP-1 Asynchronous Prescribing' (10 words). "
        "If a draft headline exceeds 10 words, rewrite it shorter before returning the JSON.\n\n"
        "BODY: 3 to 4 sentences. The FIRST sentence MUST name what makes THIS account's exposure profile "
        "different from peer accounts (Hims = multi-category consumer telehealth platform with compounded "
        "GLP-1 supply chain dependency; Ro = sexual-health-and-weight-loss focused with asynchronous "
        "prescribing as core workflow; Teladoc = enterprise virtual care contracted with health plans; "
        "Talkspace = pure-play mental health with state behavioral health licensing exposure). "
        "If multiple alerts in the same batch share the same trigger event, each body MUST lead with the "
        "ICP-segment differentiator so the alerts do not paraphrase each other.\n\n"
        "REGULATORY EXPOSURE LANGUAGE depends on `disclosure_source`:\n"
        "  - If disclosure_source is '10K': you MAY cite the 10-K with phrasing such as 'The most recent "
        "10-K names X' or 'Per the 10-K filing, X is a material risk.' Stay accurate to the listed risks.\n"
        "  - If disclosure_source is 'industry_general': the company is PRIVATE and has NO 10-K. You MUST "
        "NOT write 'the 10-K', 'per the 10-K', 'in their 10-K filing', or any variant. Use phrasing like "
        "'Industry-standard regulatory exposures for this business model include X' or 'Operators in this "
        "segment commonly face X' or 'Public commentary identifies X as the operational chokepoint.' Do "
        "NOT claim the company itself disclosed the risk. Do NOT cite SEC filings.\n\n"
        "FINANCIAL EXPOSURE: use operational language ('material exposure given top-3 state status') unless "
        "a specific disclosed metric is provided. Do not fabricate dollar ranges. Do not repeat content "
        "from cold or worked-deal frames.\n\n"
        "COLD_FIRST_TOUCH_FRAME: A copy-pasteable, BDR-ready cold outbound email to the public policy head or VP Regulatory Affairs (the Champion). Format MUST be exactly: 'Subject: [Punchy, trigger-based subject line]\n\nDear [Role],\n\n[2-3 sentence personalized email body citing the trigger event and business exposure]\n\nBest,\n[Rep Name]'. The CTA MUST be time-boxed and specific (e.g. '15-minute call on Thursday').\n\n"
        "WORKED_DEAL_REVIVAL_FRAME: A BDR reopening phone script dialogue guide to the GC or Chief Compliance Officer (the Buyer). Format MUST be exactly: 'BDR: [Crisp 2-sentence reopening line citing the trigger event as a timing reason]\n\nProspect: [Expected blocker or acknowledgement]\n\nBDR Pivot: [Crisp value hook proposing a 10-minute sync]'. Dialogue must be high-impact, conversational, and direct."
    )

    signals_block = "\n".join([
        f"  S1 (drug enforcement cascade): {opp['signal_scores']['S1']:.2f}",
        f"  S2 (rival co-mobilization): {opp['signal_scores']['S2']:.2f}",
        f"  S3 (enforcement precursor): {opp['signal_scores']['S3']:.2f}",
    ])
    source_events_block = ""
    for sig_key, events in opp["source_events"].items():
        for ev in events[:1]:
            source_events_block += f"  - {ev['event_summary']}\n"

    disclosure_source = profile.get("disclosure_source", "industry_general")
    is_public = profile.get("public", False)
    user = (
        f"Account: {opp['account_name']} ({profile.get('ticker') or 'private, no ticker'})\n"
        f"Public company: {is_public}\n"
        f"disclosure_source: {disclosure_source}\n"
        f"Segment: {profile.get('segment')}\n"
        f"State: {opp['state'].upper()}\n"
        f"Topic: {opp['topic']}\n"
        f"Composite score: {opp['composite_score']}\n"
        f"Signals fired: {opp['signals_fired']}/3\n"
        f"Signal scores:\n{signals_block}\n"
        f"Source events:\n{source_events_block}"
        f"Named disclosed risks: {', '.join(profile.get('named_disclosed_risks', []))}\n"
        f"Top state exposures: {', '.join(profile.get('top_state_exposures', []))}\n"
        f"Business-model exposure weight: {opp['business_model_exposure_weight']}\n\n"
        f"Generate the alert payload as strict JSON with keys: headline, body, "
        f"cold_first_touch_frame, worked_deal_revival_frame. Use the regulatory exposure language "
        f"prescribed for this disclosure_source. Name operational exposure in concrete terms."
    )

    required = {"headline", "body", "cold_first_touch_frame", "worked_deal_revival_frame"}
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = msg.content[0].text if msg.content else ""
        payload = _extract_json_payload(text)
        if payload and required.issubset(payload.keys()):
            payload["_source"] = "llm"
            return payload
        # Single retry with a tightened instruction; most failures are prose
        # wrapping the JSON object or trailing commentary.
        retry_user = user + (
            "\n\nCRITICAL: Return ONLY a single valid JSON object with exactly the four keys "
            "headline, body, cold_first_touch_frame, worked_deal_revival_frame. No prose before "
            "or after. No code fences. No trailing comma. No extra keys."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": retry_user}],
        )
        text = msg.content[0].text if msg.content else ""
        payload = _extract_json_payload(text)
        if payload and required.issubset(payload.keys()):
            payload["_source"] = "llm_retry"
            return payload
        fallback = _template_narrative(opp, profile)
        fallback["_source"] = "template_fallback_invalid_keys" if payload else "template_fallback_no_json"
        return fallback
    except Exception as exc:
        fallback = _template_narrative(opp, profile)
        fallback["_source"] = f"template_fallback_{type(exc).__name__}"
        return fallback


_BUYER_BRAND_PATTERN = __import__("re").compile(
    # Catches the buyer's two-word brand name (the policy-intelligence platform)
    # in any casing, with or without a separator between the words. The
    # character class allows zero or more space, hyphen, or underscore so the
    # concatenated form (no separator) and the underscore form are also
    # caught. Used by _scrub_buyer_brand below as a post-processing belt for
    # any LLM slip-up.
    r"\bstate[\s\-_]*affairs\b",
    flags=__import__("re").IGNORECASE,
)


_VOICE_BANNED_WORDS_PATTERN = __import__("re").compile(
    r"\b(?:actually|honestly|leverage|unlock|supercharge|empower|seamless|robust|elevate)\b",
    flags=__import__("re").IGNORECASE,
)


def _scrub_buyer_brand(payload: dict[str, Any]) -> dict[str, Any]:
    """Post-process LLM output to strip any organic buyer-brand-name slip-up.

    The LLM is told in the system prompt to use 'government affairs team' /
    'regulatory affairs lead' instead, but occasionally writes the buyer's
    brand name organically as a generic corporate-role title. This filter
    rewrites any such instance to 'government affairs' so the trailing noun
    (lead / team / function) reads naturally. Operates on every string value
    in the payload so headline, body, and both frames are covered.
    """
    cleaned: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(v, str):
            cleaned[k] = _BUYER_BRAND_PATTERN.sub("government affairs", v)
        else:
            cleaned[k] = v
    return cleaned


def _scrub_voice(text: str) -> str:
    """Post-process LLM output to enforce voice rules.

    Strips em dashes and en dashes (replaced with comma-space) and removes a
    fixed list of banned filler/sales words. Cleans up the double-spaces and
    stray punctuation artifacts that result from removing words mid-sentence.
    Operates on a single string so callers can apply it value-by-value across
    the payload dict the same way _scrub_buyer_brand does.
    """
    if not isinstance(text, str):
        return text
    # Replace em-dash and en-dash with comma-space; punctuation glue still
    # parses naturally in surrounding prose.
    cleaned = text.replace("—", ", ").replace("–", ", ")
    # Remove banned words (case-insensitive, word-bounded).
    cleaned = _VOICE_BANNED_WORDS_PATTERN.sub("", cleaned)
    # Collapse spacing/punctuation artifacts left behind by word removal.
    re_mod = __import__("re")
    cleaned = re_mod.sub(r"\s+,", ",", cleaned)
    cleaned = re_mod.sub(r"\s+\.", ".", cleaned)
    cleaned = re_mod.sub(r",\s*,", ",", cleaned)
    cleaned = re_mod.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    """Parse a JSON object out of an LLM response, tolerant of code fences and prose.

    LLMs sometimes return prose before or after the JSON, or wrap the object in
    a ```json ... ``` fence. This finds the outermost {...} substring and tries
    to parse it. Returns None on failure rather than raising.
    """
    if not text:
        return None
    s = text.strip()
    if "```" in s:
        # Extract the contents of the first fenced block
        parts = s.split("```")
        if len(parts) >= 2:
            inner = parts[1]
            if inner.lstrip().lower().startswith("json"):
                inner = inner.lstrip()[4:]
            s = inner.strip()
    # Find the outermost JSON object
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


def interpret(opportunities: list[dict[str, Any]], use_llm: bool = True) -> list[dict[str, Any]]:
    """Generate narrative payloads for each opportunity.

    narrative_source reflects what actually produced the payload, not just
    whether the LLM path was attempted. Template fallbacks tag the specific
    reason (exception class or invalid keys) so downstream consumers can
    distinguish a clean LLM run from a degraded fallback.
    """
    profiles = load_account_profiles()
    out: list[dict[str, Any]] = []
    for opp in opportunities:
        profile = profiles.get(opp["account_id"], {})
        if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
            narrative = _llm_narrative(opp, profile)
        else:
            narrative = _template_narrative(opp, profile)
            narrative["_source"] = "template_no_api_key" if use_llm else "template_disabled"
        source = narrative.pop("_source", "template")
        # Belt-and-suspenders: the LLM is told not to use the buyer's brand
        # name, but occasionally writes it organically as a generic corporate-
        # role title. Scrub on the way out so no payload ever leaks the term.
        narrative = _scrub_buyer_brand(narrative)
        # Second pass: enforce voice rules (no em dashes, no banned filler
        # words). Applied after brand scrub so the brand-rewrite output is
        # also normalized for voice.
        narrative = {
            k: (_scrub_voice(v) if isinstance(v, str) else v)
            for k, v in narrative.items()
        }
        out.append({
            **opp,
            "narrative": narrative,
            "narrative_source": source,
        })
    return out


def persist(alerts: list[dict[str, Any]], out_path: Path = Path("data/alerts.json")) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(alerts),
        "alerts": alerts,
    }, indent=2))


def run(use_llm: bool = True) -> dict[str, Any]:
    opportunities = load_opportunities()
    alerts = interpret(opportunities, use_llm=use_llm)
    persist(alerts)
    return {
        "alerts_generated": len(alerts),
        "narrative_source": alerts[0]["narrative_source"] if alerts else None,
        "alerts": alerts,
    }


if __name__ == "__main__":
    summary = run()
    print(f"alerts: {summary['alerts_generated']} (source: {summary['narrative_source']})")
    for a in summary["alerts"]:
        print(f"\n  {a['narrative']['headline']}")
        print(f"    body: {a['narrative']['body'][:200]}")
        print(f"    cold: {a['narrative']['cold_first_touch_frame'][:120]}")
        print(f"    worked-deal: {a['narrative']['worked_deal_revival_frame'][:120]}")
