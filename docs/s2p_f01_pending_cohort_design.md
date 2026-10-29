# S2P-F01 Design: Planted Pending Work Cohort

Date: 2026-10-21. Design complete; implementation and live validation not performed. Only this document and session state are changed.

Paths below are relative to s2p-copilot unless prefixed `SDK/` (the sibling copilot-sdk repository) or `AGE/` (ci-platform). Line references identify the current working tree.

Session state confirms that boundary allocations now precede freeze, review corrections are the last learning phase, and the diagnostic sweep found no intentional score-only cohort. The saved PW run had 12 queue-blocked failures, 14 explicit empty-queue skips, and eight serial dependents that did not execute.

Three corrections to the task premises are important:

1. The current model has **eight factors**, not seven: `backend/app/domains/s2p/config.py:4,37-48` describes `(5,5,8)` and includes `environmental_risk`. The “7-Factor Reasoning” UI/test title is stale.
2. `format_compliance` is one of five **categories** (`config.py:20-27`), not a factor. Exclude this category; retain all eight input dimensions.
3. Current preseed is not a fixed 321-invoice operation. Its fixture has two special decisions, 30 warmups and 20 post-freeze warmups, plus conditional extinction, four boundary candidates and up to 60 review/confirmation scores. Stable training IDs resume, while boundary/review IDs use UUIDs. This design makes the **new cohort** idempotent; it does not claim the entire preseed is strictly idempotent.

## Specification

### Invoice ID range and count

Use **24 fixed invoice identities**:

```text
S2P-INV-PENDING-V1-0001
...
S2P-INV-PENDING-V1-0024
```

The exact generator is `f"S2P-INV-PENDING-V1-{i:04d}"` for integers i=1..24 inclusive. Send this as `event_id`; the score endpoint derives `invoice_id=event_id` for non-fixture invoices (`routers/s2p.py:2164-2169`). Decision IDs remain server-generated and are recorded separately.

This namespace cannot equal numeric training/catalogue IDs such as `S2P-INV-0001` or `S2P-INV-0050`, nor `S2P-DEMO-WARM-V3-*`, `S2P-DEMO-BOUNDARY-*`, or `S2P-DEMO-REVIEW-*`. Search found no current source fixture/E2E references to the proposed prefix. This is a source-level check; implementation must check the live inventory for collisions before writing.

The prefix intentionally matches existing E2E `/S2P-INV|STRESS-CONC-S2P|INV-|demo-s2p/i` selectors (`SDK/e2e/s2p/helpers.ts:8`, `active-age-smoke.spec.ts:48`, `rule-vs-reasoning.spec.ts:36`). Do not use an opaque UUID or only `S2P-DEMO-*`, which need not match those selectors.

**Why 24:** tests generally need one selectable invoice; phase1:79-90 optionally selects the second invoice. Eight current unmocked test cases submit a learn (listed below). A conservative allowance for two attempts each is 16 consumption events, leaving eight of 24 available in an isolated run if each event consumes one distinct invoice. Twenty-four also fits the queue's 50-row scoring cap. This is headroom, not a guarantee against concurrent analysts, repeated suite runs, or future tests; E2E must preflight availability. No current queue test requires 50 invoices or iterates through and learns the entire queue.

### Category and factor configuration

Category assignment for slot i is `categories[(i-1) % 4]`, with this exact order:

```text
price_variance, quantity_mismatch, duplicate_risk, contract_gap
```

This yields six invoices per category and **zero format_compliance invoices**. Apply these explicit normalized factor requests by category:

| Factor | price_variance | quantity_mismatch | duplicate_risk | contract_gap |
|---|---:|---:|---:|---:|
| match_status | 0.90 | 0.55 | 0.90 | 0.65 |
| amount_variance_ratio | 0.18 | 0.08 | 0.02 | 0.10 |
| duplicate_score | 0.03 | 0.04 | 0.75 | 0.05 |
| supplier_exception_history | 0.12 | 0.15 | 0.10 | 0.20 |
| payment_terms_impact | 0.30 | 0.35 | 0.25 | 0.55 |
| commodity_index_correlation | 0.60 | 0.75 | 0.80 | 0.40 |
| tax_regulatory_compliance | 0.95 | 0.95 | 0.95 | 0.80 |
| environmental_risk | 0.10 | 0.15 | 0.10 | 0.20 |

These are explicit synthetic exception scenarios, not real supplier evidence or guaranteed actions. They exercise moderate price variance, incomplete matching, duplicate risk and a contract gap without invalid vectors. All values are finite and within [0,1]. **Do not require any particular action, confidence, positive accuracy delta, or learned ground truth from these vectors.**

Each payload additionally contains:

- `event_id` as above; `category` from the four-category cycle.
- `amount = 1000.0 + 125.0 * (i-1)` (1000.00 through 3875.00).
- `supplier_id = f"SUP-S2P-PENDING-V1-{i:04d}"` and `supplier_name = f"Planted pending demo supplier {i:02d}"`. Unique sample supplier IDs avoid conflating the new cohort with real/canonical supplier records.
- `supplier_risk_rating = 1.0 - supplier_exception_history` from the table. This is necessary because `SupplierExceptionHistory.compute()` checks supplier risk rating before its explicit-factor fallback (`domains/s2p/factors.py:269-289`); the request default would otherwise replace the intended factor with 0.5.
- The eight factors as top-level score request fields. Omit contract_id, approved_categories and spend-history overrides so they do not intentionally supersede the supplied values.
- No actual_action, outcome, status, correct, is_correct, verified, ground_truth_action, or similarly named context keys. Do not supply fake PO/receipt/contract evidence.

The existing `_score()` adds `origin="s2p_demo_preseed", planted=True, evidence_tier="T_S"` to request context (:109-110). The backend recognizes that origin in demo mode and persists planted/evidence-tier/demo-provenance metadata (`routers/s2p.py:2411-2412,2483-2488`). Do not depend on arbitrary new context keys surviving the backend's metadata allowlist (`_invoice_decision_metadata`, :1363-1386). Cohort ownership uses its reserved invoice namespace, exact supplier identity/category/amount, and persisted planted provenance.

The table specifies **requested factors**, not an override of the graph-first factor engine. `_invoice_from_request()` copies all eight explicit inputs (:2196-2209), then `compute_all_factors` can use real graph context or signals. Validate the actual returned/stored eight-element vector and its finiteness; record discrepancies, but never bypass graph logic to force an expected action. In an isolated no-context unit fixture, assert the expected factor values, including the supplier-risk inverse.

### Phase placement

Insert the new score-only phase at the boundary between **current line 444 and line 445** of `scripts/preseed_s2p_demo.py`:

```python
_seed_review_learning(api, existing, report)             # existing :444
# NEW: if training has no errors/limitations, ensure the pending cohort.
#      No learn/outcome/investigation call from this phase.
status, body, error = _request_json(                     # existing :445
    api, "POST", "/api/demo/s2p/learning/persist"
)
```

Final order:

```text
special -> warmup -> extinction -> boundary -> existing early-exit guard
-> freeze -> demo re-freeze -> post-freeze warmup -> review corrections
-> NEW planted score-only cohort -> existing persist
-> existing twin/S2P-09, budget/S2P-07, extinction/S2P-05 checks -> report
```

Run the new phase only when `errors`, `learning_errors`, `investigation_errors`, and `limitations` are all empty after review. Otherwise record cohort `status="skipped_training_failed"`. Preserve existing persistence/final checks rather than adding an early return that would prevent already-applied corrections from being flushed. A failure inside the new phase adds a normal error/limitation; it must not be hidden by later successful persist or an available queue. Existing `main()` :523-525 then returns 1.

Do not move `_seed_boundary_allocations()` back after review. Do not append the pending payloads to `special_decisions` or warmup loops: those loops call `_learn()` immediately (:406-418,438-442). Do not add the new Decision IDs to `review_decision_ids`.

### Score-only call pattern

Add a standalone preseed helper, proposed name `_seed_pending_cohort(api, existing, report)`, using the **existing `_score()`**. No separate raw GraphStore writer, backend route, local JSON decision store or disposable scorer.

Conceptual contract, not an implementation patch:

```text
load complete live inventory; group ALL rows by invoice/event identity
validate ownership/collision/duplicate state for reserved cohort IDs
for slot 1..24:
    if owned ID already exists: preserve it; classify pending/consumed
    else: _score(api, deterministic_payload(slot), refreshed_existing, report)
read complete inventory again and verify stored state
probe existing preview queue and report actual availability
```

`_score()` :105-108 skips an event already in its `existing` map; :111 POSTs `/api/s2p/score`; :115 requires a Decision ID; :118-119 updates local map/report. It does **not** learn or itself directly set database status. Existing map construction (:82-96) collapses multiple rows to one per event; the new helper must inspect complete grouped inventory **before** trusting this map, so it cannot accidentally treat a completed invoice with an older pending row as a fresh slot.

For an ordinary accepted score, backend :2480 invokes `scorer.score`; response :2506 has `learning_applied=False`. SDK `copilot_sdk/scoring/scorer.py:810-834` writes the prepared Decision; AGE `ci_platform/graph/age_graph_store.py:1148,1323` initializes pending status. Stored data includes server Decision ID/timestamps, s2p domain, category/index, recommended action/index, confidence/probabilities, factors/names/vector, versions and invoice/supplier/amount/provenance metadata. No Outcome is written by scoring. Audit/score-side-effect records may be written; “score-only” does not mean a read-only HTTP operation.

The phase must never invoke `_learn()`, POST `/api/learn` or `/api/s2p/outcome`, `_investigation_warmup()`, or any direct outcome mutation. Do not use `fresh_score()`/day-zero: that path is disposable and cannot populate the live queue.

## Data Lifecycle After F01

```text
24 deterministic planted invoice payloads
  -> _score(): stable event identity, synthetic context; POST live score
  -> Decision(status=pending, recommendation/factors/metadata; no Outcome)
  -> AGE join: actual_action=None, is_correct=None for absent Outcome
  -> read_decisions -> merge by decision_id -> metadata flattening
  -> _verified(row) == False
  -> score_pending: retain pending, dedup by invoice_id, newest 50
  -> read-only rescore, rank by confidence
  -> queue payload: total=24; default exceptions/invoices length=5
  -> frontend auto-selects first available invoice; analyst may score/learn
```

Exact verification predicate (`services/s2p_preview_data.py:68-71`):

```python
return (row.get("status") in {"confirmed", "overridden", "verified"}
        or row.get("correct") is not None or row.get("is_correct") is not None
        or row.get("verified") is True or bool(row.get("actual_action")))
```

For each newly planted row: status is pending; correct/is_correct are absent or None; verified is absent or not True; actual_action is absent/None. All branches are false. **Do not initialize correct/is_correct to False**: non-None False still means verified. `outcome="pending"` in an audit entry is not an outcome join and is not a branch in this predicate.

`read_decisions()` always merges actual outcomes (`s2p_preview_data.py:74-85`), but cannot invent one for an unlearned row; AGE projection :4523-4524 yields None from absent Outcome. The service does not generate invoice decisions from JSON; it projects stored rows. Preview `_decision_invoice()` :227-248 makes invoice-shaped records and optionally enriches known sample IDs. New IDs have no catalogue fixture and must carry their own score inputs.

Active filter chain is `preview_queue` :373 -> `queue_read_payload` :322 -> cached payload or `read_decisions` -> `score_pending` :261 -> `queue_payload` :333 -> limit slice :328. `_pending_decisions` :251 is unused in this chain; `_verified` is imported from preview_data at :18.

**Exact fresh-state expectations**, assuming no unrelated pending rows and no concurrent mutation:

- `_verified` is False for all 24; none appears in the verified outcome population.
- `score_pending(all_rows, scorer)` returns `(list_of_24_invoices, 24)`, six per category.
- Default `/api/s2p/preview/queue` returns total=24, scored_count=24, showing=5, and five invoices/exceptions. `?limit=50` returns all 24. The frontend may slice ten, but cannot show more than the endpoint's default five without a different request.
- Confidence ranking does not guarantee a category mix among the default five, or which ID is first. Do not assert the exact displayed order.
- If unrelated pending rows exist, total is the number of **all unique pending invoice IDs**, not exactly 24; score_pending scores only the latest 50. Verify owned rows using the full inventory and report the real queue separately. Never delete or complete unrelated work to achieve an exact count.

### Verification/report contract for implementation

Add `pending_cohort` to the preseed report with `version`, `target_count=24`, `created_ids`, `reused_pending_ids`, `consumed_ids`, `collision_ids`, `pending_count`, `queue_total`, `queue_showing`, and `status`. Keep counts separate from training learned counts. Add `planned_pending_scores=24` to dry-run reporting and include these in total planned score requests; existing base plan is 52 (:394), so the base-plus-pending total is 76, with conditional boundary/review/extinction maxima still reported separately.

On a fresh run require all 24 owned rows to be stored pending with complete finite eight-factor vectors and correct identity/provenance. On a rerun require every absent slot either successfully created or explicitly reported as an error; existing consumed slots are preserved. Minimum usable cohort readiness is **two unconsumed distinct invoice IDs**; fewer adds a limitation and makes preseed exit nonzero. Report whether the cohort is full (24) or partially consumed, never imply 24 remain when fewer do.

Queue verification must check HTTP success and actual returned rows. Retry GETs only, up to three attempts spaced five seconds apart (each request retains the existing 30-second network timeout); failure yields an explicit limitation. Do not rescore/relearn to repair a cache delay. API score invalidates the materializer (`routers/s2p.py:2578`), but cache/graph failures must remain visible. Queue GET writes ancillary observations (:315-318,172-200); account for those existing side effects when comparing graph counts.

Offline regression tests should cover fresh24, identical second run0new, consumed slots, partial interruption, conflicting ownership, duplicate invoice history, missing/incomplete inventory, score timeout followed by committed readback, blocked scoring, and non-format category coverage. Test the helper in isolation as well as run phase ordering. Assert zero learn/outcome calls from the helper, unchanged verified count/centroids around just the cohort phase, and absence of cohort IDs in twin comparison. No tests were executed for this design.

## E2E Impact

### Consumers and exact-ID exceptions

- Existing queue-driven failures: active-age-smoke (1), new-surfaces (2), rule-vs-reasoning (4), shadow-smoke (1), situation-analyzer (2), sweep (2). F01 removes their demonstrated empty-queue blocker, subject to healthy score/context APIs. The eight serial Situation Analyzer dependents can then execute.
- Four flows, three phase1 and seven triage tests currently skip on empty work. They should execute rather than skip. `phase1.spec.ts:82-85` optionally chooses `nth(1)`, so the minimum readiness criterion is two even though one is sufficient for most tests.
- Dashboard, Insight and Evidence consume the first queue invoice and benefit from available context, but missing fixture-specific/graph evidence is still legitimately unavailable.
- Exact `S2P-INV-0001` references exist at `SDK/e2e/demo/s2p/s2p-01-rule-said-no.spec.ts:4`, `e2e/demo-cuts/s2p-scenarios.spec.ts:15`, and `e2e/demo/platform/plat-03-five-corrections.spec.ts:5`. These directly score/query the special copper scenario; they do **not** select the proposed cohort by that ID. Keep the original special fixture and its training behavior unchanged. The container/day-zero specs likewise POST their own special IDs.
- `e2e/s2p/cross-copilot-signal.spec.ts:10`, early-warning:11 and evidence-chain:12 use named IDs in mocked payloads; F01 need not recreate those IDs in AGE. Backup `spec_old.ts`/`spec_v1.ts` files are not the active `.spec.ts` scenarios.

### Learning and depletion during the suite

Eight unmocked learn-submitting tests were found: active-age-smoke:109 (one), shadow-smoke:106 (one), flows:157/169/213 (three), triage:119/133/142 (three). Phase1:142 opens the override form but does not submit; evidence-chain scenarios mock receipt data. Other displayed loops navigate tabs, not drain all invoices. Default umbrella Playwright configuration permits one retry, so 24 gives margin over 8*2=16 distinct consumptions; a different run configuration changes that budget.

Important existing D05 behavior: UI first scores the selected invoice, producing a **new Decision**, then learns that new Decision (`TriageScreen.tsx:178,218`). It does not necessarily learn the original planted Decision. `score_pending()` removes verified rows **before** choosing the newest pending row (:262-274), so an older planted pending row can remain/reappear for the same invoice. F01 must not rely on that defect to keep work available, and must not silently fix it by changing backend dedup. A direct learn of the planted Decision does remove that Decision from pending. Conservative capacity planning assumes real consumption; F05 separately establishes invoice completion/supersession semantics.

No pass is guaranteed merely by planting rows. F01 does not fix swallowed errors, supplier fixture failures, illustrative conservation, cache latency, optional-blockquote locators, post-learn refresh, or the unconfirmed supplier flake. The diagnostic found no missing rule/situation test IDs. Full PW pass/fail counts require a later controlled run; this document claims only removal of the observed queue precondition failure.

## Idempotency Contract

**Fixed V1 membership, immutable completed history, no automatic replenishment.** This is an explicit choice to keep F01 bounded and reviewable. Unlimited freshness and a strictly fixed cohort are different requirements; F01 does not pretend to supply an inexhaustible work queue.

| Live state of reserved ID | Action on rerun |
|---|---|
| Absent | Create exactly one score-only Decision through `_score()` |
| One matching owned pending Decision | Reuse; no POST, learn or metadata rewrite |
| Matching owned verified/completed history | Record consumed; never call `_learn`, reset status, or rescore to reopen |
| Pending and verified histories for same invoice | Record consumed/mixed history; do not count as available work even if raw queue currently includes old pending row; report D05 discrepancy |
| Multiple pending Decisions for same invoice | Record ambiguity; no new score; limitation for reconciliation, not automatic deletion |
| Foreign supplier/category/provenance or malformed identity | Collision limitation; do not modify or silently adopt |
| Archived or conflicting rows | Preserve; report unavailable/ambiguous slot rather than recreate an active duplicate |

Fresh first run adds at most 24 Decision rows. Second run with no intervening consumption adds zero cohort rows. If five original cohort invoices were completed, the next run preserves their outcomes and reports 19 available slots (assuming no ambiguous histories); it does not restore 24. If fewer than two usable slots remain, report `pending_cohort_exhausted` limitation. A future **explicit** new version/reset workflow can create a fresh cohort under a different namespace, with its own ownership and capacity tests; it is not an automatic behavior or additional CLI feature in F01.

`_score()` dedup is client-side, based on its inventory map (:105-108), not a server-side unique-event transaction. Requirements therefore include **one preseed writer, no concurrent E2E/analyst mutations during planting**. Read a fresh complete inventory immediately before the phase (:71-78 validates `complete=True` and total), not only run's initial :405 map. After an uncertain POST timeout, do not blindly retry: read back by reserved invoice ID. If a single matching committed Decision exists, resume from it; if inventory fails or more than one match exists, stop creating further rows and report the uncertainty. An in-flight timed-out request cannot be proved cancelled by one immediate empty read; leave that slot unresolved until backend quiescence and a subsequent explicit rerun. This documents the existing API's concurrency limit rather than claiming exactly-once server enforcement.

## Edge Cases and Ordering Dependencies

1. **Extinction:** `_seed_extinction()` :214-218 protects unrelated pending format_compliance rows; :243-246 checks the final owned pending row before learning it. Plant only the four other categories. On future runs these owned pending invoices cannot become format blockers. Keep tax_regulatory_compliance and all other factors; they are not the extinct category.
2. **S2P-09:** boundary learning remains :421 before freeze; review corrections remain :444 the last learn. Cohort scoring does not update the centroid through learning. Frozen comparison reads the verified join (`routers/s2p_demo_beats.py:208-210,245`), excluding unlearned cohort Decisions. Score-side counters/observations and total Decision count may change; do not assert every diagnostic counter is unchanged. Persist :145-176 in demo_control flushes centroids/governance without writing outcomes.
3. **Training errors:** skip new planting when training has reported errors/limitations, but preserve the current persistence/error-reporting behavior. Pending work must not make a failed training demonstration appear successful.
4. **Conservation blocks:** score route applies governance (`routers/s2p.py:2459-2463`); RED rejects, AMBER can hold without a Decision. Do not invent a Decision ID or bypass the gate. `_score()` already rejects a response without an ID; stored readback remains mandatory.
5. **Factor schema drift:** assert deployed/config factor names match the eight inputs before accepting the phase. A seventh-only vector is not the desired design; `_pad_legacy_factor_vector()` (:2151) is a backward-compatibility facility, not a reason to omit environmental_risk.
6. **Existing pending work:** do not drain it, fabricate its outcomes, or force the global queue total to 24. With more than 50 pending identities, the newest-50 cap may exclude reused cohort rows; report queue visibility honestly and leave queue pagination/fairness to its own fix.
7. **Ownership/provenance:** reserved IDs alone are insufficient. Check expected supplier/category/amount and persisted planted/T_S provenance for existing seed Decisions; distinguish later analyst-created score history rather than rewriting it. Missing provenance in an existing reserved namespace is a collision requiring review.
8. **Scope:** expected implementation touches preseed helper/run/reporting and focused tests only. No scorer, graph schema, backend filter, supplier fixture, frontend, frozen-twin, outcome-writing or conservation changes are part of F01.
