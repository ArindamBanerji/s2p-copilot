# S2P-F04 Full Diagnostic Report

Date: 2026-10-21. Diagnostic and design only; no application, test, fixture, or configuration changes. Paths below are workspace-relative unless a section states otherwise. Source references describe the working tree, not necessarily committed HEAD.

## Section 0: Failure classification

### Evidence and reconciliation

The supplied numbers cannot describe 218 distinct tests: **184 + 12 + 1 + 14 + 8 = 219**. The current `copilot-sdk/e2e/playwright-report/index.html` embedded ZIP contains `report.json` and per-file result JSON. It records **218 total, 184 expected, 12 unexpected, 0 flaky, 22 skipped**, starting **2026-10-21T11:30:48.981Z**, duration **725.669 seconds**. Its 22 skips split into 14 annotated empty-queue skips and eight unexecuted serial dependents. This is a newer artifact than the 08:28 report described in the previous session entry; the outcome distribution is the same. There are 36 active `*.spec.ts` files and 218 test declarations in current source.

All 12 recorded failures stop at invoice selection, before their named feature assertions. That conclusion comes from the actual errors, not from assuming triage skips explain other specs. Dashboard, Insight, Evidence, Performance and Suppliers have passing cases in this artifact, although some assertions accept loading/empty/unavailable states and therefore prove less than their titles suggest.

Classification uses one primary category per recorded nonpass: Q for the direct queue precondition failure; S for explicit skip annotations; C for unexecuted dependents. These are orthogonal to secondary dependencies: all S and C cases here also depend on queue readiness. A DOM timeout caused by an actually empty queue is not counted again as an independent locator defect.

| Category | Observed count | Spec files affected | Traced cause |
|---|---:|---|---|
| Q | 12 | active-age-smoke, new-surfaces, rule-vs-reasoning, shadow-smoke, situation-analyzer, sweep | No pending invoice to select; feature assertions not reached |
| E | 0 | None among recorded failures | No endpoint-specific failure established independently of queue |
| L | 0 | None among recorded failures | Missing invoice DOM is explained by empty data; latent locator defects discussed below |
| S | 14 | flows, phase1, triage | Explicit `test.skip(true, "No queued invoice available for scoring")` |
| C | 8 | situation-analyzer | Scored State serial `beforeAll` fails while selecting an invoice |
| O | 0 | None | No other recorded nonpass |
| **Observed total** | **34** | | **12F + 0Fl + 14S + 8DNR** |
| Unverified external claim | 1 | Allegedly suppliers:81 | No flaky result in available artifact; not an invented 35th test |
| Requested accounting total | 35 | | 34 traced outcomes + 1 unresolved claim, **not 35 verified outcomes** |

The supplier test `suppliers.spec.ts:81`, “Selecting supplier shows profile, seasonality, history, and heatmap,” passed on its sole attempt. Its seasonal assertion is at :86. `SuppliersScreen.tsx:246,418-429` mounts the seasonal panel even without a supplier, and :419-420 reads quarterly lead time/OTIF. Missing fixture fields explain missing bars, not necessarily a missing panel. A different flaky run's trace is needed to diagnose that claim. Do not count it as a known queue failure or promise its repair.

### Exact failure modes and what must pass next

All spec references in this table are under `copilot-sdk/e2e/s2p/`.

| Test declaration | Test | Actual failing location and expectation | Required state / subsequent assertions |
|---|---|---|---|
| active-age-smoke:79 | S2P active AGE test-mode smoke keeps UI flows working | :53 `toContainText(/Supplier\|Amount\|Category/i)`, 20s; received “Selected InvoiceChoose an invoice to begin triage.Score” | Select real pending invoice, score with ID/action/confidence, confirm learn; then assert AGE/dashboard flow |
| new-surfaces:28 | Rule vs Reasoning contrast renders both decision columns | :21 same 20s Selected Invoice assertion | Pending invoice, successful score, Rule-Based and Situation-Aware columns |
| new-surfaces:53 | S2P triage flow reaches governed contrast after scoring | :21 same assertion | Pending invoice and score, situation and contrast states |
| rule-vs-reasoning:47 | test_contrast_panel_visible_on_triage | :30 `waitForSelector('article:has(h2:has-text("Invoice Selector")) button')`, 30s | Pending invoice, then actual score and contrast panel |
| rule-vs-reasoning:53 | test_contrast_shows_two_columns | :30 same missing invoice button | Above, then both columns |
| rule-vs-reasoning:61 | test_contrast_shows_rule_threshold_and_outcome | :30 same missing invoice button | Above, then threshold/outcome or explicit no-comparison state |
| rule-vs-reasoning:68 | test_contrast_uses_situation_explanation_and_context | :30 same missing invoice button | Above, then explanation/context; latent :73 unsafe optional-node read must also be fixed |
| shadow-smoke:71 | S2P AGE shadow smoke keeps UI flows working | :44 Selected Invoice assertion, 20s | Pending invoice, actual score and learn acknowledgements; graph prechecks already passed |
| situation-analyzer:137 | 2. NL explanation renders | :49 first invoice button visible, 15s, invoked by `beforeAll` :129 | Populate queue and complete setup score; body :143 not reached |
| situation-analyzer:306 | 13. No crash on empty context | :49 first invoice button visible, 15s, invoked at :336 | Mocked situation alone is insufficient: requires queue and score too; make this edge case fully mocked |
| sweep:24 | Exception Triage mounts rule-vs-reasoning and situation panels | :18 Selected Invoice assertion, 20s | Pending selection, successful score, then panels |
| sweep:67 | governed triage: Exception Triage → score → rule/reasoning contrast | :18 same assertion | Pending selection, successful score, then both contrast columns |

The new-surfaces trace `playwright-report/data/3415b23ac9d538776844e8820e91f59a14a95f90.zip` repeatedly records HTTP **200** from `/api/s2p/preview/queue`, with body resource `c8d75b4ff777957e902622314e9bc821d3635b2c.json`:

```json
{"status":"ok","source":"graph","total":0,"scored_count":0,"showing":0,"exceptions":[],"invoices":[],"auto_approve_rate":0.0,"confidence_avg":0.0}
```

The omitted engine/scorer metadata includes `(5, 5, 8)` and all eight factors. This is genuinely empty backend data, not F02's former client fallback hiding a 500.

### Severity findings

* **P1 F04-Q1 — runtime pending population missing.** Live queue remains empty; the latest saved preseed log has no `pending_cohort` result. Source now has F01 but deployment/application is unverified. References: preseed :455,776-789; trace above; live probes in Section 4.
* **P1 F04-Q2 — readiness can falsely succeed.** Preseed :585-607 accepts any HTTP 200 object; no minimum visible count or identity reconciliation. Offline reproduction returned `ready`, inventory pending=24, queue total/showing=0, no limitations. This can conceal stale/broken projections after planting.
* **P1 F04-H1 — acceptance helpers hide failed preconditions or learning.** Empty queue skips at flows:75, phase1:57, triage:49; response predicates require success status and turn errors into timeouts; learning-result helpers accept unchanged controls after timeout (flows:55-62, triage:66-73, active-age:56-63, shadow:47-54). Required-stack acceptance must fail clearly, not skip or accept unchanged controls.
* **P2 F04-U1 — F02 does not implement caller error states.** Dashboard:24-35 rejects `Promise.all` without catch, then :71 shows zero from null. Insight:29-40,49-61 and Evidence:25-36 have uncaught fetch promises. Triage:142-154 conservation has no catch. Distinguish error from successful empty; React error boundaries alone do not catch asynchronous promise rejections.
* **P2 F04-L1 — latent explanation locator failure.** rule-vs-reasoning:73 awaits `blockquote p` before testing absence; SituationPanel:118-120 intentionally has no blockquote on real HTTP error. The fallback assertion is unreachable after that locator times out.
* **P2 F04-C1 — serial/shared setup and hybrid mocks amplify failures.** situation-analyzer:120-131 scores once in serial setup; eight tests never execute. Its empty-context test :313-336 mocks only situation, leaving selection/scoring live. Several backend-down branches simply return, recording passes without assertions (:154,166,173,189,198,211,241,291).
* **P2 F04-D1 — invoice-level completion semantics remain inconsistent.** `s2p_preview.py:262-274` removes verified rows before deduplicating invoice identities. An older pending row survives a newer verified re-score. `TriageScreen.tsx:230-247` does not refresh/remove queue items after successful learn. F01 must not depend on this bug to maintain capacity.
* **P3 F04-T1 — schema/readiness assertions are weaker than labels.** Backend has eight factors; Triage still labels the panel “7-Factor Reasoning” (:452); some mocks contain seven factors (cross-copilot-signal:57-65, observability:37-45). Passing loading/unavailable alternatives and immediate `.count()` checks limit coverage. These are not proven causes of the twelve saved failures.

## Section 1: Pending cohort state

**Verdict B for working-tree acceptance: exists, but readiness verification is broken. Verdict C at committed HEAD: no cohort implementation.** The score-only mechanism itself is sound; it is not evidence that the live cohort was applied. Do not delete/reimplement it wholesale.

`git rev-parse --short HEAD`: S2P `2287c62`; SDK `2fa18c4`. `git status` reports the preseed file modified and root tests untracked, among many existing changes. Preserve those changes. `git show HEAD:scripts/preseed_s2p_demo.py` has extinction-related “pending” text but no `_seed_pending_cohort`, `PENDING_COHORT_*`, or reserved V1 IDs. Both requested `git log --all --grep=pending/cohort -- scripts/preseed_s2p_demo.py` searches and the pending/f01/cohort branch-name search returned no matches. The last-20 path history returned `d876a20 AGE perf fix: preview 11.45s->0.17s, accumulation bug fixed, broken endpoints fixed. 2094 BE 0F, 218 PW 0F.` Commit-message claims are not a rerun result.

Session records document F01 implementation (10 tests), F03 (11 tests, root suite 23 passed), and F02 (TypeScript/build pass). These are prior results, not newly executed tests in this sweep. The saved `copilot-sdk/logs/preseed_s2p.log` has seed_version `s2p-final-v4`, learned=9, no errors/limitations, and **no pending_cohort key**. “321 decisions learned by this run” is unsupported: previous rows are reused, loops are adaptive, and logged learned count is per run.

Current `run()` phase order:

| Phase | Lines in scripts/preseed_s2p_demo.py | Learning behavior |
|---|---|---|
| specials | 738-742 | Score then learn |
| warmup | 744-750 | Score then learn |
| extinction | 752; implementation 382-416 | Owned format rows and final causal row learned; unsafe concurrency exits honestly |
| boundary | 753; 611-634 | Score, investigate, learn |
| early-exit | 754-755 | Errors/learning errors/limitations; investigation errors are checked later |
| freeze / demo re-freeze | 757 / 763 | Snapshot operations |
| post-freeze warmup | 769-774 | Score then learn |
| review corrections | 776; 637-693 | Explicit `/api/learn` plus easy confirmations; bounded/early positive exit |
| pending cohort | 777-788 | **Score-only**, fresh inventory, gated on all four error/limitation lists |
| persist | 789-793 | Flush centroids/governance; no outcome write |
| twin/budget/extinction checks | 794-851 | Read/validate; preserve S2P-09/07/05 |
| report/exit | 853,866-869 | Any error/limitation produces nonzero exit |

Thus “every scored row is learned” describes the intended successful training phases, not current entire `run()`. Errors can also leave training rows unresolved; they are not a deliberately provisioned pending cohort.

F01 refreshes inventory at :479-492, preserves consumed slots :501-502, detects foreign provenance :499-500, queries again :549, and uses no learn call. In addition to the demonstrated empty-200 bug, multiple owned pending rows are treated as reusable (:498-505), and final counting (:560-567) filters to owned rows rather than rejecting a newly introduced foreign/mixed group. Specify duplicate/collision validation rather than hiding this behind an idempotency claim. Score dedup is client-side (:275-278); arbitrary concurrent writers cannot be made exactly-once by this script alone.

## Section 2: Decision store contract

Exact `s2p-copilot/backend/app/services/s2p_preview_data.py:68-85`:

```python
def _verified(row: dict[str, Any]) -> bool:
    return (row.get("status") in {"confirmed", "overridden", "verified"}
            or row.get("correct") is not None or row.get("is_correct") is not None
            or row.get("verified") is True or bool(row.get("actual_action")))

def read_decisions(store: Any, *, with_outcomes: bool = False) -> list[dict[str, Any]]:
    verified = _verified_rows(store)
    if isinstance(verified, tuple):
        verified = verified[0]
    return merge_decisions(store.get_all_decisions(domain="s2p"), verified)

def merge_decisions(rows: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize one shared history/outcome snapshot without additional IO."""
    outcome_by_id = {row.get("decision_id"): row for row in outcomes if row.get("decision_id") is not None}
    return [_decision_data({**row, **outcome_by_id.get(row.get("decision_id"), {})})
            for row in rows if row.get("domain", "s2p") == "s2p" and not row.get("archived")]
```

`_decision_data` :53-56 flattens metadata, row, outcome metadata/context and row context. Keep forbidden verification fields out of **all** payload/context/metadata layers, not just top level. `with_outcomes` does not control merging. `_verified_rows` :28-40 returns empty on some graph errors; do not interpret that fallback as authoritative proof of no outcomes.

**Score-only safe: YES for the specified normal planted payload, if scoring commits successfully.** `s2p.py:2409-2411` labels explicit demo origin as planted/T_S; :2477-2483 invokes SDK score with invoice metadata. SDK `scorer.py:813-833` persists a Decision; S2P active adapter `app/s2p_graph_status.py:247-307` writes a governed Decision; AGE `age_graph_store.py:1148,1323` initializes status pending. No learning/outcome call occurs in this score handler, including its enrichment tail. Response `learning_applied=False` at :2506 is not a `_verified` input. Auto-approve enrichment records telemetry; it does not confirm this Decision.

Absent joined outcomes leave actual_action/is_correct as None (`age_graph_store.py:4523-4524`); no default False is inserted to accidentally verify a score-only row. Persist (`s2p_demo_control.py:145-174`) updates centroid cells and governance only.

Example slot 1 carries event/invoice identity, supplier identity/name, amount 1000, category price_variance, eight factors, inverse supplier risk, and context origin/planted/T_S. Stored Decision adds decision_id, domain, category/index, recommended action/index, confidence/probabilities, factor vector/names, timestamp/source/schema/scorer versions and metadata. Status is pending; correct absent/None; is_correct absent/None; verified absent; actual_action absent/None. All five `_verified` branches are false. Request outcome/status/correct/is_correct/verified/actual_action/ground_truth_action must remain absent. Recommendation is not actual action.

Learning is different: preseed :301-306 sends decision_id, actual_action, outcome=confirmed and synthetic context; :660-665 sends explicit review outcomes. The learn route :2797-2966 validates/gates, writes through scorer learn/audit, persists L5, invalidates projections and returns learning_applied. Verified status and actual action/correctness then remove rows from the pending predicate. RED/AMBER gate rejection does not justify fabricating an ID or outcome.

### Queue chain and data lifecycle map

```text
preseed _pending_cohort_payload:189 -> _score:269 (context provenance, no learn)
 -> POST /api/s2p/score (s2p.py:2365)
 -> governance:2458 [RED -> 503; AMBER -> held/no Decision ID]
 -> SDK score -> GraphStore governed Decision(status=pending)
 -> GET preview/queue (s2p_preview.py:372-375; mounted main.py:640)
 -> _preview_queue_payload:315 -> queue_read_payload:322
 -> cached_response OR read_decisions:74 -> merge_decisions:81
 -> score_pending:261: not _verified(row)
 -> newest PENDING row per invoice, cap newest 50, read-only score, confidence sort
 -> queue_payload:333 -> default first 5 (limit can request 50)
 -> observation once per invoice:203 [ancillary graph write, not verification]
 -> API getPreviewQueue:134 -> TriageScreen:116-140
 -> exceptions[0] selection -> buttons:286 -> analyst score/learn
```

Exact filter is `pending = sorted((row for row in decisions if not _verified(row)), key=lambda row: (_timestamp(row), str(row["decision_id"])), reverse=True)` (:262-263). `_pending_decisions` :251 exists but is **not called by this active chain**; there is no double filtering through that helper. Empty list returns gracefully: total/scored_count/showing=0, invoices/exceptions=[], zero averages, status=ok, engine/scorer metadata. The route turns unexpected errors into HTTP500 (:378-384).

Materializer uses app-owned snapshots, default TTL30s (`response_materializer.py:18-35`), generation fencing at :65 and clearing on invalidate :105-109. Score invalidates at `s2p.py:2578`; learn at :2947. During background refresh, `get()` can return a previously published payload (:81-85). Stale windows need bounded rechecking, not permanent-empty assumptions. No evidence that caching alone caused the recorded empty graph state.

Config `config.py:20-48,81-84`: shape **(5,5,8)**; categories **price_variance, quantity_mismatch, duplicate_risk, contract_gap, format_compliance**. Factors **match_status, amount_variance_ratio, duplicate_score, supplier_exception_history, payment_terms_impact, commodity_index_correlation, tax_regulatory_compliance, environmental_risk**. `format_compliance` is a category. Shared domain config can override lists, so retain deployed-schema validation.

## Section 3: E2E infrastructure

Use umbrella `copilot-sdk/e2e/playwright.config.ts`, not the local minimal config. S2P project :57-63 uses port5177, host from COPILOT_HOST, timeout60s/expect10s; global retries1/workers1 by default (:6-9), action10s (:18), retained failure traces. No project dependency is configured. Some specs override to150s/180s. Local `s2p/playwright.config.ts:3-6` instead sets30s/baseURL only; no warmup/retry setup. Record the exact invocation with every result.

`e2e/global-setup.ts:16-35` warms four frontend shells, catches warmup errors, and closes the browser. It does not seed S2P, validate a pending invoice, or establish backend readiness. The shared `fixtures/copilot-fixture.ts:5-10,135-185` supports trading/purchasing/dataops, not S2P; S2P specs import Playwright directly. Its request retries are therefore not S2P protection.

Shared helper expectations (complete source extracts are in the appendix):

| Helper | Location | Behavior / limitation |
|---|---|---|
| clickTab / gotoTab / navigateToTab | helpers/ui.ts:3,31,42 | Immediate tab/button count then fallback text; shell readiness not data readiness |
| waitForAppShell | helpers/ui.ts:19 | DOM/main present, nonempty, not basic loading text; no queue assertion |
| waitForScreenReady | helpers/ui.ts:38 | Checks main child ready attribute; only meaningful if screen sets it honestly |
| waitForTriageQueue | s2p/helpers.ts:5 | Accepts invoice OR “queued” OR “No invoice exceptions”; empty response passes |
| waitForS2PScoreResponse | s2p/helpers.ts:11 | Waits only POST with status200; 503/422 hidden behind timeout |
| clickAndWaitForS2PScore | s2p/helpers.ts:25 | Registers waiter with click, then result visible; does not validate ID/gate |
| collectConsoleErrors / expectNoConsoleErrors | helpers/ui.ts | Collects page errors and selected responses; broad known-error filters can hide faults |
| local clickScore / ensureSelectedInvoice | flows:65; phase1:48; triage:40; active-age:45; shadow:36 | Immediate count then skip or eventual assertion; inconsistent policies |
| local openScoredTriage | new-surfaces:14; rule-vs-reasoning:22; sweep:14 | Requires pre-existing selection; sweep clicks without waiting for response |
| goToTriage / scoreSelected | situation-analyzer:17,41 | Empty-ready accepted, then missing first invoice fails |
| optionalEndpoint / fetchTransferStatus | factor-proposer:6; transfer-badge:14 | Health/404/network conditions return null and skip; acceptable only in explicitly optional capability runs |

No active S2P spec calls `request.post()` to create a real pending test cohort. Live cases score through UI and require pre-existing invoices. Many other specs use `page.route().fulfill()` fixtures (auto-approve, clustering, cross-copilot-signal, discovery, early-warning, evidence-chain, governance, novelty UI, observability, payment, process-fusion, simulation). Those mocks do not repair the live queue. Situation empty-context is a problematic hybrid.

The eight DNR are **3 Category, 4 Confidence, 5 Context chain, 6 Traversal depth, 7 Factors, 8 Provenance, 11 Partial context, 12 Sample provenance** at situation-analyzer:153,165,172,188,197,210,219,240. Results have workerIndex=-1 and duration0; beforeAll:124 calls goToTriage:128 then scoreSelected:129. First scored-state test receives setup failure. No cross-project dependency or missing-feature skip is needed to explain them.

`demo.py --preseed` does seed S2P: :945 builds jobs, :961 selects sibling script, :979 sets child seed environment, :989 launches script with timeout1800, :1000-1005 unconditionally restarts normal mode unless debug preseed-only. Seed mode has S2P_DEMO_MODE=true, normal mode removes mutation privileges while retaining read presentation. A normal-mode inventory403 is expected; do not weaken that guard. This sweep did not run preseed, restart services, or run mutating PW tests.

Post-F02 `api.ts` was read in full: 90 exports; 87 propagate via helpers/delegates, three special cases: conservation endpoint fallback (:182), learn timeout rethrow (:208), situation synthetic AbortError degradation (:257). Generic HTTP failures throw. Frontend handling is separate: Triage displays queue error (:129,280) but also sets fake zero internally (:132); Dashboard/Insight/Evidence lack catches as described. Ready attribute is immediate true in those three screens; Triage waits for queue settlement (:255), which includes error—not successful data availability.

## Section 4: Endpoint health

Bounded GET probes at approximately **2026-10-21T12:38–12:42Z**, no POSTs:

| Endpoint | Live result | Source/mount and interpretation |
|---|---|---|
| /health | HTTP200 status ok | main.py:719-721; health is not queue readiness |
| /api/s2p/preview/queue?limit=50 | HTTP200 total=0 showing=0 invoices=[] | s2p_preview.py:372; main:640; valid empty shape, wrong acceptance prerequisite |
| /api/demo/s2p/seed-decisions | HTTP403 | demo_control:103-109 require_demo:34; complete inventory intentionally inaccessible in normal mode |
| /api/s2p/centroid/all | HTTP200 factors=all8 | centroid router mounted main:617; deployed schema matches source |
| /api/conservation/status | HTTP200 GREEN, verified_count=321, total_decisions=321 | SDK conservation_router:47-93; main:510-514; counts support all current decisions verified, not independently audited raw inventory |
| /api/s2p/graph/status | HTTP200 structured diagnostic payload | app/s2p_graph_status.py:475-476; main:644 |
| /api/self/diagnostics | HTTP200 includes epsilon_firm | shared framework router mounted main:509 |
| /api/self/evolution/summary | HTTP200 schema_version field | shared framework router main:509; saved measurement tests passed |
| /api/s2p/suppliers | HTTP200 total=64 | suppliers router main:639; total includes live suppliers, not just ten fixtures |

The queue GET has a documented observation side effect for nonempty rows; the inspected live queue was empty, so its invoice loop did not write observations. No direct graph writes or lifecycle operations were performed. Live 200 does not establish every nested value or all future branches.

For the twelve failing tests the immediate endpoint is queue. Downstream endpoints were not reached by those failed flows; they must be tested after prerequisite repair:

| Route | Implementation / mount | Contract and failure handling |
|---|---|---|
| POST /api/s2p/score | s2p.py:2365-2737; main:599 | S2PScoreResponse, extra fields allowed; committed response has decision_id/action/confidence/factors. RED503; AMBER held response without ID. Do not treat any200 as a successful score |
| POST /api/learn | s2p.py:2797-2966; main:508 | GenericResponse permits extras; inspect learning_applied, gate, persistence. 200 may mean held/partial, not learned |
| POST /api/s2p/outcome | s2p.py:2969; main:599 | Alternative outcome path in test waiters; UI learn currently uses /api/learn |
| GET /api/s2p/situation/{decision_id} | s2p_situation.py:28-158; main:624 | Requires scored ID; graph failure503; unknown/unindexed entity returns explicit degraded dict; traversal timeout8s returns timeout dict; healthy dict has context_chain, explanation, confidence, factors, depth, provenance |
| GET /api/s2p/evidence/template | s2p_evidence router; main:623 | Selected invoice/category context, consumed through api.ts:234-239; not the saved failure point |
| GET /api/s2p/centroid/explain/{id} | centroid router; main:617 | Score-dependent explanation used by scored UI; validate with newly committed ID during application |

The source routers exist and are mounted. No evidence supports changing endpoint paths, fabricating invoice context, or replacing valid empty responses with fake invoices. F02 changes client error propagation, not route schemas. F03 changes fixture enrichment and provider mapping (:224-225,250-251), not queue verification. Live values win at provider:260-269; sample fallback stays labelled. Neither fix establishes a pending population.

## Design specifications

### DESIGN-1: Q/S data readiness — choose combined approach

Keep successful training phases and their causal measurements unchanged. Use the existing F01 dedicated score-only phase after review and before persist. This explicitly models unresolved analyst work without withholding outcomes needed for extinction, boundary calibration, or twin measurement. Restructuring those training phases risks S2P-05/07/09 and is unnecessary. E2E self-seeding alone leaves demo data wrong; avoid ad hoc backend writes in every test and automatic replenishment of consumed IDs.

Exact cohort contract (already implemented, retain):

* 24 IDs `S2P-INV-PENDING-V1-0001` through `0024`; suppliers `SUP-S2P-PENDING-V1-0001` through `0024`; names `Planted pending demo supplier 01` etc.
* Amount `1000.0 + 125.0*(i-1)`, range1000–3875. Six per category in this order: price_variance, quantity_mismatch, duplicate_risk, contract_gap. No format_compliance invoices.
* Eight factors in config order, values below; supplier_risk_rating=1-supplier_exception_history. No actual_action, outcome, status, correct, is_correct, verified or ground_truth_action in score inputs or nested context. Only synthetic origin/planted/T_S provenance.

| Category | match | amount variance | duplicate | supplier history | payment | commodity | tax | environmental |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| price_variance | .90 | .18 | .03 | .12 | .30 | .60 | .95 | .10 |
| quantity_mismatch | .55 | .08 | .04 | .15 | .35 | .75 | .95 | .15 |
| duplicate_risk | .90 | .02 | .75 | .10 | .25 | .80 | .95 | .10 |
| contract_gap | .65 | .10 | .05 | .20 | .55 | .40 | .80 | .20 |

Strengthen existing helper, not its scoring/learning APIs:

1. Fresh complete inventory before writes and after. For each reserved identity require exactly one owned row for reuse; mixed/foreign/duplicate identity is a limitation, never silently usable. Completed owned identity is consumed, never reopened. Single writer is a documented prerequisite; server does not promise unique invoice event IDs.
2. Distinguish response contract error from conservation block. Current :517 accepts every200-without-ID as blocked: instead require explicit held/gate/conservation evidence; malformed200 is error. Preserve errors across persistence. On timeout, read back once and reconcile; never blindly repeat POST.
3. Verify each created/reused slot exists, is pending, has matching identity/provenance and eight finite factors. New missing/verified/duplicate rows fail fresh-cohort acceptance. Consumed rows from previous runs remain consumed and count toward exhaustion, not a demand to rewrite them.
4. Queue probe must validate numeric total/showing, arrays and shape, and selectable pending identities. Retry empty/stale success as well as transient failure, up to3 reads 5s apart; on persistent disagreement add an explicit limitation with expected/observed IDs/counts. For an isolated fresh run require all24 owned IDs returned by `?limit=50` and exactly24 newly planted pending identities. Do not require global total=24 if other legitimate invoices exist. If >50 pending rows obscure owned IDs, report coverage unavailable; do not silently claim all24 visible or invent pagination.
5. Preserve minimum2 contract for a consumed cohort's interactive demo, but distinguish `partial` from fresh acceptance. A full E2E pass needs a larger capacity check: eight learn-submitting cases exist (active-age1, shadow1, flows3, triage3), potentially16 attempts with retries1. Require at least18 genuinely unresolved owned identities before full live acceptance, preferably24 fresh. If exhausted, use an explicitly isolated/reset test graph through existing approved lifecycle or a separately reviewed cohort-version change; never auto-reset outcomes.
6. No score-only cohort IDs enter review_decision_ids or special fixtures. Score doesn't move centroids, but does add telemetry/Decision population; assert frozen-twin review population and extinction evidence unchanged. Conservation remains authoritative; blocked seeding is a readiness failure, not a reason to bypass gates.

### DESIGN-2: E and frontend error contracts

No independent endpoint failure accounts for a saved twelve-failure result. Do not invent endpoint fixes. Add contract verification after cohort application: queue success nonempty vs success empty vs error; score success with ID vs held/noID vs503; learn learning_applied true vs held/partial; situation full/degraded/HTTP error. Preserve HTTP semantics and synthetic provenance.

Implement explicit request state at Dashboard, Insight, Evidence and Triage conservation consumers. Model loading/success/error independently per resource; only success with empty array means empty. Render visible retry/error state and no invented numeric zero on error. Dashboard resources must settle independently (one failure must not discard the other's success). Guard stale responses/unmounts. `data-screen-ready` means essential requests have settled, accompanied by `data-load-state=loading|ready|empty|error`; helpers must check state, not ready alone. Retain F02 rejection and situation timeout contract. SituationPanel already catches errors; do not revert it to null-swallowing.

Conservation fallback remains a separate truthfulness concern: preview returns `source=illustration`, GREEN/1000 (:402-408), and Dashboard calls it directly. Specify operational display from authoritative conservation status; if illustration is retained, visibly label it and never use it for readiness. Normalize verified_count explicitly for display, rather than changing graph gates or pretending illustration is live. Cover first-endpoint error plus illustrative fallback so it cannot produce operational GREEN unnoticed.

### DESIGN-3: L and conditional rendering

The invoice buttons and feature panels exist; don't add duplicate test IDs to fix empty data. Fix rule-vs-reasoning:73 to wait for either settled explanation or explicit unavailable state, then branch on the rendered state before reading blockquote. Mock successful, timeout/degraded, and HTTP503 responses separately. Retain exact comparison to situation explanation when present.

Make situation edge-case tests deterministic by mocking queue + score + situation together. Empty-queue test must assert empty UI; error-queue test must assert error UI. Neither should invoke the nonempty live helper. Supplier seasonal test must await profile success/empty/error and assert the corresponding panel; a missing panel still fails. Do not label the unobserved supplier flake fixed by F03 without a reproduced trace.

Update factor panel label to schema-derived count (8) or “Factor Reasoning,” and assert environmental_risk where full schema is expected. Preserve explicit smaller mock fixtures only when testing a partial-data state; label them as such.

### DESIGN-4 and DESIGN-5: C root and D07 helpers

Create one S2P-specific required-queue helper in `e2e/s2p/helpers.ts`, used by the six failed specs plus triage/phase1/flows. Register response listener before navigation; inspect actual queue HTTP status and body, then wait for selectable invoice button with expected ID. Reject empty, malformed or error responses with URL/status/count/context in the error. No arbitrary sleeps and no skip for a required seeded-stack precondition. Preserve explicit mocked-empty tests and genuine optional-capability suites with named skip reasons.

Score/learn helpers must listen by endpoint+method regardless of status; inspect body/status immediately. Require score ID, canonical action and finite confidence, and learn learning_applied=true for success-path tests. A held response should fail success setup with its reason, while dedicated governance tests assert held behavior. Don't accept unchanged controls as evidence of learning. Don't automatically retry writes after timeout.

Budget helpers within total test/setup timeout. For live score path allow90s, queue≤30s under healthy seeded stack, learn≤90s; a combined score+learn test needs explicit aggregate budget (e.g.240s), not a90s helper inside60s test. Mocked tests stay short. Keep one worker for destructive shared live flows unless identities are allocated independently.

For situation serial block, fixing queue setup removes this observed cascade. Prefer independent per-test mocked scored-state fixtures for presentation assertions plus one separate real score/situation contract smoke. This avoids nine tests depending on a single expensive mutation. If serial live group is retained temporarily, require queue validation in beforeAll with a clear aggregate setup timeout and explicit failure artifact; never convert absence of backend to an early-return pass.

Invoice completion is a separate small backend/frontend change: define latest row per invoice before checking verification, with deterministic timestamp/ID ordering. A newer verified Decision closes older pending projections; a genuinely newer pending decision represents reopened work. Add mixed-history tests before changing this policy, and preserve graph history. Refresh queue/conservation after acknowledged learning, clear resolved selection, surface refresh errors. Do not depend on old pending duplicates to satisfy tests.

## Build prompt plan

Five focused prompts; model names are requested planning choices, not claims of access to a particular model. Use **Sol, high reasoning** for each implementation; use **Astra, high reasoning** for review if ambiguity arises. Each prompt must preserve unrelated dirty changes and update its own repository session log.

| ID | Scope and exact files | Pre/post checks and tests | Dependency / expected outcomes |
|---|---|---|---|
| F04-A | S2P preseed readiness repair: scripts/preseed_s2p_demo.py; tests/test_pending_cohort.py; tests/test_preseed_phase_order.py only if needed | Pre: existing helper/schema/order. Add tests empty200, malformed200, delayed populated response, final missing row, duplicate/mixed ownership, timeout commit/no commit, explicit blocked response. Post: no learn calls, no new endpoint, boundaries before freeze, idempotent fresh/rerun, mypy/root tests. Target8 new cases | First. Repairs false-ready; does not itself change live counts. Apply via existing controlled preseed later |
| F04-B | SDK E2E required-data and response helpers: e2e/s2p/helpers.ts plus active-age-smoke, new-surfaces, rule-vs-reasoning, shadow-smoke, situation-analyzer, sweep, flows, phase1, triage specs | Pre: archive current report. Unify selection/HTTP failures, eliminate fake learning success, isolate mocked situation cases; add6 mocked contract cases (empty/error/delayed queue, score503, held score, blocked learn). Verify no success-status-only waiters remain in changed helpers. Typecheck E2E and run focused mocks | After A contract. Addresses Q12/S14/C8 prerequisites; test behavior may reveal new real downstream failures |
| F04-C | SDK caller error/loading state: DashboardScreen, InsightScreen, EvidenceScreen, TriageScreen; relevant conservation panel/consumer; new e2e/s2p/error-states.spec.ts | Pre: F02 throws. Add6 parameterized cases: queue500 vs200empty across Dashboard/Insight/Evidence; add2 conservation cases authoritative failure and illustrative fallback. Verify error visible, zero not fabricated, independent resources settle, no unhandled rejection. tsc/build/focused PW | Can follow B; no backend write behavior changes. +8 tests; not represented as eight historical failures |
| F04-D | S2P invoice projection semantics: backend/app/routers/s2p_preview.py plus backend/tests/test_s2p_preview.py (locate existing projection tests before editing); SDK TriageScreen and triage acceptance | Pre: approve latest-invoice completion contract in this design; preserve history and GraphStore. Add4 backend histories: old pending/new verified; new pending after old verified; same timestamp tie; independent IDs; add1 mocked UI learn-refresh case. Verify refresh errors distinct, no stale selection; targeted backend tests and tsc/build | After A/B; prevents queue ghost rows and repeated consumption. +1 PW test; backend counts separate |
| F04-E | Controlled application and acceptance evidence, documentation only; no arbitrary seed/endpoint/selector changes | Pre: reviewed A-D diff, F03 present, isolated/approved demo stack and capacity. Existing lifecycle preseed, inspect pending_cohort and all S2P gates; normal restart; queue subset/count check; run umbrella S2P project, save JSON+HTML/traces with timestamp and commit/worktree manifest. Rerun failing subset and identify any new first failure. Never overwrite sole baseline artifact | Last. Target34 old nonpasses becoming executed successes, contingent on downstream validation. Stop and classify newly exposed errors; do not fabricate a green run |

Each is intended as a focused reviewable implementation unit, approximately30–60 minutes excluding full live suites. If caller inventory expands materially, split C rather than quietly leaving affected callers unhandled. No new dependencies. Broader durable receipts/static analytics/state cleanup from the earlier sweep remains separately scoped.

Expected count arithmetic: existing218 tests unchanged in identity; B +6, C +8, D +1 gives **233 tests** if those additions are used exactly. On a healthy fully provisioned stack the acceptance **target** is **233P / 0F / 0Fl / 0S / 0DNR** (original suite target218P). This is not a measured or guaranteed prediction: previously unreached assertions, governance holds, external context, and the unverified flaky claim prevent a defensible unconditional green forecast. Capability-based skips must be listed individually if the environment intentionally omits a capability. Do not claim219 original tests or count the alleged flake twice.

### Self-check and limits

* 34 actual nonpasses traced; alleged35th cannot be verified from available evidence. The requested “35 verified outcomes” check is **blocked by contradictory input**, not silently marked passed.
* All218 current test declarations are inventoried below with primary dependency category and source evidence. Category overlap is explained; the summary counts only nonpasses.
* All twelve actual first failures and eight DNR root are identified. Designs cover Q/S/C and independently found E/L risks without inventing endpoint failures.
* `_verified` is quoted from source; score-only safety applies to ordinary specified payload and successful guarded persistence, not arbitrary malicious nested metadata.
* F01 working-tree/HEAD/runtime distinctions are explicit. Existing ten tests do not cover empty200 queue validation; offline reproduction confirms the gap.
* No preseed, backend restart, POST, full backend suite or PW suite was run during this diagnostic. Prior pass counts are attributed to session records. Live GETs and one offline stubbed probe are the new runtime evidence.


## Appendix A: Complete active-spec inventory

Counts use anchored test declarations, excluding RegExp `.test()` calls. Skip counts are source call sites, not skipped-test counts.

| Spec | Lines | Tests | test.skip calls |
|---|---:|---:|---:|
| active-age-smoke.spec.ts | 121 | 1 | 1 |
| auto-approve.spec.ts | 135 | 5 | 0 |
| clustering.spec.ts | 165 | 6 | 0 |
| cohort-status.spec.ts | 61 | 8 | 0 |
| control_tower.spec.ts | 39 | 3 | 0 |
| counterfactual-c3.spec.ts | 16 | 1 | 1 |
| cross-copilot-signal.spec.ts | 130 | 3 | 0 |
| dashboard.spec.ts | 67 | 6 | 0 |
| day-zero.spec.ts | 66 | 3 | 1 |
| discovery.spec.ts | 189 | 8 | 0 |
| early-warning.spec.ts | 146 | 7 | 0 |
| evidence-chain.spec.ts | 176 | 9 | 0 |
| evidence.spec.ts | 40 | 3 | 0 |
| evolution.spec.ts | 48 | 4 | 0 |
| factor-proposer.spec.ts | 102 | 6 | 2 |
| flows.spec.ts | 318 | 16 | 1 |
| governance-panels.spec.ts | 191 | 8 | 0 |
| insight.spec.ts | 56 | 4 | 0 |
| moat-panels.spec.ts | 72 | 10 | 0 |
| new-surfaces.spec.ts | 58 | 3 | 1 |
| novelty.spec.ts | 78 | 6 | 0 |
| observability.spec.ts | 170 | 8 | 0 |
| payment.spec.ts | 268 | 7 | 0 |
| performance.spec.ts | 53 | 5 | 0 |
| phase1.spec.ts | 153 | 9 | 1 |
| process-fusion.spec.ts | 59 | 2 | 0 |
| pvg.spec.ts | 62 | 4 | 0 |
| rule-vs-reasoning.spec.ts | 83 | 4 | 1 |
| shadow-smoke.spec.ts | 117 | 1 | 3 |
| simulation-panels.spec.ts | 180 | 8 | 0 |
| situation-analyzer.spec.ts | 346 | 15 | 0 |
| suppliers.spec.ts | 99 | 9 | 0 |
| sweep.spec.ts | 90 | 9 | 0 |
| tabs.spec.ts | 41 | 6 | 0 |
| transfer-badge.spec.ts | 71 | 3 | 2 |
| triage.spec.ts | 158 | 8 | 1 |

Total: **218 declarations**. Inactive `_old.ts`/`_v1.ts` files excluded.

## Appendix B: Every current test and its primary dependency

For passed tests these categories describe dependencies, not failures. Mocked endpoint assertions do not establish live endpoint health. Nonpasses use observed outcome evidence from Section 0. Source evidence is literal; shared setup also applies.

| Spec:line | Test | Result | Category | Source evidence |
|---|---|---|---|---|
| active-age-smoke.spec.ts:79 | S2P active AGE test-mode smoke keeps UI flows working | unexpected | Q | :105 `await scoreFirstInvoice(page);` |
| auto-approve.spec.ts:86 | dashboard shows auto-approve panel | expected | L | :87 `await openDashboard(page);` |
| auto-approve.spec.ts:94 | auto-approve panel displays per-category thresholds | expected | L | :95 `await openDashboard(page);` |
| auto-approve.spec.ts:104 | auto-approve panel shows spot-check count and accuracy | expected | L | :105 `await openDashboard(page);` |
| auto-approve.spec.ts:114 | auto-approve expansion proof button is visible | expected | L | :115 `await openDashboard(page);` |
| auto-approve.spec.ts:121 | clicking expansion proof shows proof evidence | expected | L | :122 `await openDashboard(page);` |
| clustering.spec.ts:114 | suppliers screen shows clustering panel | expected | L | :115 `await openSuppliers(page);` |
| clustering.spec.ts:122 | clustering panel shows cluster cards with labels | expected | L | :123 `await openSuppliers(page);` |
| clustering.spec.ts:132 | high potential cluster displays high potential status | expected | L | :133 `await openSuppliers(page);` |
| clustering.spec.ts:139 | clustering shows consolidation savings | expected | L | :140 `await openSuppliers(page);` |
| clustering.spec.ts:148 | clustering shows total supplier count | expected | L | :149 `await openSuppliers(page);` |
| clustering.spec.ts:157 | clustering panel has no SOC vocabulary | expected | L | :158 `await openSuppliers(page);` |
| cohort-status.spec.ts:15 | S2P cohort status panel renders | expected | L | :16 `await gotoPanel(page);` |
| cohort-status.spec.ts:20 | S2P cohort status shows state badge | expected | L | :21 `await gotoPanel(page);` |
| cohort-status.spec.ts:25 | S2P instrument section always visible | expected | L | :26 `await gotoPanel(page);` |
| cohort-status.spec.ts:30 | S2P real section visible | expected | L | :31 `await gotoPanel(page);` |
| cohort-status.spec.ts:35 | S2P cohort-status endpoint returns valid shape | expected | E | :37 `const response = await request.get(\`${BACKEND}/api/s2p/cohort-status\`, { timeout: 30_000 });` |
| cohort-status.spec.ts:46 | S2P no synthetic lift displayed | expected | L | :47 `await gotoPanel(page);` |
| cohort-status.spec.ts:51 | S2P no INSUFFICIENT_DATA message | expected | L | :52 `await gotoPanel(page);` |
| cohort-status.spec.ts:56 | S2P no console errors on cohort panel | expected | L | :58 `await gotoPanel(page);` |
| control_tower.spec.ts:16 | dashboard shows Control Tower intent queue | expected | L | :17 `await page.goto("/");` |
| control_tower.spec.ts:25 | Control Tower exposes queue or empty state | expected | L | :26 `await page.goto("/");` |
| control_tower.spec.ts:31 | Control Tower dashboard copy has no SOC vocabulary | expected | L | :32 `await page.goto("/");` |
| counterfactual-c3.spec.ts:11 | counterfactual card visible on s2p | expected | L | :12 `await page.goto(FRONTEND);` |
| cross-copilot-signal.spec.ts:108 | Signal banner visible when cross_copilot_signal is in context | expected | Q | :110 `await score(page);` |
| cross-copilot-signal.spec.ts:117 | Signal banner hidden when no signals are returned | expected | Q | :119 `await score(page);` |
| cross-copilot-signal.spec.ts:124 | Signal banner shows supplier name | expected | Q | :126 `await score(page);` |
| dashboard.spec.ts:20 | Dashboard loads with exception queue | expected | L | :21 `await page.goto("/");` |
| dashboard.spec.ts:28 | Exception Triage screen loads | expected | L | :29 `await page.goto("/");` |
| dashboard.spec.ts:36 | Insight screen loads | expected | L | :37 `await page.goto("/");` |
| dashboard.spec.ts:44 | Evidence screen loads | expected | L | :45 `await page.goto("/");` |
| dashboard.spec.ts:52 | Suppliers screen loads with profiles | expected | L | :53 `await page.goto("/");` |
| dashboard.spec.ts:61 | Performance screen loads | expected | L | :62 `await page.goto("/");` |
| day-zero.spec.ts:21 | S2P dashboard shows the day-zero card | expected | L | :22 `await gotoDashboard(page);` |
| day-zero.spec.ts:28 | S2P day-zero state avoids magnitude claims and uses plain language | expected | E | :29 `await mockMeasurementState(page, {` |
| day-zero.spec.ts:50 | S2P measured state shows accuracy and IKS | expected | E | :51 `await mockMeasurementState(page, {` |
| discovery.spec.ts:119 | Evidence screen shows discovery panel | expected | L | :120 `await openEvidence(page);` |
| discovery.spec.ts:127 | discovery panel shows alert count | expected | L | :128 `await openEvidence(page);` |
| discovery.spec.ts:135 | discovery shows source badges | expected | L | :136 `await openEvidence(page);` |
| discovery.spec.ts:144 | discovery shows impact estimate | expected | L | :145 `await openEvidence(page);` |
| discovery.spec.ts:152 | Evidence screen shows disruption recovery panel | expected | L | :153 `await openEvidence(page);` |
| discovery.spec.ts:160 | disruption shows decreasing recovery times | expected | L | :161 `await openEvidence(page);` |
| discovery.spec.ts:169 | disruption shows cumulative savings | expected | L | :170 `await openEvidence(page);` |
| discovery.spec.ts:177 | discovery and disruption panels have no SOC vocabulary | expected | L | :178 `await openEvidence(page);` |
| early-warning.spec.ts:87 | Insight screen shows early warning panel | expected | L | :88 `await openInsight(page);` |
| early-warning.spec.ts:95 | early warning shows active warning count | expected | L | :96 `await openInsight(page);` |
| early-warning.spec.ts:104 | high risk supplier shows risk score | expected | L | :105 `await openInsight(page);` |
| early-warning.spec.ts:113 | warning shows signal breakdown | expected | L | :114 `await openInsight(page);` |
| early-warning.spec.ts:123 | warning shows recommendation | expected | L | :124 `await openInsight(page);` |
| early-warning.spec.ts:131 | early warning panel shows pattern label | expected | L | :132 `await openInsight(page);` |
| early-warning.spec.ts:138 | early warning panel has no SOC vocabulary | expected | L | :139 `await openInsight(page);` |
| evidence-chain.spec.ts:102 | Evidence shows Outcome Receipt Chain panel | expected | L | :103 `await openEvidence(page);` |
| evidence-chain.spec.ts:110 | Receipt Chain shows statistics or empty receipt-chain state | expected | L | :111 `await openEvidence(page);` |
| evidence-chain.spec.ts:120 | Receipt Chain shows chain validity | expected | L | :121 `await openEvidence(page);` |
| evidence-chain.spec.ts:128 | Evidence shows Audit Export panel | expected | L | :129 `await openEvidence(page);` |
| evidence-chain.spec.ts:136 | Audit Export has Generate Audit Pack button | expected | L | :137 `await openEvidence(page);` |
| evidence-chain.spec.ts:143 | Clicking Generate Audit Pack shows audit pack data or unavailable state | expected | L | :144 `await openEvidence(page, "with-receipts");` |
| evidence-chain.spec.ts:153 | Receipt Chain shows hash values if receipts exist | expected | L | :154 `await openEvidence(page, "with-receipts");` |
| evidence-chain.spec.ts:161 | Receipt Chain shows zero or empty state when no receipts exist | expected | L | :162 `await openEvidence(page);` |
| evidence-chain.spec.ts:169 | Evidence-chain panels have no SOC vocabulary | expected | L | :170 `await openEvidence(page, "with-receipts");` |
| evidence.spec.ts:18 | invoice audit trail shows decision chain | expected | L | :19 `await openEvidence(page);` |
| evidence.spec.ts:26 | rule lifecycle shows states | expected | L | :27 `await openEvidence(page);` |
| evidence.spec.ts:34 | compliance summary shows percentages | expected | L | :35 `await openEvidence(page);` |
| evolution.spec.ts:15 | Evolution panel is visible on Evidence | expected | L | :16 `await openEvidence(page);` |
| evolution.spec.ts:23 | Rule lifecycle shows states or empty state | expected | L | :24 `await openEvidence(page);` |
| evolution.spec.ts:31 | Evolution history shows events or empty state | expected | L | :32 `await openEvidence(page);` |
| evolution.spec.ts:39 | Evolution screen has no console errors | expected | L | :42 `await openEvidence(page);` |
| factor-proposer.spec.ts:65 | factor analysis endpoint returns array | expected | S | :67 `test.skip(!response, "S2P factor analysis endpoint not available");` |
| factor-proposer.spec.ts:73 | factor recommendations endpoint returns candidates | expected | S | :75 `test.skip(!response, "S2P factor recommendations endpoint not available");` |
| factor-proposer.spec.ts:81 | factor insight panel renders on Evidence tab | expected | L | :82 `await openEvidence(page);` |
| factor-proposer.spec.ts:86 | factor chart shows bars for each factor | expected | L | :87 `await openEvidence(page);` |
| factor-proposer.spec.ts:93 | replace candidate highlighted in amber | expected | L | :94 `await openEvidence(page);` |
| factor-proposer.spec.ts:98 | propose replacement returns recommendation | expected | L | :99 `await openEvidence(page);` |
| flows.spec.ts:91 | all 6 tabs load without blank screens | expected | L | :92 `await page.goto("/");` |
| flows.spec.ts:106 | Dashboard shows preview data from S2P backend | expected | L | :107 `await page.goto("/");` |
| flows.spec.ts:117 | full round-trip Dashboard to all screens to Dashboard | expected | L | :118 `await page.goto("/");` |
| flows.spec.ts:148 | triage select score confirm reward round trip | skipped | S | :155 `await clickScore(page);` |
| flows.spec.ts:161 | score learn round trip preserves conservation projection | skipped | S | :167 `await clickScore(page);` |
| flows.spec.ts:174 | triage to dashboard navigation keeps dashboard preview visible | expected | L | :175 `await page.goto("/");` |
| flows.spec.ts:188 | process context persists across reload after scoring | skipped | S | :193 `await clickScore(page);` |
| flows.spec.ts:206 | graded financial reward appears as decimal reward | skipped | S | :212 `await clickScore(page);` |
| flows.spec.ts:218 | Process-Tech Fusion story spans all S2P screens | expected | L | :219 `await page.goto("/");` |
| flows.spec.ts:240 | cross-graph insight shows supplier impact ranking | expected | L | :241 `await page.goto("/");` |
| flows.spec.ts:251 | evidence to performance connects compliance and conservation | expected | L | :252 `await page.goto("/");` |
| flows.spec.ts:263 | performance what-if shows projected values | expected | L | :264 `await page.goto("/");` |
| flows.spec.ts:272 | savings estimate is visible | expected | L | :273 `await page.goto("/");` |
| flows.spec.ts:281 | dashboard to triage drill-down path remains available | expected | L | :282 `await page.goto("/");` |
| flows.spec.ts:292 | all S2P screens survive page reload | expected | L | :293 `await page.goto("/");` |
| flows.spec.ts:307 | SOC vocabulary is absent from S2P remaining screens | expected | L | :308 `await page.goto("/");` |
| governance-panels.spec.ts:125 | Evidence shows Compliance Screening panel | expected | L | :126 `await openEvidence(page);` |
| governance-panels.spec.ts:133 | Compliance Screening shows SOX score | expected | L | :134 `await openEvidence(page);` |
| governance-panels.spec.ts:141 | Compliance Screening shows EU AI Act or Article 14 text | expected | L | :142 `await openEvidence(page);` |
| governance-panels.spec.ts:149 | Compliance Screening shows compliance rate or gap count | expected | L | :150 `await openEvidence(page);` |
| governance-panels.spec.ts:157 | Suppliers shows Supplier Rationalization panel | expected | L | :158 `await openSuppliers(page);` |
| governance-panels.spec.ts:165 | Supplier Rationalization shows grow maintain phase-out categories | expected | L | :166 `await openSuppliers(page);` |
| governance-panels.spec.ts:174 | Supplier Rationalization shows savings or recommendations | expected | L | :175 `await openSuppliers(page);` |
| governance-panels.spec.ts:183 | Governance panels have no SOC vocabulary | expected | L | :184 `await openEvidence(page);` |
| insight.spec.ts:18 | factor fingerprint shows seven S2P factors | expected | L | :19 `await openInsight(page);` |
| insight.spec.ts:34 | similar invoices list renders with distances | expected | L | :35 `await openInsight(page);` |
| insight.spec.ts:42 | cross-graph shows supplier and commodity impact correlation | expected | L | :43 `await openInsight(page);` |
| insight.spec.ts:50 | process signals show Celonis bottleneck data | expected | L | :51 `await openInsight(page);` |
| moat-panels.spec.ts:9 | SM-01 S14-CONTRAST renders the rule-vs-reasoning surface | expected | L | :10 `await openS2PTab(page, "Exception Triage");` |
| moat-panels.spec.ts:14 | SM-02 S2P-EXTINCT renders lifecycle stages | expected | L | :15 `await openS2PTab(page, "Evidence");` |
| moat-panels.spec.ts:21 | SM-03 S2P-TWIN renders the modeled baseline label | expected | L | :22 `await openS2PTab(page, "Performance");` |
| moat-panels.spec.ts:27 | SM-04 S2P-WHATIF renders the factor inspector | expected | L | :28 `await openS2PTab(page, "Insight");` |
| moat-panels.spec.ts:34 | SM-05 S2P-CONFIDENCE is visible on Performance | expected | L | :35 `await openS2PTab(page, "Performance");` |
| moat-panels.spec.ts:39 | SM-06 S2P-DAY0 renders the honest readiness statement | expected | L | :40 `await openS2PTab(page, "Dashboard");` |
| moat-panels.spec.ts:46 | SM-07 lifecycle panel exposes an unavailable empty state honestly | expected | L | :47 `await openS2PTab(page, "Evidence");` |
| moat-panels.spec.ts:51 | SM-08 twin panel distinguishes pilot target from measured evidence | expected | L | :52 `await openS2PTab(page, "Performance");` |
| moat-panels.spec.ts:56 | SM-09 readiness panel shows provenance and trust dimensions | expected | L | :57 `await openS2PTab(page, "Dashboard");` |
| moat-panels.spec.ts:64 | SM-10 moat panels are reachable across the S2P flow | expected | L | :65 `await openS2PTab(page, "Dashboard");` |
| new-surfaces.spec.ts:28 | Rule vs Reasoning contrast renders both decision columns | unexpected | Q | :29 `await openScoredTriage(page);` |
| new-surfaces.spec.ts:36 | S2P measurement endpoints return explainable payloads | expected | E | :38 `const diagnostics = await page.request.get(\`${S2P_API}/api/self/diagnostics\`);` |
| new-surfaces.spec.ts:53 | S2P triage flow reaches governed contrast after scoring | unexpected | Q | :54 `await openScoredTriage(page);` |
| novelty.spec.ts:43 | novelty status endpoint returns object | expected | E | :45 `const response = await page.request.get(\`${API_BASE}/api/s2p/novelty/status\`, { timeout: 45_000 });` |
| novelty.spec.ts:50 | novelty history endpoint returns array | expected | E | :52 `const response = await page.request.get(\`${API_BASE}/api/s2p/novelty/history\`, { timeout: 45_000 });` |
| novelty.spec.ts:57 | novelty rate endpoint returns rate value | expected | E | :59 `const response = await page.request.get(\`${API_BASE}/api/s2p/novelty/rate\`);` |
| novelty.spec.ts:64 | novelty alert banner renders on triage when active | expected | L | :65 `await openTriageWithNovelty(page, true);` |
| novelty.spec.ts:69 | novelty banner shows category and rate | expected | L | :70 `await openTriageWithNovelty(page, true);` |
| novelty.spec.ts:75 | novelty banner hidden when rate below threshold | expected | L | :76 `await openTriageWithNovelty(page, false);` |
| observability.spec.ts:96 | Dashboard shows Distribution Monitor panel | expected | L | :97 `await openDashboard(page);` |
| observability.spec.ts:104 | Distribution Monitor shows novelty rate and status | expected | L | :105 `await openDashboard(page);` |
| observability.spec.ts:114 | Distribution Monitor shows window information | expected | L | :115 `await openDashboard(page);` |
| observability.spec.ts:124 | Insight shows Centroid Explorer panel | expected | L | :125 `await openInsight(page);` |
| observability.spec.ts:132 | Centroid Explorer shows Factor Weights section | expected | L | :133 `await openInsight(page);` |
| observability.spec.ts:141 | Centroid Explorer shows Centroids by Action | expected | L | :142 `await openInsight(page);` |
| observability.spec.ts:150 | Centroid Explorer has category selector | expected | L | :151 `await openInsight(page);` |
| observability.spec.ts:162 | Observability panels have no SOC vocabulary | expected | L | :163 `await openDashboard(page);` |
| payment.spec.ts:207 | Suppliers screen shows payment strategy panel | expected | L | :208 `await openSuppliers(page);` |
| payment.spec.ts:215 | payment strategy shows total discount opportunity | expected | L | :216 `await openSuppliers(page);` |
| payment.spec.ts:223 | payment strategy shows strategy breakdown | expected | L | :224 `await openSuppliers(page);` |
| payment.spec.ts:232 | early-pay suppliers show discount amounts | expected | L | :233 `await openSuppliers(page);` |
| payment.spec.ts:242 | extend suppliers show DPO improvement | expected | L | :243 `await openSuppliers(page);` |
| payment.spec.ts:251 | payment strategy table has all suppliers analyzed | expected | L | :252 `await openSuppliers(page);` |
| payment.spec.ts:260 | payment panel has no SOC vocabulary | expected | L | :261 `await openSuppliers(page);` |
| performance.spec.ts:18 | trajectory chart or empty learning curve renders | expected | L | :19 `await openPerformance(page);` |
| performance.spec.ts:24 | conservation shows penalty verified or accuracy | expected | L | :25 `await openPerformance(page);` |
| performance.spec.ts:30 | what-if simulator has controls | expected | L | :31 `await openPerformance(page);` |
| performance.spec.ts:38 | operational summary shows metrics and savings | expected | L | :39 `await openPerformance(page);` |
| performance.spec.ts:46 | dashboard mini process context shows bottleneck | expected | L | :47 `await page.goto("/");` |
| phase1.spec.ts:72 | test_s2p_triage_renders_invoice_queue | expected | L | :73 `await openTriage(page);` |
| phase1.spec.ts:79 | test_s2p_triage_select_invoice_shows_factors | skipped | S | :86 `await clickScore(page);` |
| phase1.spec.ts:93 | test_s2p_triage_score_produces_result_card | skipped | S | :94 `await scoreFirstInvoice(page);` |
| phase1.spec.ts:101 | test_s2p_insight_renders_fingerprint | expected | L | :102 `await openTab(page, "Insight");` |
| phase1.spec.ts:110 | test_s2p_evidence_renders_template | expected | L | :111 `await openTriage(page);` |
| phase1.spec.ts:118 | test_s2p_evidence_renders_compliance | expected | L | :119 `await openTab(page, "Evidence");` |
| phase1.spec.ts:127 | test_s2p_performance_renders_trajectory | expected | L | :128 `await openTab(page, "Performance");` |
| phase1.spec.ts:134 | test_s2p_performance_renders_savings | expected | L | :135 `await openTab(page, "Performance");` |
| phase1.spec.ts:142 | test_s2p_override_shows_reason_dropdown | skipped | S | :143 `await scoreFirstInvoice(page);` |
| process-fusion.spec.ts:46 | process fusion panel visible on insight | expected | L | :47 `await openInsight(page);` |
| process-fusion.spec.ts:52 | process fusion shows four sections | expected | L | :53 `await openInsight(page);` |
| pvg.spec.ts:16 | dashboard shows financial impact | expected | L | :17 `await page.goto("/");` |
| pvg.spec.ts:33 | insight shows leakage detection | expected | L | :34 `await page.goto("/");` |
| pvg.spec.ts:43 | performance shows cycle-time signal or unavailable state | expected | L | :44 `await page.goto("/");` |
| pvg.spec.ts:53 | PVG screens have no SOC vocabulary | expected | L | :54 `await page.goto("/");` |
| rule-vs-reasoning.spec.ts:47 | test_contrast_panel_visible_on_triage | unexpected | Q | :48 `await openScoredTriage(page);` |
| rule-vs-reasoning.spec.ts:53 | test_contrast_shows_two_columns | unexpected | Q | :54 `await openScoredTriage(page);` |
| rule-vs-reasoning.spec.ts:61 | test_contrast_shows_rule_threshold_and_outcome | unexpected | Q | :62 `await openScoredTriage(page);` |
| rule-vs-reasoning.spec.ts:68 | test_contrast_uses_situation_explanation_and_context | unexpected | Q | :69 `await openScoredTriage(page);` |
| shadow-smoke.spec.ts:71 | S2P AGE shadow smoke keeps UI flows working | unexpected | Q | :102 `await scoreFirstInvoice(page);` |
| simulation-panels.spec.ts:116 | Dashboard shows Disruption Simulation panel | expected | L | :117 `await openDashboard(page);` |
| simulation-panels.spec.ts:124 | Disruption Simulation shows scenario or empty unavailable state | expected | L | :125 `await openDashboard(page);` |
| simulation-panels.spec.ts:132 | Disruption Simulation shows conservation impact badges or summary counts | expected | L | :133 `await openDashboard(page);` |
| simulation-panels.spec.ts:140 | Disruption Simulation shows cost exposure and recovery information | expected | L | :141 `await openDashboard(page);` |
| simulation-panels.spec.ts:148 | Insight shows Cross-System Discovery panel | expected | L | :149 `await openInsight(page);` |
| simulation-panels.spec.ts:156 | Cross-System Discovery shows correlation or empty unavailable state | expected | L | :157 `await openInsight(page);` |
| simulation-panels.spec.ts:164 | Cross-System Discovery shows sources or type distribution | expected | L | :165 `await openInsight(page);` |
| simulation-panels.spec.ts:172 | Simulation and discovery panels have no SOC vocabulary | expected | L | :173 `await openDashboard(page);` |
| situation-analyzer.spec.ts:80 | 1. SituationPanel heading visible | expected | L | :81 `await goToTriage(page);` |
| situation-analyzer.spec.ts:87 | 14. Coexists with evidence panels | expected | L | :88 `await goToTriage(page);` |
| situation-analyzer.spec.ts:105 | 15. Page loads within 10 seconds | expected | Q | :129 `await scoreSelected(page);` |
| situation-analyzer.spec.ts:137 | 2. NL explanation renders | unexpected | Q | :137 `test('2. NL explanation renders', async () => {` |
| situation-analyzer.spec.ts:153 | 3. Category renders | skipped | C | :153 `test('3. Category renders', async () => {` |
| situation-analyzer.spec.ts:165 | 4. Confidence percentage renders | skipped | C | :165 `test('4. Confidence percentage renders', async () => {` |
| situation-analyzer.spec.ts:172 | 5. Context chain nodes visible | skipped | C | :172 `test('5. Context chain nodes visible', async () => {` |
| situation-analyzer.spec.ts:188 | 6. Traversal depth shown | skipped | C | :188 `test('6. Traversal depth shown', async () => {` |
| situation-analyzer.spec.ts:197 | 7. Factors list renders | skipped | C | :197 `test('7. Factors list renders', async () => {` |
| situation-analyzer.spec.ts:210 | 8. Provenance badges on values | skipped | C | :210 `test('8. Provenance badges on values', async () => {` |
| situation-analyzer.spec.ts:219 | 11. Partial context renders gracefully | skipped | C | :219 `test('11. Partial context renders gracefully', async () => {` |
| situation-analyzer.spec.ts:240 | 12. Sample provenance visually distinct | skipped | C | :240 `test('12. Sample provenance visually distinct', async () => {` |
| situation-analyzer.spec.ts:261 | 9. Heading renders when no exception selected | expected | E | :264 `await page.route(` |
| situation-analyzer.spec.ts:289 | 10. Graceful when backend unavailable | expected | L | :292 `await goToTriage(page);` |
| situation-analyzer.spec.ts:306 | 13. No crash on empty context | unexpected | Q | :336 `await scoreSelected(page);` |
| suppliers.spec.ts:29 | test_suppliers_screen_renders_profiles | expected | L | :30 `await openSuppliers(page);` |
| suppliers.spec.ts:37 | test_supplier_card_shows_exception_rate | expected | L | :38 `await openSuppliers(page);` |
| suppliers.spec.ts:43 | test_supplier_card_shows_otif | expected | L | :44 `await openSuppliers(page);` |
| suppliers.spec.ts:49 | test_supplier_trend_indicator_visible | expected | L | :50 `await openSuppliers(page);` |
| suppliers.spec.ts:55 | test_supplier_source_badge_visible | expected | L | :56 `await openSuppliers(page);` |
| suppliers.spec.ts:61 | test_declining_supplier_highlighted | expected | L | :62 `await openSuppliers(page);` |
| suppliers.spec.ts:72 | test_supplier_history_panel_renders | expected | L | :73 `await openSuppliers(page);` |
| suppliers.spec.ts:81 | Selecting supplier shows profile, seasonality, history, and heatmap | expected | L | :82 `await openSuppliers(page);` |
| suppliers.spec.ts:91 | Suppliers screen has no SOC vocabulary | expected | L | :92 `await openSuppliers(page);` |
| sweep.spec.ts:24 | Exception Triage mounts rule-vs-reasoning and situation panels | unexpected | Q | :25 `await openScoredTriage(page);` |
| sweep.spec.ts:31 | Insight mounts centroid explorer | expected | L | :32 `await openTab(page, "Insight");` |
| sweep.spec.ts:36 | Insight process fusion labels illustrative provenance | expected | L | :37 `await openTab(page, "Insight");` |
| sweep.spec.ts:41 | Evidence mounts audit, lifecycle, and compliance surfaces | expected | L | :42 `await openTab(page, "Evidence");` |
| sweep.spec.ts:46 | shared S2P endpoints expose explainable state | expected | E | :47 `const diagnostics = await request.get(\`${API}/api/self/diagnostics\`);` |
| sweep.spec.ts:58 | S2P control surfaces are reachable | expected | E | :59 `expect((await request.get(\`${API}/api/self/trust-traps\`)).status()).toBe(200);` |
| sweep.spec.ts:67 | governed triage: Exception Triage → score → rule/reasoning contrast | unexpected | Q | :68 `await openScoredTriage(page);` |
| sweep.spec.ts:74 | decision evidence: Insight → Evidence → supplier audit context | expected | L | :75 `await openTab(page, "Insight");` |
| sweep.spec.ts:82 | full S2P tab navigation remains error-free | expected | L | :83 `await page.goto("/");` |
| tabs.spec.ts:13 | Dashboard tab renders | expected | L | :14 `await openTab(page, /Dashboard/i);` |
| tabs.spec.ts:18 | Exception Triage tab renders | expected | L | :19 `await openTab(page, /Exception Triage/i);` |
| tabs.spec.ts:23 | Insight tab renders | expected | L | :24 `await openTab(page, /Insight/i);` |
| tabs.spec.ts:28 | Evidence tab renders | expected | L | :29 `await openTab(page, /Evidence/i);` |
| tabs.spec.ts:33 | Suppliers tab renders | expected | L | :34 `await openTab(page, /Suppliers/i);` |
| tabs.spec.ts:38 | Performance tab renders | expected | L | :39 `await openTab(page, /Performance/i);` |
| transfer-badge.spec.ts:35 | transfer status API returns valid shape | expected | S | :37 `test.skip(!status, "S2P transfer status endpoint not available");` |
| transfer-badge.spec.ts:43 | transfer status controls dashboard badge | expected | S | :45 `test.skip(!status, "S2P transfer status endpoint not available");` |
| transfer-badge.spec.ts:61 | dashboard with transfer badge has no console errors | expected | L | :64 `await page.goto(FRONTEND);` |
| triage.spec.ts:82 | invoice list loads from queue | expected | L | :83 `await openTriage(page);` |
| triage.spec.ts:90 | score button exists and scoring shows recommendation with confidence | skipped | S | :91 `await scoreFirstInvoice(page);` |
| triage.spec.ts:98 | factor breakdown shows S2P factors | skipped | S | :99 `await scoreFirstInvoice(page);` |
| triage.spec.ts:106 | process context shows bottleneck when available | skipped | S | :107 `await scoreFirstInvoice(page);` |
| triage.spec.ts:114 | confirm button records reward or confirmed result | skipped | S | :115 `await scoreFirstInvoice(page);` |
| triage.spec.ts:124 | override path records reward and learned text | skipped | S | :125 `await scoreFirstInvoice(page);` |
| triage.spec.ts:138 | conservation status visible after learn | skipped | S | :139 `await scoreFirstInvoice(page);` |
| triage.spec.ts:150 | S2P actions are canonical and SOC actions are absent | skipped | S | :151 `await scoreFirstInvoice(page);` |

## Appendix C: Endpoint literals and patterns in active specs

Includes mocked and live references. Patterns are not additional backend routes. Relative `/score` response matchers are covered by the helper analysis.

| Literal/pattern | Source locations |
|---|---|
| `${API_BASE}/api/s2p/novelty/history` | novelty.spec.ts:52 |
| `${API_BASE}/api/s2p/novelty/rate` | novelty.spec.ts:59 |
| `${API_BASE}/api/s2p/novelty/status` | novelty.spec.ts:45 |
| `${API_BASE}/health` | factor-proposer.spec.ts:7 |
| `${API}/api/conservation/status` | sweep.spec.ts:48 |
| `${API}/api/s2p/preview/queue` | sweep.spec.ts:60 |
| `${API}/api/self/diagnostics` | sweep.spec.ts:47 |
| `${API}/api/self/evolution/summary` | sweep.spec.ts:49 |
| `${API}/api/self/trust-traps` | sweep.spec.ts:59 |
| `${API}/health` | sweep.spec.ts:61 |
| `${BACKEND}/api/s2p/cohort-status` | cohort-status.spec.ts:37 |
| `${BACKEND}/api/transfer/status` | transfer-badge.spec.ts:17 |
| `${BACKEND}/health` | counterfactual-c3.spec.ts:7, day-zero.spec.ts:6, transfer-badge.spec.ts:15 |
| `${S2P_API}/api/conservation/status` | new-surfaces.spec.ts:42 |
| `${S2P_API}/api/self/diagnostics` | new-surfaces.spec.ts:38 |
| `${S2P_API}/api/self/evolution/summary` | new-surfaces.spec.ts:48 |
| `${S2P_API}/health` | new-surfaces.spec.ts:10 |
| `**/api/conservation/status` | cross-copilot-signal.spec.ts:25 |
| `**/api/s2p/auto-approve/expansion-proof?*` | auto-approve.spec.ts:52 |
| `**/api/s2p/auto-approve/stats` | auto-approve.spec.ts:27 |
| `**/api/s2p/discovery/alerts` | discovery.spec.ts:5 |
| `**/api/s2p/discovery/disruptions` | discovery.spec.ts:51 |
| `**/api/s2p/discovery/extended` | simulation-panels.spec.ts:56 |
| `**/api/s2p/enterprise/process-fusion` | process-fusion.spec.ts:7 |
| `**/api/s2p/evidence/audit-pack` | evidence-chain.spec.ts:58 |
| `**/api/s2p/evidence/chain-integrity` | evidence-chain.spec.ts:48 |
| `**/api/s2p/evidence/receipts?**` | evidence-chain.spec.ts:32 |
| `**/api/s2p/evidence/template?**` | cross-copilot-signal.spec.ts:28 |
| `**/api/s2p/explorer/dk-weights` | observability.spec.ts:55 |
| `**/api/s2p/explorer/drift/**` | observability.spec.ts:31 |
| `**/api/s2p/factors/analysis` | factor-proposer.spec.ts:15 |
| `**/api/s2p/factors/propose` | factor-proposer.spec.ts:43 |
| `**/api/s2p/governance/compliance-screening` | governance-panels.spec.ts:5 |
| `**/api/s2p/governance/rationalization` | governance-panels.spec.ts:51 |
| `**/api/s2p/measurement-state` | day-zero.spec.ts:16 |
| `**/api/s2p/novelty/status` | novelty.spec.ts:7, observability.spec.ts:5 |
| `**/api/s2p/novelty/triggered-decisions?**` | novelty.spec.ts:26 |
| `**/api/s2p/preview/conservation` | auto-approve.spec.ts:16 |
| `**/api/s2p/preview/queue` | auto-approve.spec.ts:4, cross-copilot-signal.spec.ts:5, early-warning.spec.ts:5 |
| `**/api/s2p/score` | cross-copilot-signal.spec.ts:48 |
| `**/api/s2p/simulation/impact-summary` | simulation-panels.spec.ts:40 |
| `**/api/s2p/simulation/scenarios` | simulation-panels.spec.ts:5 |
| `**/api/s2p/situation/**` | cross-copilot-signal.spec.ts:31 |
| `**/api/s2p/suppliers` | clustering.spec.ts:51, payment.spec.ts:165 |
| `**/api/s2p/suppliers/*/heatmap` | payment.spec.ts:158 |
| `**/api/s2p/suppliers/*/history?*` | clustering.spec.ts:90, payment.spec.ts:151 |
| `**/api/s2p/suppliers/clusters` | clustering.spec.ts:5, payment.spec.ts:131 |
| `**/api/s2p/suppliers/declining` | clustering.spec.ts:79, payment.spec.ts:144 |
| `**/api/s2p/suppliers/early-warnings` | early-warning.spec.ts:25 |
| `**/api/s2p/suppliers/payment-strategy` | payment.spec.ts:118 |
| `/api/learn` | active-age-smoke.spec.ts:38, flows.spec.ts:48, shadow-smoke.spec.ts:29, triage.spec.ts:33 |
| `/api/s2p/factors/analysis` | factor-proposer.spec.ts:66 |
| `/api/s2p/factors/recommendations` | factor-proposer.spec.ts:74 |
| `/api/s2p/outcome` | active-age-smoke.spec.ts:38, flows.spec.ts:48, shadow-smoke.spec.ts:29, triage.spec.ts:33 |
| `await page.route(/\/api\/s2p\/situation\/.+/, async (route) => {` | situation-analyzer.spec.ts:313 |
| `http://127.0.0.1:8002/api/s2p/graph/status` | active-age-smoke.spec.ts:84, shadow-smoke.spec.ts:83 |
| `http://127.0.0.1:8002/api/s2p/preview/queue` | situation-analyzer.spec.ts:265 |
| `http://127.0.0.1:8002/health` | active-age-smoke.spec.ts:82, rule-vs-reasoning.spec.ts:8, shadow-smoke.spec.ts:76, situation-analyzer.spec.ts:8 |

## Appendix D: Shared helper code and local helper inventory

### copilot-sdk/e2e/s2p/helpers.ts

```typescript
  1: import { expect, type Locator, type Page } from "@playwright/test";
  2: 
  3: // The live queue is graph-backed; loading 313 pending records can exceed the
  4: // former fixture-backed 15-second UI budget.
  5: export async function waitForTriageQueue(page: Page, timeout = 90_000) {
  6:   await expect(
  7:     page.locator("article").filter({ hasText: /Invoice Selector/i }),
  8:   ).toContainText(/S2P-INV|STRESS-CONC-S2P|INV-|demo-s2p|queued|No invoice exceptions/i, { timeout });
  9: }
 10: 
 11: export function waitForS2PScoreResponse(page: Page, timeout = 90_000) {
 12:   return page.waitForResponse(
 13:     (response) =>
 14:       response.url().includes("/score") &&
 15:       response.request().method() === "POST" &&
 16:       response.status() === 200,
 17:     { timeout },
 18:   );
 19: }
 20: 
 21: export function s2pScoreResult(page: Page) {
 22:   return page.locator("article", { hasText: /Action index|Recommendation/i }).first();
 23: }
 24: 
 25: export async function clickAndWaitForS2PScore(page: Page, scoreButton: Locator, timeout = 90_000) {
 26:   await Promise.all([
 27:     waitForS2PScoreResponse(page, timeout),
 28:     scoreButton.click(),
 29:   ]);
 30:   await expect(s2pScoreResult(page)).toBeVisible({ timeout: 30_000 });
 31: }
```

### copilot-sdk/e2e/helpers/ui.ts

```typescript
  1: import { expect, type Page } from "@playwright/test";
  2: 
  3: export async function clickTab(page: Page, name: string | RegExp) {
  4:   const tab = page.getByRole("tab", { name });
  5:   if (await tab.count()) {
  6:     await tab.first().click({ timeout: 20_000 });
  7:     return;
  8:   }
  9: 
 10:   const button = page.getByRole("button", { name });
 11:   if (await button.count()) {
 12:     await button.first().click({ timeout: 20_000 });
 13:     return;
 14:   }
 15: 
 16:   await page.getByText(name).first().click({ timeout: 20_000 });
 17: }
 18: 
 19: export async function waitForAppShell(page: Page, timeout = 25_000) {
 20:   await page.waitForLoadState("domcontentloaded", { timeout });
 21:   await page.locator("main").waitFor({ state: "attached", timeout });
 22:   await expect(page.locator("main")).not.toBeEmpty({ timeout });
 23:   await expect(page.locator("main")).not.toContainText(
 24:     /^P?PaperLoading (analysis|performance|dashboard|journal|trade detail)\.\.\.$/i,
 25:     { timeout },
 26:   );
 27: }
 28: 
 29: // Graph-backed DataOps panels can take longer than the former fixture-backed
 30: // responses to settle their first request.
 31: export async function gotoTab(page: Page, tabName: string, timeout = 30_000) {
 32:   await page.goto("/", { waitUntil: "domcontentloaded" });
 33:   await waitForAppShell(page, timeout);
 34:   await clickTab(page, tabName);
 35:   await waitForAppShell(page, timeout);
 36: }
 37: 
 38: export async function waitForScreenReady(page: Page, timeout = 30_000) {
 39:   await page.locator('main > [data-screen-ready="true"]').waitFor({ state: "attached", timeout });
 40: }
 41: 
 42: export async function navigateToTab(page: Page, tabName: string) {
 43:   await clickTab(page, tabName);
 44: }
 45: 
 46: export async function expectAnyText(
 47:   page: Page,
 48:   patterns: Array<RegExp | string>,
 49:   options: { timeout?: number } = {},
 50: ) {
 51:   const timeout = options.timeout ?? 10_000;
 52:   if (patterns.length === 0) throw new Error("No patterns");
 53:   let combined = page.getByText(patterns[0]).first();
 54:   for (let i = 1; i < patterns.length; i += 1) {
 55:     combined = combined.or(page.getByText(patterns[i]).first());
 56:   }
 57:   await expect(combined.first()).toBeVisible({ timeout });
 58: }
 59: 
 60: 
 61: export async function expectTrajectoryOrEmpty(page: Page) {
 62:   const chart = page.locator("main svg").first();
 63:   try {
 64:     await expect(chart).toBeVisible({ timeout: 2_000 });
 65:     return;
 66:   } catch {
 67:     await expectAnyText(page, [/No trajectory points available/i], { timeout: 8_000 });
 68:   }
 69: }
 70: 
 71: export function collectConsoleErrors(page: Page): string[] {
 72:   const errors: string[] = [];
 73:   page.on("response", (response) => {
 74:     if (response.status() === 503) {
 75:       errors.push(`HTTP 503 ${response.url()}`);
 76:     }
 77:   });
 78:   page.on("console", (message) => {
 79:     if (message.type() === "error") {
 80:       if (/Failed to load resource: the server responded with a status of 503/i.test(message.text())) {
 81:         return;
 82:       }
 83:       errors.push(message.text());
 84:     }
 85:   });
 86:   page.on("pageerror", (error) => {
 87:     errors.push(error.message);
 88:   });
 89:   return errors;
 90: }
 91: 
 92: export function expectNoConsoleErrors(errors: string[]) {
 93:   const unexpected = errors.filter(
 94:     (error) =>
 95:       !/api\/purchasing\/payment\/summary.*CORS|Failed to load resource: net::ERR_FAILED/i.test(error) &&
 96:       !/HTTP 503 .*\/api\/s2p\/control-tower\/queue\?/i.test(error),
 97:   );
 98:   expect(unexpected, `Unexpected browser console errors:\n${unexpected.join("\n")}`).toEqual([]);
 99: }
```

### Imports and local helper definitions

Local helper behavior is analyzed in Section 3; these definitions identify the exact source locations, including mock setup functions.

| Spec | Import / helper definition |
|---|---|
| active-age-smoke.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| active-age-smoke.spec.ts:2 | `import { clickTab, collectConsoleErrors, expectNoConsoleErrors, waitForAppShell } from "../helpers/ui";` |
| active-age-smoke.spec.ts:3 | `import { clickAndWaitForS2PScore } from "./helpers";` |
| active-age-smoke.spec.ts:7 | `function main(page: Page) {` |
| active-age-smoke.spec.ts:11 | `function panel(page: Page, text: string \| RegExp) {` |
| active-age-smoke.spec.ts:19 | `function scoreResultPanel(page: Page) {` |
| active-age-smoke.spec.ts:23 | `function recommendationControls(page: Page) {` |
| active-age-smoke.spec.ts:27 | `function waitForScoreResponse(page: Page) {` |
| active-age-smoke.spec.ts:36 | `function waitForLearnResponse(page: Page) {` |
| active-age-smoke.spec.ts:45 | `async function ensureSelectedInvoice(page: Page) {` |
| active-age-smoke.spec.ts:56 | `async function expectLearningResultOrStableControls(page: Page) {` |
| active-age-smoke.spec.ts:66 | `async function scoreFirstInvoice(page: Page) {` |
| auto-approve.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| auto-approve.spec.ts:3 | `async function mockDashboardApis(page: Page) {` |
| auto-approve.spec.ts:70 | `async function openDashboard(page: Page) {` |
| auto-approve.spec.ts:76 | `function autoApprovePanel(page: Page) {` |
| auto-approve.spec.ts:80 | `function expansionProofPanel(page: Page) {` |
| clustering.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| clustering.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| clustering.spec.ts:4 | `async function mockSupplierApis(page: Page) {` |
| clustering.spec.ts:101 | `async function openSuppliers(page: Page) {` |
| clustering.spec.ts:108 | `function clusteringPanel(page: Page) {` |
| cohort-status.spec.ts:1 | `import { test, expect } from "@playwright/test";` |
| cohort-status.spec.ts:2 | `import { clickTab, collectConsoleErrors, expectNoConsoleErrors, waitForAppShell } from "../helpers/ui";` |
| cohort-status.spec.ts:7 | `async function gotoPanel(page: import("@playwright/test").Page) {` |
| control_tower.spec.ts:1 | `import { test, expect } from "@playwright/test";` |
| control_tower.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| control_tower.spec.ts:4 | `function main(page: import("@playwright/test").Page) {` |
| control_tower.spec.ts:8 | `function panel(page: import("@playwright/test").Page, text: string \| RegExp) {` |
| counterfactual-c3.spec.ts:1 | `import { expect, test } from "@playwright/test";` |
| cross-copilot-signal.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| cross-copilot-signal.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| cross-copilot-signal.spec.ts:4 | `async function mockTriageApis(page: Page, withSignal: boolean) {` |
| cross-copilot-signal.spec.ts:85 | `async function openTriage(page: Page) {` |
| cross-copilot-signal.spec.ts:91 | `function waitForScoreResponse(page: Page) {` |
| cross-copilot-signal.spec.ts:100 | `async function score(page: Page) {` |
| dashboard.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| dashboard.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| dashboard.spec.ts:4 | `function main(page: Page) {` |
| dashboard.spec.ts:8 | `function panel(page: Page, text: string \| RegExp) {` |
| dashboard.spec.ts:16 | `function pageHeading(page: Page, text: string \| RegExp) {` |
| day-zero.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| day-zero.spec.ts:10 | `async function gotoDashboard(page: Page) {` |
| day-zero.spec.ts:15 | `async function mockMeasurementState(page: Page, payload: object) {` |
| discovery.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| discovery.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| discovery.spec.ts:4 | `async function mockDiscoveryApis(page: Page) {` |
| discovery.spec.ts:96 | `async function openEvidence(page: Page) {` |
| discovery.spec.ts:103 | `function panel(page: Page, text: string \| RegExp) {` |
| discovery.spec.ts:111 | `function discoveryPanel(page: Page) {` |
| discovery.spec.ts:115 | `function disruptionPanel(page: Page) {` |
| early-warning.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| early-warning.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| early-warning.spec.ts:4 | `async function mockInsightApis(page: Page) {` |
| early-warning.spec.ts:74 | `async function openInsight(page: Page) {` |
| early-warning.spec.ts:81 | `function earlyWarningPanel(page: Page) {` |
| evidence-chain.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| evidence-chain.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| evidence-chain.spec.ts:6 | `async function mockEvidenceChainApis(page: Page, mode: ReceiptMode = "empty") {` |
| evidence-chain.spec.ts:87 | `async function openEvidence(page: Page, mode: ReceiptMode = "empty") {` |
| evidence-chain.spec.ts:94 | `function panel(page: Page, text: string \| RegExp) {` |
| evidence.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| evidence.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| evidence.spec.ts:4 | `async function openEvidence(page: Page) {` |
| evidence.spec.ts:10 | `function panel(page: Page, text: string \| RegExp) {` |
| evolution.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| evolution.spec.ts:2 | `import { clickTab, collectConsoleErrors, expectNoConsoleErrors } from "../helpers/ui";` |
| evolution.spec.ts:4 | `async function openEvidence(page: Page) {` |
| evolution.spec.ts:11 | `function evolutionRegion(page: Page) {` |
| factor-proposer.spec.ts:1 | `import { test, expect, type APIRequestContext, type APIResponse, type Page } from "@playwright/test";` |
| factor-proposer.spec.ts:2 | `import { clickTab, waitForAppShell } from "../helpers/ui";` |
| factor-proposer.spec.ts:6 | `async function optionalEndpoint(request: APIRequestContext, path: string): Promise<APIResponse \| null> {` |
| factor-proposer.spec.ts:14 | `async function mockFactorApis(page: Page) {` |
| factor-proposer.spec.ts:57 | `async function openEvidence(page: Page) {` |
| flows.spec.ts:1 | `import { test, expect } from "@playwright/test";` |
| flows.spec.ts:2 | `import { clickTab, waitForAppShell } from "../helpers/ui";` |
| flows.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| flows.spec.ts:16 | `function main(page: import("@playwright/test").Page) {` |
| flows.spec.ts:20 | `function panel(page: import("@playwright/test").Page, text: string \| RegExp) {` |
| flows.spec.ts:28 | `function scoreResultPanel(page: import("@playwright/test").Page) {` |
| flows.spec.ts:32 | `function recommendationControls(page: import("@playwright/test").Page) {` |
| flows.spec.ts:36 | `function waitForScoreResponse(page: import("@playwright/test").Page) {` |
| flows.spec.ts:46 | `function waitForLearnResponse(page: import("@playwright/test").Page) {` |
| flows.spec.ts:55 | `async function expectLearningResultOrStableControls(page: import("@playwright/test").Page, expected: RegExp) {` |
| flows.spec.ts:65 | `async function clickScore(page: import("@playwright/test").Page) {` |
| flows.spec.ts:84 | `async function confirmRecommendation(page: import("@playwright/test").Page) {` |
| governance-panels.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| governance-panels.spec.ts:2 | `import { clickTab, waitForAppShell } from "../helpers/ui";` |
| governance-panels.spec.ts:4 | `async function mockComplianceApis(page: Page) {` |
| governance-panels.spec.ts:50 | `async function mockRationalizationApis(page: Page) {` |
| governance-panels.spec.ts:99 | `async function openEvidence(page: Page) {` |
| governance-panels.spec.ts:108 | `async function openSuppliers(page: Page) {` |
| governance-panels.spec.ts:117 | `function panel(page: Page, heading: string \| RegExp) {` |
| insight.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| insight.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| insight.spec.ts:4 | `async function openInsight(page: Page) {` |
| insight.spec.ts:10 | `function panel(page: Page, text: string \| RegExp) {` |
| moat-panels.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| moat-panels.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| moat-panels.spec.ts:4 | `async function openS2PTab(page: Page, tab: string) {` |
| new-surfaces.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| new-surfaces.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| new-surfaces.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| new-surfaces.spec.ts:9 | `async function requireS2P(page: Page) {` |
| new-surfaces.spec.ts:14 | `async function openScoredTriage(page: Page) {` |
| novelty.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| novelty.spec.ts:2 | `import { clickTab, waitForAppShell } from "../helpers/ui";` |
| novelty.spec.ts:6 | `async function openTriageWithNovelty(page: Page, active = true) {` |
| observability.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| observability.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| observability.spec.ts:4 | `async function mockObservabilityApis(page: Page) {` |
| observability.spec.ts:75 | `async function openDashboard(page: Page) {` |
| observability.spec.ts:81 | `async function openInsight(page: Page) {` |
| observability.spec.ts:88 | `function panel(page: Page, text: string \| RegExp) {` |
| payment.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| payment.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| payment.spec.ts:117 | `async function mockSupplierApis(page: Page) {` |
| payment.spec.ts:194 | `async function openSuppliers(page: Page) {` |
| payment.spec.ts:201 | `function paymentPanel(page: Page) {` |
| performance.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| performance.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| performance.spec.ts:4 | `async function openPerformance(page: Page) {` |
| performance.spec.ts:10 | `function panel(page: Page, text: string \| RegExp) {` |
| phase1.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| phase1.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| phase1.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| phase1.spec.ts:7 | `async function openTab(page: Page, name: string) {` |
| phase1.spec.ts:12 | `async function openTriage(page: Page) {` |
| phase1.spec.ts:18 | `function main(page: Page) {` |
| phase1.spec.ts:22 | `function panel(page: Page, text: string \| RegExp) {` |
| phase1.spec.ts:30 | `function scoreResultPanel(page: Page) {` |
| phase1.spec.ts:34 | `function recommendationControls(page: Page) {` |
| phase1.spec.ts:38 | `function waitForScoreResponse(page: Page) {` |
| phase1.spec.ts:48 | `async function clickScore(page: Page) {` |
| phase1.spec.ts:66 | `async function scoreFirstInvoice(page: Page) {` |
| process-fusion.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| process-fusion.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| process-fusion.spec.ts:6 | `async function mockFusion(page: Page) {` |
| process-fusion.spec.ts:38 | `async function openInsight(page: Page) {` |
| pvg.spec.ts:1 | `import { test, expect } from "@playwright/test";` |
| pvg.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| pvg.spec.ts:4 | `function main(page: import("@playwright/test").Page) {` |
| pvg.spec.ts:8 | `function panel(page: import("@playwright/test").Page, text: string \| RegExp) {` |
| rule-vs-reasoning.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| rule-vs-reasoning.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| rule-vs-reasoning.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| rule-vs-reasoning.spec.ts:7 | `async function backendHealthy(page: Page): Promise<boolean> {` |
| rule-vs-reasoning.spec.ts:12 | `function waitForScoreResponse(page: Page) {` |
| rule-vs-reasoning.spec.ts:22 | `async function openScoredTriage(page: Page) {` |
| shadow-smoke.spec.ts:1 | `import { expect, test, type Page } from "@playwright/test";` |
| shadow-smoke.spec.ts:2 | `import { clickTab, collectConsoleErrors, expectNoConsoleErrors, waitForAppShell } from "../helpers/ui";` |
| shadow-smoke.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| shadow-smoke.spec.ts:7 | `function main(page: Page) {` |
| shadow-smoke.spec.ts:11 | `function panel(page: Page, text: string \| RegExp) {` |
| shadow-smoke.spec.ts:19 | `function scoreResultPanel(page: Page) {` |
| shadow-smoke.spec.ts:23 | `function recommendationControls(page: Page) {` |
| shadow-smoke.spec.ts:27 | `function waitForLearnResponse(page: Page) {` |
| shadow-smoke.spec.ts:36 | `async function ensureSelectedInvoice(page: Page) {` |
| shadow-smoke.spec.ts:47 | `async function expectLearningResultOrStableControls(page: Page) {` |
| shadow-smoke.spec.ts:57 | `async function scoreFirstInvoice(page: Page) {` |
| simulation-panels.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| simulation-panels.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| simulation-panels.spec.ts:4 | `async function mockSimulationApis(page: Page) {` |
| simulation-panels.spec.ts:55 | `async function mockDiscoveryApis(page: Page) {` |
| simulation-panels.spec.ts:95 | `async function openDashboard(page: Page) {` |
| simulation-panels.spec.ts:101 | `async function openInsight(page: Page) {` |
| simulation-panels.spec.ts:108 | `function panel(page: Page, heading: string \| RegExp) {` |
| situation-analyzer.spec.ts:1 | `import { expect, type Page, test } from '@playwright/test';` |
| situation-analyzer.spec.ts:2 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from './helpers';` |
| situation-analyzer.spec.ts:6 | `async function s2pBackendUp(page: Page) {` |
| situation-analyzer.spec.ts:13 | `async function goToS2P(page: Page) {` |
| situation-analyzer.spec.ts:17 | `async function goToTriage(page: Page) {` |
| situation-analyzer.spec.ts:41 | `async function scoreSelected(page: Page) {` |
| situation-analyzer.spec.ts:56 | `function situationPanel(page: Page) {` |
| situation-analyzer.spec.ts:60 | `async function expectDownState(page: Page) {` |
| suppliers.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| suppliers.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| suppliers.spec.ts:4 | `async function openSuppliers(page: Page) {` |
| suppliers.spec.ts:10 | `function supplierCards(page: Page) {` |
| suppliers.spec.ts:14 | `function supplierList(page: Page) {` |
| suppliers.spec.ts:18 | `function historyPanel(page: Page) {` |
| suppliers.spec.ts:22 | `async function selectFirstSupplierIfPresent(page: Page) {` |
| sweep.spec.ts:1 | `import { test, expect } from "@playwright/test";` |
| sweep.spec.ts:2 | `import { clickTab, waitForAppShell } from "../helpers/ui";` |
| sweep.spec.ts:3 | `import { waitForTriageQueue } from "./helpers";` |
| sweep.spec.ts:7 | `async function openTab(page: import("@playwright/test").Page, name: string) {` |
| sweep.spec.ts:14 | `async function openScoredTriage(page: import("@playwright/test").Page) {` |
| tabs.spec.ts:1 | `import { expect, type Page, test } from '@playwright/test';` |
| tabs.spec.ts:3 | `async function goToS2P(page: Page) {` |
| tabs.spec.ts:8 | `async function openTab(page: Page, name: RegExp) {` |
| transfer-badge.spec.ts:1 | `import { test, expect, type APIRequestContext } from "@playwright/test";` |
| transfer-badge.spec.ts:2 | `import { collectConsoleErrors, expectNoConsoleErrors } from "../helpers/ui";` |
| transfer-badge.spec.ts:14 | `async function fetchTransferStatus(request: APIRequestContext): Promise<TransferStatus \| null> {` |
| transfer-badge.spec.ts:23 | `function assertTransferStatusShape(status: TransferStatus) {` |
| triage.spec.ts:1 | `import { test, expect, type Page } from "@playwright/test";` |
| triage.spec.ts:2 | `import { clickTab } from "../helpers/ui";` |
| triage.spec.ts:3 | `import { clickAndWaitForS2PScore, waitForTriageQueue } from "./helpers";` |
| triage.spec.ts:7 | `async function openTriage(page: Page) {` |
| triage.spec.ts:14 | `function panel(page: Page, text: string \| RegExp) {` |
| triage.spec.ts:22 | `function waitForScoreResponse(page: Page) {` |
| triage.spec.ts:31 | `function waitForLearnResponse(page: Page) {` |
| triage.spec.ts:40 | `async function clickScore(page: Page) {` |
| triage.spec.ts:58 | `function scoreResultPanel(page: Page) {` |
| triage.spec.ts:62 | `function recommendationControls(page: Page) {` |
| triage.spec.ts:66 | `async function expectLearningResultOrStableControls(page: Page, expected: RegExp) {` |
| triage.spec.ts:76 | `async function scoreFirstInvoice(page: Page) {` |

## Appendix E: Existing F01 implementation and symbol audit

Working-tree source: `s2p-copilot/scripts/preseed_s2p_demo.py`. This code predates this diagnostic.

### Full pending helper

```python
455: def _seed_pending_cohort(
456:     api: str,
457:     existing: dict[str, dict[str, Any]],
458:     report: dict[str, Any],
459: ) -> None:
460:     """Plant deterministic, score-only invoices that remain available to triage."""
461:     phase = _pending_cohort_report()
462:     report["pending_cohort"] = phase
463:     if any(report.get(key) for key in (
464:         "errors", "learning_errors", "investigation_errors", "limitations",
465:     )):
466:         phase["status"] = "skipped_training_failed"
467:         return
468: 
469:     config_status, config, config_error = _probe(api, "/api/s2p/centroid/all")
470:     deployed_factors = None if config is None else config.get("factors")
471:     if config_status != 200 or deployed_factors != list(PENDING_COHORT_FACTOR_NAMES):
472:         phase["status"] = "skipped_factor_schema_mismatch"
473:         report["limitations"].append(
474:             "S2P-F01: pending cohort factor schema mismatch "
475:             f"({config_status}: {config_error or deployed_factors})"
476:         )
477:         return
478: 
479:     try:
480:         initial_rows = _decision_rows(api)
481:     except ValueError as exc:
482:         phase["status"] = "inventory_failed"
483:         report["errors"].append({"phase": "pending_cohort", "detail": str(exc)})
484:         return
485:     grouped = _group_decisions(initial_rows)
486: 
487:     # Replace the caller's potentially stale map with the complete inventory
488:     # read immediately before this phase. _score() uses this map for dedup.
489:     existing.clear()
490:     for identity, rows in grouped.items():
491:         if len(rows) == 1:
492:             existing[identity] = rows[0]
493: 
494:     payloads = [_pending_cohort_payload(slot) for slot in range(1, PENDING_COHORT_COUNT + 1)]
495:     for payload in payloads:
496:         event_id = str(payload["event_id"])
497:         rows = grouped.get(event_id, [])
498:         if rows:
499:             if any(not _cohort_row_owned(row, payload) for row in rows):
500:                 phase["collision_ids"].append(event_id)
501:             elif any(_cohort_row_verified(row) for row in rows):
502:                 phase["consumed_ids"].append(event_id)
503:             else:
504:                 phase["reused_pending_ids"].append(event_id)
505:             continue
506: 
507:         errors_before = len(report["errors"])
508:         scored = _score(api, payload, existing, report)
509:         if scored is not None:
510:             phase["created_ids"].append(event_id)
511:             continue
512: 
513:         score_errors = report["errors"][errors_before:]
514:         score_error = score_errors[-1] if score_errors else {}
515:         score_status = score_error.get("status")
516:         detail = str(score_error.get("detail") or "").lower()
517:         is_gate_block = score_status == 200 or (
518:             score_status in {403, 503}
519:             and any(token in detail for token in ("conservation", "amber", "red", "blocked", "gate"))
520:         )
521:         if is_gate_block:
522:             del report["errors"][errors_before:]
523:             phase["blocked_ids"].append(event_id)
524:             continue
525: 
526:         # A timeout can occur after the graph commit. Reconcile the reserved
527:         # identity before any future rerun; never issue a blind retry here.
528:         if score_status == 0:
529:             try:
530:                 readback = _group_decisions(_decision_rows(api)).get(event_id, [])
531:             except ValueError as exc:
532:                 report["errors"].append({
533:                     "event_id": event_id,
534:                     "phase": "pending_cohort_readback",
535:                     "detail": str(exc),
536:                 })
537:                 phase["status"] = "inventory_failed"
538:                 break
539:             matching = [row for row in readback if _cohort_row_owned(row, payload)]
540:             if len(readback) == 1 and len(matching) == 1:
541:                 del report["errors"][errors_before:]
542:                 phase["created_ids"].append(event_id)
543:                 existing[event_id] = matching[0]
544:             elif len(readback) > 1:
545:                 phase["collision_ids"].append(event_id)
546:                 phase["status"] = "ambiguous_timeout_readback"
547:                 break
548: 
549:     try:
550:         final_rows = _decision_rows(api)
551:     except ValueError as exc:
552:         phase["status"] = "inventory_failed"
553:         report["errors"].append({"phase": "pending_cohort_verify", "detail": str(exc)})
554:         return
555:     final_grouped = _group_decisions(final_rows)
556:     pending_ids: list[str] = []
557:     invalid_vector_ids: list[str] = []
558:     for payload in payloads:
559:         event_id = str(payload["event_id"])
560:         owned_rows = [
561:             row for row in final_grouped.get(event_id, [])
562:             if _cohort_row_owned(row, payload)
563:         ]
564:         if owned_rows and not any(_cohort_row_verified(row) for row in owned_rows):
565:             pending_ids.append(event_id)
566:             if any(not _cohort_factor_vector_valid(row) for row in owned_rows):
567:                 invalid_vector_ids.append(event_id)
568:     phase["pending_count"] = len(pending_ids)
569: 
570:     if phase["collision_ids"]:
571:         report["limitations"].append(
572:             "S2P-F01: reserved pending-cohort IDs have conflicting provenance: "
573:             + ", ".join(phase["collision_ids"])
574:         )
575:     if invalid_vector_ids:
576:         report["limitations"].append(
577:             "S2P-F01: pending cohort rows have incomplete factor vectors: "
578:             + ", ".join(invalid_vector_ids)
579:         )
580:     if len(pending_ids) < 2:
581:         report["limitations"].append(
582:             f"S2P-F01: pending cohort exhausted; {len(pending_ids)} usable slots remain"
583:         )
584: 
585:     queue_ok = False
586:     for attempt in range(3):
587:         queue_status, queue, queue_error = _probe(api, "/api/s2p/preview/queue?limit=50")
588:         if queue_status == 200 and queue is not None:
589:             phase["queue_total"] = queue.get("total")
590:             phase["queue_showing"] = queue.get("showing")
591:             queue_ok = True
592:             break
593:         if attempt < 2:
594:             time.sleep(5)
595:     if not queue_ok:
596:         report["limitations"].append(
597:             f"S2P-F01: preview queue unavailable after 3 attempts ({queue_status}: {queue_error})"
598:         )
599: 
600:     if phase["status"] == "pending":
601:         phase["status"] = (
602:             "ready"
603:             if len(pending_ids) == PENDING_COHORT_COUNT
604:             and not phase["blocked_ids"]
605:             and not report["limitations"]
606:             and not report["errors"]
607:             else "partial"
608:         )
609: 
610: 
```

### All pending-symbol search matches

```text
33: PENDING_COHORT_VERSION = "v1"
34: PENDING_COHORT_COUNT = 24
35: PENDING_COHORT_CATEGORIES = (
41: PENDING_COHORT_FACTOR_NAMES = (
51: PENDING_COHORT_FACTORS: dict[str, dict[str, float]] = {
189: def _pending_cohort_payload(slot: int) -> dict[str, Any]:
190:     category = PENDING_COHORT_CATEGORIES[(slot - 1) % len(PENDING_COHORT_CATEGORIES)]
191:     factors = PENDING_COHORT_FACTORS[category]
193:         "event_id": f"S2P-INV-PENDING-V1-{slot:04d}",
196:         "supplier_id": f"SUP-S2P-PENDING-V1-{slot:04d}",
245:     if not isinstance(vector, list) or len(vector) != len(PENDING_COHORT_FACTOR_NAMES):
253: def _pending_cohort_report(status: str = "pending") -> dict[str, Any]:
255:         "version": PENDING_COHORT_VERSION,
256:         "target_count": PENDING_COHORT_COUNT,
455: def _seed_pending_cohort(
460:     """Plant deterministic, score-only invoices that remain available to triage."""
461:     phase = _pending_cohort_report()
462:     report["pending_cohort"] = phase
471:     if config_status != 200 or deployed_factors != list(PENDING_COHORT_FACTOR_NAMES):
483:         report["errors"].append({"phase": "pending_cohort", "detail": str(exc)})
494:     payloads = [_pending_cohort_payload(slot) for slot in range(1, PENDING_COHORT_COUNT + 1)]
534:                     "phase": "pending_cohort_readback",
553:         report["errors"].append({"phase": "pending_cohort_verify", "detail": str(exc)})
603:             if len(pending_ids) == PENDING_COHORT_COUNT
720:         report["planned_pending_scores"] = PENDING_COHORT_COUNT
725:             + PENDING_COHORT_COUNT
783:             report["pending_cohort"] = _pending_cohort_report("inventory_failed")
784:             report["errors"].append({"phase": "pending_cohort", "detail": str(exc)})
786:             _seed_pending_cohort(api, pending_existing, report)
788:         report["pending_cohort"] = _pending_cohort_report("skipped_training_failed")
```

