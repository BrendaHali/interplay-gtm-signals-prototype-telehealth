# Interplay GTM Signals · Telehealth & Digital Health Prototype

**Live dashboard:** https://brendahali.github.io/interplay-gtm-signals-prototype-telehealth/


## Executive Summary

This repository documents a time-boxed prototype of a state-policy intelligence engine for the multistate telehealth and digital health vertical. The Interplay GTM Signals prototype fuses five live public data sources (openFDA, OpenStates, LDA Senate, RSS news, SEC EDGAR) into a per-account 0-to-100 Signal Strength score across fifty-four operators selected as ICP-representative. Each surfaced alert anchors in a 10-Q risk disclosure the operator's own counsel filed last quarter, transforming broad outbound activity into a ranked queue of buying moments tied to named regulatory events. The CRO-visible outcome is sales-cycle compression on accounts where a one-to-three-person government affairs team already recognizes the regulatory issue as material.

---

## Prototype Scope and Production Requirements

The fifty-four watched operators represent the multistate telehealth segment by ICP screen across eight categories (see §1.1). This repository documents a time-boxed prototype built to demonstrate signal integration potential, technical design, and output shape under limited time constraints. It is an investigatory exercise and must not be treated as a finished, production-ready system.

A standalone public-data engine is structurally incomplete without deep empirical analysis of historical sales cycles. Building a robust, data-driven GTM machine requires auditing past deals to isolate which regulatory events actually drove pipeline, followed by blending these external policy alerts with real-time customer product engagement telemetry (such as multi-state trend alerts or transcript searches) and CRM status fields. Everything must be rigorously data-driven.

Deploying this into an active GTM machine requires five key production inputs:

1. **Paid API tiers.** OpenStates (~$50/month for unmetered access), additional bill-data providers (BillTrack50, LegiScan) for cross-validation, and an active CRM connection (HubSpot Engagements API).
2. **Persistent database storage.** A managed datastore (Cloud SQL, RDS, BigQuery) replacing the local JSON files for multi-day signal history, attribution audit trails, and concurrent access.
3. **Historical deal data.** Six months of historical alert-to-outcome attribution for empirical weight calibration, replacing the heuristic 0.35 / 0.30 / 0.35 weighting prior with evidence-based priors.
4. **Product telemetry integration.** Ingesting customer usage signals from your product platform as a high-intent fourth signal dimension.
5. **GTM Ops review and feedback loop.** A dedicated ninety-day human-in-the-loop validation phase where GTM Ops manually reviews signal quality and AEs log conversion outcomes. A full three-month feedback loop is required to refine the signals against actual conversion data and observe sales-cycle compression.

The prototype demonstrates engine capability within the brief's time-box. The production pathway depends on these inputs plus the Roadmap items in [`docs/MEMO.md`](docs/MEMO.md).

---

## Submission Package

| Artifact | Where it lives |
|---|---|
| Live dashboard (primary deliverable) | https://brendahali.github.io/interplay-gtm-signals-prototype-telehealth/ |
| Trigger a fresh run | [GitHub Actions workflow_dispatch](https://github.com/BrendaHali/interplay-gtm-signals-prototype-telehealth/actions/workflows/run.yml) → "Run workflow" button. The daily cron also runs at 08:00 UTC. |
| Public repository | https://github.com/BrendaHali/interplay-gtm-signals-prototype-telehealth |
| Decision memo (1-page) | [`docs/MEMO.md`](docs/MEMO.md) |
| Per-task evidence | Task 1 (ICP): §1 below · Task 2 (signals): §2 · Task 3 (pipeline): §3 §4 §5 · Task 5 (memo): `docs/MEMO.md` |

---

## 1 · ICP Definition

Fifty-four US multistate telehealth and digital-health operators carry concentrated regulatory exposure across state boards, pharmacy regulators, and interstate compacts (asynchronous prescribing, controlled substances, pharmacy compounding, clinician licensing, Medicaid telehealth reimbursement), forming a fifty-state surface area covered through automated monitoring against the one-to-three-person government affairs teams typical for the segment. The buyer is the General Counsel or Chief Compliance Officer; the champion is the VP Regulatory Affairs or Head of Public Policy. The function is mission-critical with external-tool dependency, supporting both high willingness-to-pay and high renewal stickiness.

### 1.1 · ICP Validation and Segmentation

The thesis validates in the operators' own SEC filings. Public companies disclose the same regulatory topics the engine surfaces, in volume, in their most recent 10-Q:

| Account | Ticker | 10-Q topic mentions |
|---|---|---|
| Hims & Hers Health | HIMS | 21 |
| LifeMD | LFMD | 16 |
| WeightWatchers Sequence | WW | 14 |
| GoodRx | GDRX | 14 |
| Talkspace | TALK | 8 |
| Amwell | AMWL | 6 |
| Teladoc Health | TDOC | 4 |

Comprehensive watchlist coverage with size-tier orchestration concentrates AE attention through routing logic. Every signal event evaluates against the full operator universe, capturing each state-policy exposure across the watchlist; size tier governs the composite floor for routing and the AE destination.

| Segment | Example accounts | Regulatory chokepoint |
|---|---|---|
| Consumer multi-category prescribers | Hims & Hers, Ro, Hers, Thirty Madison | State asynchronous prescribing, pharmacy compounding |
| GLP-1 weight-loss prescribers | LifeMD, Calibrate, Noom, WW Sequence, 9amHealth | Compounded GLP-1, BMI prescribing thresholds |
| ADHD / controlled-substance telehealth | Cerebral, Done Global, Workit Health, Pelago | DEA controlled-substance prescribing, Ryan Haight |
| Mental-health telehealth | Talkspace, Lyra, Spring, Headspace, BetterHelp, Talkiatry, Brightside, Charlie, Quartet | Behavioral health licensing, psychology compact |
| Enterprise virtual care | Teladoc, Amwell, Included Health, Accolade, MDLive, Doctor on Demand | IMLC adoption, Medicaid telehealth reimbursement |
| Primary care + employer health | One Medical, K Health, Galileo, Crossover, Eden, Cleo | Corporate practice of medicine, ERISA preemption |
| Specialty consumer telehealth | Maven, Tia, Wisp, Plume, Folx, Hone, Sword, Curology, Equip, Allara, Pomelo, Origin, Nurx, Twentyeight | Reproductive health rules, gender-affirming care bans, physical therapy licensing |
| Prescription, pharmacy, directory | GoodRx, Zocdoc, Truepill, Honeybee, Capsule, Alto, Lemonaid | State PBM regulation, anti-kickback, pharmacy licensing |

The buyer offers three tiers (News, Pro, Enterprise). Each opportunity maps to an `expected_deal_band` (`5_top` $200K+ through `1_floor` <$40K) via a pricing function over `state_count`, `topic_exposure`, `employee_count_band`, `funding_stage`, and `target_tier`. Pricing constants live in `scripts/_lib/blender.py` and recalibrate once attribution data yields real win/loss numbers.

**Size-Tier Orchestration.** Routing rules read from `data/scoring_config.yaml`:

| Size tier | Accounts | Composite floor | Routes to | AE assignment |
|---|---|---|---|---|
| Enterprise | 26 | 0.20 | `outputs/alerts.json` (AE queue + Slack) | Named AE per account |
| Midmarket | 18 | 0.40 | `outputs/alerts.json` | Shared midmarket pool |
| Startup | 10 | 0.60 | `data/watchlist_opportunities.json` (capture only) | Pending enrichment |

**Two-Axis Classification.** Size tier and target tier are decoupled by design and answer different questions:

| Axis | What it captures | What it drives |
|---|---|---|
| Size tier | What the account IS: employee count, funding stage, revenue band | Per-tier composite floor (0.20 / 0.40 / 0.60) and AE routing destination |
| Target tier | What buyer product fits the regulatory scope: states in play, count of high-risk topic exposures, post-incident operating context | Estimated ACV and expected deal band |

Topic exposure is the primary input to target_tier. A midmarket-sized operator with three high-risk topics across multistate footprint (Cerebral: controlled-substance telehealth, mental-health telehealth, and asynchronous prescribing across 10 states) rates Enterprise fit at midmarket headcount because the regulatory scope warrants Enterprise-tier product coverage. Midmarket-size with Enterprise-fit combinations represent the highest-conviction expansion targets, where regulatory pain (the value driver) operates independently of headcount (the price-tolerance signal).

All fifty-four profiles carry human-validated enrichment across size tier, employee count band, funding stage, revenue band, target tier, and topic exposure. Seven accounts are public (HIMS, LFMD, TALK, TDOC, AMWL, GDRX, WW); the remaining forty-seven are private. Six accounts carry an `acquired_note` flag for material corporate events (Accolade, One Medical, Alto, Lemonaid, Done Global, Thirty Madison + Nurx); see `data/account_profiles.json`. The interpretation layer gates "10-K" language on `disclosure_source` to maintain disclosure-accurate copy across the public-private operator mix.

---

## 2 · Signal Architecture

Three primitive signal detectors plus one risk-disclosure enrichment. Each primitive maps to a distinct buying moment for the prospect's government affairs team. Each produces a 0-to-1 score per (account, state, topic) opportunity.

**Hybrid Federal-State Architecture.** To optimize API quota usage while maintaining maximum GTM coverage, the prototype separates national policy indicators from localized state-level triggers. The **OpenStates v3** client targets the **Top 10 states by telehealth volume** (CA, NY, TX, IL, FL, PA, OH, GA, NC, MI), which represent over 80% of segment revenue. Concurrently, our federal trackers monitor national datasets (openFDA, LDA Senate, SEC EDGAR, and federal agency enforcement news). The **Composite Blender** then projects these national trends (such as federal lobbying pushes or FDA drug alerts) onto each account's specific state exposure footprint (e.g., CA, NY, or TX), showing their localized vulnerability to a federal cascade.

| Signal | What it detects | The buying moment | Data sources | 60-day success metric |
|---|---|---|---|---|
| **S1 Drug Enforcement Cascade** | Statistically significant spike in openFDA enforcement on a telehealth-prescribed drug category (GLP-1s, ADHD stimulants, hormones, SSRIs). Z-score and Poisson upper-tail probability evaluated against a twelve-week baseline; spike-only events fire at 0.75x; spike paired with a forward-only matching state bill within 60 days scores higher. | FDA enforcement on compounded semaglutide sterility or methylphenidate supply operates as a 30-to-60 day leading indicator of state pharmacy-board or legislative response. GA teams capture the strategic timing window through notification at federal-enforcement time, ahead of state-bill introduction. | openFDA Drug Enforcement, OpenStates bills | At least 60% of S1-routed alerts convert to AE first-touch within 7 days. Quality risk: spike detection firing on noise. |
| **S2 Rival Co-Mobilization** | Two or more named telehealth competitors registering federal lobbying activity on the same prescribing, scope-of-practice, or compact issue within a rolling ninety-day window. Topic classification uses word-boundary regex against LDA general-issue codes. | When two-plus competitors lobby federally on the same issue, state action is statistically more likely than baseline. Multi-competitor convergence is the rare diagnostic event that timely external notification surfaces to the prospect's GA team. | LDA Senate disclosures, competitor pair sets | Post-call AE feedback confirms the prospect's GA team gained the competitive lobbying intel through the call. Quality risk: LDA topic classifier producing alerts the prospect already saw. |
| **S3 Enforcement Precursor** | FDA, FTC, DEA, or state-AG enforcement against a telehealth prescriber or compounding pharmacy. Optional cascade into a state legislative response within fourteen days. Fires at 0.6x as `legislative_response_pending` until a legislative cascade resolves. | An enforcement action against a peer is the single most credible motivating event for a buyer's GA team. The cascade-to-state-hearing window captures the political-cover-for-state-action pattern. | CFPB Newsroom RSS, Google News (telehealth-scoped), OpenStates committee schedules | At least 30% of S3 alerts trigger a meeting where the prospect names the underlying enforcement action as the timing reason. Quality risk: enforcement-to-bill window misaligned. |
| **SEC EDGAR risk-disclosure enrichment** | Counts mentions of each telehealth topic inside each public account's most recent 10-Q / 10-K. Output drives `risk_disclosure_multiplier` (1.0 to 1.3, capped). Private accounts hold at 1.0. | The operator's own filing language is ground-truth evidence the topic is material. When Hims's 10-Q references compounded GLP-1 twenty-one times, an alert on (Hims, compounded_glp1) scores higher than the same alert on a peer with lower disclosure density. | SEC EDGAR full-text search (`efts.sec.gov`), filings index (`data.sec.gov`) | Enrichment, not a routed signal. Success measured by whether public-account alerts cite filing language the prospect's GA team confirms as accurate in the first call. |

### 2.1 · Signal Selection Criteria

Each signal in the portfolio meets four quality criteria: cascade-window leading indicator (notification advantage over commodity legislative-tracking products), enrichment depth (combines bill data with at least one additional public source), scored integration (every input drives composite scoring directly), and ground-truth anchoring (alerts cite evidence the prospect's own counsel disclosed).

Patterns evaluated and excluded from the portfolio:

| Pattern | Rationale for exclusion |
|---|---|
| Bill-status alerts ("HB 123 advanced to committee") | Commodity coverage available through incumbent legislative-tracking products; flagged per the brief's auto-fail criterion |
| Sponsor-count thresholds ("bill has 5+ sponsors") | Decorative; uncorrelated with passage probability or regulatory impact |
| Volume-based news alerts ("3+ mentions of telehealth this week") | Noise-prone; conflates volume with materiality |
| Pure keyword matching on bill text | High false-positive rate; misses the cascade pattern that produces buying moments |
| Hearing-only alerts without enforcement context | Lacks the "why now" anchor that converts an alert into an outbound trigger |

S1 and S3 operate in decoupled-fire mode on most days. The OpenStates free-tier quota (500 daily requests) reaches saturation on the third or fourth refresh of the day; the OpenStates client persists every bill into a cumulative store at `data/openstates_bills.json` and refreshes incrementally, keeping the quota a refresh-rate consideration. Signal detectors operate against the cumulative store between refreshes. A paid tier upgrade in the Roadmap moves S1 and S3 into full cascade mode for every run. S2 operates as the dominant active signal because LDA federal filings carry no rate-limit dependency.

---

## 3 · Composite Scoring (Signal Strength)

Three signal scores combine with two multipliers loaded from `data/account_profiles.json` and `data/scoring_config.yaml`:

```
composite = (0.35 × S1 + 0.30 × S2 + 0.35 × S3)
          × business_model_exposure_weight   (1.0 to 1.4, per account)
          × risk_disclosure_multiplier       (1.0 to 1.3, capped, from SEC EDGAR)
```

The dashboard renders this composite scaled to 0–100 as **Signal Strength**. The engagement multiplier activates once HubSpot integration provides real CRM engagement state.

**Score Components:**

| Component | What it captures | Range | Source |
|---|---|---|---|
| S1 (drug enforcement cascade) | Federal FDA enforcement spike on a telehealth-prescribed drug category, optionally cascading to a state bill within 60 days | 0–1 | openFDA + OpenStates |
| S2 (rival co-mobilization) | Two or more named telehealth competitors registering federal lobbying activity on the same topic in 90 days | 0–1 | LDA Senate |
| S3 (enforcement precursor) | FDA, FTC, DEA, or state-AG enforcement against a telehealth peer, optionally cascading to a state legislative response | 0–1 | News RSS + OpenStates |
| business_model_exposure_weight | Per-account exposure scalar (consumer multi-category prescriber = 1.4, enterprise virtual care = 1.0) | 1.0–1.4 | `data/account_profiles.json` |
| risk_disclosure_multiplier | Public-company 10-Q topic-mention boost (private accounts contribute 1.0) | 1.0–1.3 | SEC EDGAR full-text search |
| **Signal Strength (display)** | composite × 100, rounded to nearest integer | 0–100 | Dashboard derivation |

**Band Labels:** HIGH (≥60), MEDIUM (40–59), LOW (<40).

Opportunities above the per-size-tier composite floor (enterprise 0.20, midmarket 0.40, startup 0.60) route to the interpretation layer (Claude Haiku 4.5 with a template fallback). Per-AE daily caps activate once real AE assignments populate through the HubSpot integration. The interpretation layer produces a four-section payload: ten-word-maximum headline, three-to-four sentence body, cold first-touch frame, and worked-deal revival frame. Both frames appear side-by-side to support net-new prospecting and worked-deal acceleration from the same alert payload.

---

## 4 · Source Verification

Every source operates against live APIs and is verified by `scripts/verify_sources.py`. Numbers refresh on every daily run and persist in `outputs/run_summary.json`.

| Source | URL | Live result |
|---|---|---|
| openFDA Drug Enforcement | api.fda.gov/drug/enforcement.json | 121 records across 6 telehealth-prescribed drug categories in 365 days |
| OpenStates v3 | v3.openstates.org | 323 bills across 6 telehealth keywords in 90 days. The client maintains a cumulative local store at `data/openstates_bills.json` and refreshes incrementally; the 500-daily-request free tier operates as a refresh-rate consideration, with the cumulative store serving signal detectors between refreshes. |
| LDA Senate disclosures | lda.senate.gov/api/v1/filings | 648 filings across 54 accounts on the most recent live run |
| CFPB Newsroom RSS | consumerfinance.gov/about-us/newsroom/feed | 16 items pulled |
| Google News (telehealth-scoped) | news.google.com/rss/search | 172 raw items across 4 queries in 90 days |
| SEC EDGAR full-text search | efts.sec.gov, data.sec.gov | 83 topic mentions across the seven currently-public accounts' latest 10-Q / 10-K filings. HIMS 21, LFMD 16, WW 14, GDRX 14, TALK 8, AMWL 6, TDOC 4. Two formerly public accounts (Accolade, One Medical) reclassified as private after acquisition. |

---

## 5 · Pipeline Architecture

```mermaid
flowchart TD
  Cron[GitHub Actions cron · daily 08:00 UTC] --> Ingest
  Dispatch[GitHub Actions workflow_dispatch · on-demand] -.manual run.-> Cron

  subgraph Ingest[Ingest layer · five live public data sources]
    OFDA[openFDA Drug Enforcement · 365 day rolling]
    OS[OpenStates · telehealth keywords · top 10 states]
    LDA[LDA Senate · 20 watched accounts]
    EN[Enforcement News · CFPB RSS plus Google News telehealth-scoped]
    SEC[SEC EDGAR · 10-Q and 10-K risk full-text search · public accounts]
  end

  OFDA --> Detect
  OS --> Detect
  LDA --> Detect
  EN --> Detect

  subgraph Detect[Three signal primitives]
    S1[S1 · drug enforcement spike, optional bill cascade]
    S2[S2 · rival co-mobilization via LDA]
    S3[S3 · enforcement precursor, optional legislative response]
  end

  S1 --> Blend
  S2 --> Blend
  S3 --> Blend
  SEC --> Blend

  Blend[Composite blender · per account x state x topic] --> Mults[Business-model × risk-disclosure multipliers]
  Mults --> Rank[Rank by composite score]
  Rank --> Interp[Interpretation · Claude Haiku 4.5 · dual frames · disclosure-gated language]

  Interp --> Slack[Slack incoming webhook]
  Interp --> Pages[GitHub Pages · client-side dashboard]
```

---

## 6 · Operations

**Production Deployment & Hosting Strategy.** GitHub Actions runs the pipeline daily at 08:00 UTC. The workflow ingests fresh data, executes all stages, posts alerts to the configured Slack webhook, regenerates the static site into `site/`, and commits outputs to the main branch. 

For the current prototype scope, we host the static dashboard on **GitHub Pages** powered directly by the repository and GitHub Actions. Utilizing a dynamic runtime provider like Vercel is unnecessary overhead since the client-side dashboard consumes pre-compiled pipeline outputs. For future development phases of larger scope—such as dynamic query runtimes, multi-user authentication, or CRM dynamic writebacks—we will transition to Vercel or Firebase. We recommend **Firebase** if the broader GTM and telemetry infrastructure is Google Cloud/BigQuery-based, due to Firebase's native, low-latency connectivity and secure integrations.

**Dashboard.** The client-side dashboard at `index.html` reads `outputs/accounts_with_signals.json` (per-account rollup, primary data source), `outputs/account_profiles.json`, `outputs/alerts.json`, and `outputs/run_summary.json` at page load and renders alert cards, signal score breakdowns, source verification indicators, state filters, and search entirely in browser JavaScript. `scripts/generate_site.py` copies the dashboard plus the latest outputs into `site/` for the Pages artifact.

**On-Demand Execution.** The GitHub Actions Workflow Dispatch endpoint accepts manual triggers from any user with repo access via the "Run workflow" button on the workflow page.

---

## 7 · Local Setup

```bash
git clone https://github.com/BrendaHali/interplay-gtm-signals-prototype-telehealth.git
cd interplay-gtm-signals-prototype-telehealth
pip install -r requirements.txt
cp .env.example .env  # populate API keys

python scripts/verify_sources.py        # confirm all sources operating
python scripts/run_pipeline.py          # full pipeline
python scripts/run_pipeline.py --skip-ingest  # re-run against cached data
python scripts/generate_site.py         # copy dashboard + outputs into site/
open index.html                         # dashboard reads outputs/ directly
```

---

## 8 · Documentation

| Document | Purpose |
|---|---|
| [`docs/MEMO.md`](docs/MEMO.md) | One-page decision memo covering Executive Summary, Strategic Context, Key Design Decisions, Roadmap, Strategic Data Inputs, Strategic Question for the Panel, and Recommendation. |
