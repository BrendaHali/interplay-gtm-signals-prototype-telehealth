**DECISION MEMO**

**Subject:** Operational Pilot & Infrastructure Transition Plan for the Telehealth & Digital Health Prototype

---

## Executive Summary

This memo walks through a time-boxed prototype of a state-policy intelligence engine for the multistate telehealth vertical. The engine pulls together five live public sources (openFDA, OpenStates, LDA Senate, RSS news, SEC EDGAR) into a per-account 0-to-100 Signal Strength score across fifty-four operators picked because they fit the ICP. The latest live run surfaces eight active conversations, each one tied to a 10-Q risk disclosure the operator's own counsel filed last quarter. The next step grounds the prototype in deep historical sales-cycle analysis, evolving the time-boxed exercise into a data-driven, long-term GTM machine.

## Prototype Scope & Strategic Context

Multistate telehealth operators face regulatory exposure across all fifty states with one-to-three-person government affairs teams, which is the structural automation opportunity this engine targets. The buying signal already lives in public data: FDA enforcement actions, federal lobbying records, peer-firm settlements, and SEC risk disclosures. The prototype pulls those sources into a single account-level alert anchored in the operator's own SEC-disclosed risk language.

The fifty-four watched operators represent the multistate telehealth segment by ICP screen across eight categories; profile enrichment was done manually from public sources. Growing this prototype into a long-term production GTM machine takes five operational inputs: paid API tiers, persistent database storage, HubSpot CRM integration, historical win-loss records, and a GTM Ops manual review cycle.

## Key Design Decisions

The prototype reflects five core decisions:

1. **Telehealth ICP focus.** State law concentrates the regulatory decision surface for telehealth: every meaningful chokepoint sits at a state board or interstate compact, which makes state-level policy intelligence high-value here.

2. **Per-account signal aggregation.** Account-level rollup mirrors how buyers buy. One telehealth platform firing across multiple states surfaces as a single prioritized conversation with multistate context, so the sales team scans fewer rows.

3. **SEC EDGAR full-text-search risk proxy.** Real-time public-filing topic counts scale the composite score up to 1.3x and anchor outbound copy in evidence the operator's own counsel disclosed. The query runs in seconds, so daily refresh stays cheap.

4. **Hybrid federal-state scoping.** The OpenStates client targets the top 10 states by telehealth volume (CA, NY, TX, IL, FL, PA, OH, GA, NC, MI) to keep the API quota healthy and focus outbound effort. National sources (openFDA, LDA Senate, SEC EDGAR, federal agency news) capture macro shifts. The Blender then projects those national signals onto each operator's specific state footprint.

5. **GitHub over Vercel for hosting and CI/CD.** The static client-side dashboard reads pre-compiled pipeline outputs, so a dynamic runtime provider like Vercel adds overhead this prototype skips. GitHub Actions and Pages cover the prototype's needs. At larger enterprise scope (dynamic querying, authentication, two-way CRM sync), the system moves to Vercel or Firebase. Firebase fits when the broader GTM stack is on Google, because of its native secure connectivity with BigQuery.

## Operational Pilot & Recommendation

The repository is a prototype that demonstrates what is technically possible under time-boxed constraints. The path to a sales-team-ready system runs through three workstreams:

*   Auditing past deals to isolate which regulatory events drove pipeline in the past.
*   Grounding the baseline heuristics in empirical revenue correlation by connecting the HubSpot API.
*   Blending third-party policy alerts with real-time customer product engagement telemetry (such as multi-state trend configurations or transcript searches) and CRM status fields to capture high-intent behavior.

The recommended pathway is a collaborative pilot for a multi-month period, characterized by a tight manual feedback loop and continuous adjustment. The first thirty days focus on building the data-driven foundation. The prototype then runs through a ninety-day operational pilot review. During this window, GTM Ops manually reviews and rates every routed alert, while AEs log conversion outcomes. A full three-month cycle establishes the feedback loops, refines the signals against measured conversion data, and surfaces the engine's true impact on sales-cycle compression. Every weight, every signal, every routing decision flows from measured deal data.
