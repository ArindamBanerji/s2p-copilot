# S2P Diagnostic Sweep Findings

Date: 2026-10-21. Diagnostic only; no application, test, fixture, graph, or runtime changes.

Paths beginning `backend/`, `scripts/`, and `data/` are relative to s2p-copilot. `SDK/` means the sibling copilot-sdk repository; `AGE/` means ci-platform. Citations refer to the current working tree, which already contains uncommitted changes. Reported risks are distinguished from failures demonstrated by saved artifacts.

Session state read first: the S2P-09 ordering change is complete, with boundary learning before freeze and review learning last. Its recorded backend run was 2,314 passed, 1 skipped, 5 supplier-related failures. This sweep did not rerun that suite or Playwright and did not run preseed.

Evidence correction: the available `SDK/e2e/playwright-report/index.html` embedded report records **218 tests: 184 expected, 12 unexpected, 0 flaky, 22 skipped**, duration 792.548 seconds, starting 2026-10-21 08:28:45 UTC. The 22 comprise 14 explicit empty-queue skips and eight serial dependents that never ran. This matches the queue failure counts, but does **not** substantiate the reported extra supplier flaky test. Current source also has shifted line numbers. Do not combine totals from different runs.

## P1 — Must fix before next acceptance PW run

### S2P-D01 — Preseed supplies learning history but no usable pending population

- **File:line:** `scripts/preseed_s2p_demo.py:123,308,334,406,420,438`; `backend/app/services/s2p_preview_data.py:68`; `backend/app/routers/s2p_preview.py:261`.
- **Description/evidence:** `_learn()` sends `"actual_action": action` and `"outcome": "confirmed"`; every successful ordinary seed score is followed by learning. Review corrections also POST to learn directly. The queue selects `row for row in decisions if not _verified(row)`.
- **Root cause:** training/demo-evidence population and analyst work population are conflated. A successful run adds verified rows, not pending work. Saved active-AGE trace responses are genuinely HTTP 200 with `"status":"ok","source":"graph","total":0,"exceptions":[],"invoices":[]`; this particular failure is not a swallowed 500.
- **Recommended fix:** add an explicitly planted, bounded score-only queue population, separate from training, after the final learning phase. Preserve the extinction scenario by excluding format_compliance. Reconcile owned IDs against the live inventory; preserve completed outcomes, never unverify existing rows, and replenish consumed demo work deliberately rather than generating unlimited rows. Add preseed assertions for pending count and absence of verification fields, and an independent E2E setup check. Keep the S2P-09 ordering and honest delta gate.

### S2P-D02 — Request errors are represented as successful empty queue responses

- **File:line:** `SDK/apps/s2p/frontend/src/api.ts:134,147`; `screens/TriageScreen.tsx:118,255,280`.
- **Description/evidence:** `getPreviewQueue()` uses `.catch(() => ({ exceptions: [], total: 0, auto_approve_rate: 0, confidence_avg: 0 }))`. Triage calls the wrapper `fetchPreviewQueue()`, clears its error in `.then()`, and therefore displays an empty state after a 500/network failure.
- **Root cause:** the API layer destroys the distinction between an unavailable queue and a real empty queue. Most API wrappers similarly reduce failures to null; the full inventory is below.
- **Recommended fix:** use the already-existing strict queue getter for operational screens, retain HTTP/error details, add bounded requests and retry UI, and test 200-empty, 200-populated, 500, and network timeout separately. Migrate other wrappers by domain rather than changing all null contracts at once. This is independent of the demonstrated 200-empty failure.

### S2P-D03 — Supplier fixture no longer satisfies its enrichment contract

- **File:line:** `data/s2p_demo_suppliers.json:1`; `backend/tests/test_fixture_enrichment.py:108,124,137`; `test_g5_g6_aggregate_endpoints.py:59`; `test_supplier_aggregate.py:18`.
- **Description/evidence:** the current git diff removes `quarterly_otif`, `behavioral_scores`, `category_exception_rates`, and `monthly_volume` from supplier entries. For example, the removed SUP-001 quarterly OTIF values were 0.94, 0.90, 0.86, 0.82. The diff is 60 insertions / 340 deletions; some insertions simply relocate existing fields.
- **Root cause:** consumers/tests still require these fields, while trend aggregation depends on actual quarterly series, not the remaining `recent_trend` label. `routers/s2p_suppliers.py:384` reads `supplier.get("quarterly_otif")`; `services/supplier_profile_provider.py:224` projects it as `otif_by_quarter`.
- **Recommended fix:** reconcile the intended fixture contract with its owner. Restore coherent, explicitly sample enrichment data if removal was accidental; otherwise migrate the consumers and tests together to an honest unavailable-data contract. Do not weaken five assertions simply to obtain a pass. Independent of pending queue depletion; it also reduces supplier seasonality/heatmap/clustering evidence available to UI tests.

### S2P-D04 — Illustrative conservation is displayed as live operational health

- **File:line:** `backend/app/routers/s2p_preview.py:400`; `SDK/apps/s2p/frontend/src/api.ts:194`; `screens/DashboardScreen.tsx:26,109`; `components/ConservationMiniGauge.tsx:9`.
- **Description/evidence:** preview returns `"source": "illustration", "status": "GREEN", "accuracy": 0.84, "verified_decisions": 1000, "passed": True`. Dashboard loads this endpoint directly. The gauge renders `{verified} verified decisions` and status without consuming provenance. On failure of real `/api/conservation/status`, `getConservationStatus()` substitutes this preview endpoint.
- **Root cause:** a labelled illustrative backend payload loses its meaning at operational UI consumers; actual computed values coexist under different keys and do not replace the hardcoded GREEN/count.
- **Recommended fix:** operational dashboard/triage use authoritative conservation and explicit unavailable state; isolate and visibly label illustration in demo-only presentation. Never fall back from a failed live safety read to synthetic GREEN. The backend learn gates remain independent; this is misleading display, not evidence that the server bypasses its gate.

## P2 — Fix soon; intermittent failures or lifecycle correctness

### S2P-D05 — Learning does not complete the invoice queue lifecycle

- **File:line:** `backend/app/routers/s2p_preview.py:262-280`; `SDK/apps/s2p/frontend/src/screens/TriageScreen.tsx:178,218-252`.
- **Description:** dedup chooses the newest **pending** Decision for each invoice, after removing verified Decisions. Scoring from triage creates a new Decision; learning that new Decision does not resolve the older pending Decision. The old row can therefore reappear on reload. The current screen also just calls `setLearnResult(result)` after learning; it neither refreshes the queue nor removes the resolved selection.
- **Root cause:** Decision-history identity and invoice-work identity have no explicit completion relationship in this path.
- **Recommended fix:** define invoice completion/supersession semantics before changing dedup. Resolve or exclude superseded work using authoritative history, without hiding legitimate re-opened work; refresh queue after a confirmed commit. Add tests for two pending versions, newer verified version, and concurrent arrivals. This is a traced latent issue, not the cause of today's all-verified empty queue.

### S2P-D06 — Queue latency and stale snapshots can outlive test budgets

- **File:line:** `backend/app/services/response_materializer.py:42,53,79`; `s2p_preview_data.py:74`; `routers/s2p_preview.py:322`; `routers/s2p.py:2472`.
- **Description:** materialization scans all Decisions and verified Decisions; a cache miss falls back to another full live scan plus up to 50 scores. Refresh waiters use `wait_for(lambda: not self._refreshing)` without a deadline. While refreshing, `get()` returns the old payload before checking its age. Scoring's domain mutation lock is acquired without a timeout, so the per-invoice lock timeout is not an end-to-end request bound.
- **Root cause:** unbounded history reads, uncapped stale-while-refresh, and layered wait budgets. This reinforces the pool-pressure diagnosis already in session state; no new pool outage was induced here.
- **Recommended fix:** bounded live queue reads, single-flight refresh with deadlines and staleness metadata, capped stale responses, and explicit unavailable responses under storage failure. Keep generation-based invalidation. Test slow storage, direct seed writes, in-flight invalidation, and late commits after client timeout.

### S2P-D07 — E2E readiness helpers and assertions can hide or misdiagnose failures

- **File:line:** `SDK/e2e/s2p/helpers.ts:3`; `rule-vs-reasoning.spec.ts:23,73`; `sweep.spec.ts:13`; `suppliers.spec.ts:4,22`; `e2e/helpers/ui.ts:24`.
- **Description:** queue readiness accepts `queued` and `No invoice exceptions`, not selectable work. Helpers then use immediate `count()` and ID-name regexes rather than awaiting an available row. Most need only one row. Score response waiters accept only HTTP 200, so HTTP failures become long timeouts. Sweep asserts an always-mounted panel after clicking Score, without awaiting score completion. The rule-context test calls `textContent()` on a possibly absent `blockquote p` before reaching its intended unavailable-state branch.
- **Root cause:** structural shell readiness, data readiness, and successful operation are treated as interchangeable. Some tests permit unavailable/empty text as a passing business scenario.
- **Recommended fix:** shared helpers that check HTTP result, settled queue, at least one selectable row, and completed score/learn separately; use stable scoped selectors rather than invoice naming conventions. Match outer test deadlines to cumulative waits. Make negative/error-state tests deterministic using interception. Do not make required acceptance paths skip on missing seed data.

### S2P-D08 — Financial responses freeze at initial snapshot

- **File:line:** `backend/app/routers/financial_router.py:25,306,359`; `backend/app/main.py:661`; `routers/s2p.py:2947`.
- **Description/evidence:** `_ensure_financial_snapshots()` rebuilds only if `_FINANCIAL_SNAPSHOT is None or _FINANCIAL_SNAPSHOT_GRAPH_STORE is not graph_store`. Startup warms it. The learn path invalidates the response materializer and SDK caches, but no production call to `reset_financial_snapshots()` exists. Same-store new decisions/receipts do not refresh these globals.
- **Root cause:** process-global, non-versioned snapshots with no mutation invalidation/TTL. Multiple snapshots are assigned incrementally without an atomic publication lock.
- **Recommended fix:** app-owned versioned materialization sourced from live decisions and durable receipts; invalidate on relevant commits and publish related snapshots together. Test learn-after-first-GET and two app/store instances. Independent of queue population.

### S2P-D09 — Outcome receipt presentation is volatile and chain window handling is incorrect

- **File:line:** `backend/app/services/receipt_store.py:11,26,58,93`; `routers/s2p_evidence.py:326,364,374`; `routers/financial_router.py:115`; `routers/s2p.py:2932`.
- **Description:** the UI-oriented receipt store is a module-global in-memory deque with invoice/decision indexes, no lock, and no graph hydration found at startup. `add()` reads the tail/hash then appends in separate steps; concurrent post-lock learn completions can interleave. When the 10,000-row deque evicts its first element, `verify_chain()` still starts with `expected_previous = ""`, so the surviving first receipt's legitimate nonempty predecessor is treated as corruption.
- **Root cause:** a bounded presentation buffer is treated as a complete durable chain. This does not mean AGE EvidenceReceipt persistence itself is missing or corrupt: that is a separate writer.
- **Recommended fix:** read durable receipts for evidence/financial APIs, retain explicit chain-window predecessor/checkpoint, serialize append/index updates if an in-memory cache remains, and test restart, concurrent append, and capacity+1. Observed in source; concurrency/capacity failure not load-tested during this sweep.

### S2P-D10 — Projection trusts flattened metadata and malformed rows can break the entire queue

- **File:line:** `backend/app/services/s2p_preview_data.py:47-71`; `backend/app/routers/s2p_preview.py:231,262,378`.
- **Description:** `_decision_data()` merges context after authoritative row fields: `**outcome, **_mapping(outcome.get("context")), **_mapping(row.get("context"))`. Reserved fields such as status/correct/actual_action can therefore be shadowed if present in stored context. `_mapping()` calls `json.loads()` without handling invalid JSON; `_timestamp()` can throw on a malformed timestamp. One bad row aborts the full queue.
- **Root cause:** presentation flattening and verification classification share one unrestricted dictionary.
- **Recommended fix:** classify verification from authoritative Decision/Outcome fields before flattening; protect reserved fields, validate context, quarantine/report malformed rows with visible degraded metadata rather than silently dropping the whole queue. No evidence that current preseed sends colliding context fields; this is a latent compatibility/integrity hazard.

## P3 — Technical debt, not the current queue blocker

### S2P-D11 — Additional fixture-backed analytics need clearer contracts

- **File:line:** `backend/app/routers/s2p_data_helpers.py:22`; `s2p_insight.py:285,331`; `s2p_pvg.py:89,154,174,194`; `services/invoice_enricher.py:27`.
- **Description/root cause:** similar-invoice/process-context and PVG paths still read static invoices. PVG impact reports `ANNUAL_TARGET_USD = 680000.0` scaled as `total_savings`, not observed outcomes. PVG's production guard on `_load_candidate_json()` does not guard `load_invoices()`. Queue fixture enrichment fills missing sample attributes only after pending selection, and does not create live Decision rows.
- **Recommended fix:** explicitly separate demo targets/sample analytics from measured financial/decision history. Migrate operational readers to GraphStore; preserve sample provenance where fixtures are appropriate. This is not evidence that the real conservation gate reads JSON: its input path is live GraphStore. D04 is the concrete conservation-display violation.

### S2P-D12 — Remaining mutable singletons create app-isolation/restart debt

- **File:line:** `backend/app/routers/s2p_preview.py:27,203`; `s2p.py:235-275`; `s2p_evidence.py:41`; `services/novelty_tracker.py:158`; `supplier_profile_accumulator.py:525`; `s2p_auto_approve_gate.py:447`.
- **Description/root cause:** preview observation IDs are process-global, unbounded, unscoped to graph/app, and check/add is unsynchronized. GET queue also writes observations, so an observation-write error can turn a valid queue read into HTTP 500. Other globals include score caches/in-flight sets, DK tracker, executors, evidence context cache, supplier accumulator, novelty tracker and auto-approve gate. Locks on some globals do not provide app isolation or durability.
- **Recommended fix:** app-owned lifecycle/state, bounded namespaced caches, idempotent observation identity and explicit ancillary-write failure policy. Preserve genuine audit requirements. Treat constants/lookup tables and APIRouter registrations differently from runtime mutable state. D08/D09 cover the concrete high-impact instances separately.

### S2P-D13 — Preseed repeatability and stale helper/API contracts

- **File:line:** `scripts/preseed_s2p_demo.py:298,326,348`; `backend/app/routers/s2p_preview.py:251`; `services/s2p_preview_data.py:74`; `routers/s2p_clustering.py:17`.
- **Description/root cause:** boundary/review/confirmation events use fresh UUIDs on each run, so the whole preseed is not strictly idempotent despite its introductory claim; only stable-ID fixture phases resume. `_pending_decisions()` is unused, and `with_outcomes` does not change `read_decisions()` behavior. The only TODO/FIXME/HACK/WORKAROUND match in the requested directories is the clustering TODO to move behavioral helpers into services.
- **Recommended fix:** document deliberate bounded new learning episodes versus resumable fixture creation; add run identity/reporting. Remove or clarify unused compatibility interfaces in a separate cleanup. Do not change UUID semantics casually because repeated correction episodes affect twin measurements.

## Data Lifecycle Map and investigation answers

```text
POST /api/s2p/score
  -> compute factors + governance checks
  -> CompoundingScorer.score -> persist_prepared_score -> GraphStore Decision
     status=pending; action/recommendation/confidence/factors/metadata set
     actual_action/is_correct absent or None; no Outcome created
  -> [optional POST /api/learn]
     router RED/AMBER gate -> scorer conservation gate -> centroid update
     -> Outcome(actual_action,is_correct,actual_index,verified_at,metadata)
     -> Decision.status=confirmed/overridden; Decision.correct=bool
  -> AGE Decision/Outcome join
     no Outcome: actual_action=None,is_correct=None
     with Outcome: actual_action/is_correct/verified_at/outcome_metadata supplied
  -> preview merge_decisions by decision_id -> metadata/context flattening
  -> _verified:
     status confirmed/overridden/verified OR correct != None OR
     is_correct != None OR verified is True OR bool(actual_action)
  -> score_pending selects NOT verified -> latest pending per invoice
     -> newest 50 rescored read-only -> confidence rank -> default 5 displayed
  -> queue HTTP200 {exceptions,invoices,total,...}
  -> api.ts -> Triage: loading -> populated OR empty
     BUG: transport/HTTP errors are converted to empty before Triage catch
```

### Area 1: precise lifecycle answers

**1a. Learn payload.** `scripts/preseed_s2p_demo.py:131-136`:

```python
payload = {
    "decision_id": score.get("decision_id"),
    "actual_action": action,
    "outcome": "confirmed",
    "context": {"origin": "s2p_demo_preseed", "planted": True, "evidence_tier": "T_S"},
}
```

No status is sent. At :138-140, a successful applied learn increments the report and executes `score["status"] = "confirmed"` on the local response object. The backend determines persisted canonical status from correctness, not this local assignment.

**1b. Why verified becomes true.** AGE `ci_platform/graph/age_graph_store.py:1471-1477` uses `status = "confirmed" if bool(is_correct) else "overridden"`, then `d.status` and `d.correct` updates. Its :4517-4532 projection joins `actual_action`, `is_correct`, `actual_index`, `verified_at`, and `outcome_metadata`. Preview's exact predicate at :68-71 is:

```python
return (row.get("status") in {"confirmed", "overridden", "verified"}
        or row.get("correct") is not None or row.get("is_correct") is not None
        or row.get("verified") is True or bool(row.get("actual_action")))
```

The first status branch already succeeds for ordinary successful learns; correctness and actual_action also independently qualify. Incorrect/False correctness still means verified, not pending. An `outcome` string alone is not a branch in this preview predicate.

**1c. Every phase and exceptions.**

| Phase | Exact call/path | Pending escape |
|---|---|---|
| Special | :408 `_score(...)`; :410 `_learn(api, score, report)` | Failed/no-ID score; invalid action or failed learn; existing verified row skips network learn |
| Warmup | :416 `_score(...)`; :418 `_learn(...)` | Same exceptions |
| Extinction | :232 final `_score`; :237 learn owned pending rows; :246 learn final | :238-239 learning error returns; :243-245 concurrent/unrelated queue change defers final learn; foreign pending rows are intentionally not drained |
| Boundary | :302 score; :307 investigation; :308 learn | Candidate rejection occurs before score; investigation reports errors but normally continues to learn; exceptions/interruption can leave a scored row |
| Post-freeze | :440 score; :442 learn | Failed learn or interruption |
| Review | :329 score; :334 direct POST learn; :348 easy scores; :354 `_learn` | Review uses direct learn, not helper; failed application returns at :342; easy learn failures are reported |

Current run order is :420 extinction -> :421 boundary -> :422 early guard -> :425 freeze -> :431 re-freeze -> :438 post-freeze -> :444 review -> :445 persist. Thus “there is ANY path without `_learn()`” is **yes**, including direct review POST, error/concurrent-change/interruption paths, and already-verified reuse. There is **no intentional successful score-only queue phase**. Do not describe the script as unconditionally verifying every pre-existing invoice: unrelated pending rows are protected.

**1d. Math.** Let P be existing pending decisions, S successfully created training decisions, L those newly learned, and E old owned pending decisions resolved. Pending after run is P + S - L - E (ignoring concurrent arrivals and excluding archived/other-domain rows); queue total is then unique invoice IDs, not raw Decision count. Successful ordinary phases have S=L. On the previously diagnosed D=312/V=312 state, 312-312=0 pending, so dedup still yields 0. The saved queue traces independently confirm total=0. This is not a claim that every possible preseed run or the current live graph always has exactly 312 rows.

**1e. Score is not verification.** `routers/s2p.py:2480` calls `scorer.score(...)`; :2506 returns `"learning_applied": False`. SDK `scoring/scorer.py:810-834` persists prepared Decision data with `write_governed_decision` or `write_decision`, not `write_outcome`. AGE :1148/:1323 initialize `status: 'pending'`. Audit `outcome="pending"` at router :122 is an audit entry, not a verified Decision outcome. The special day-zero response (:2404) uses a disposable scorer and is explicitly nonpersistent; it cannot supply queue work.

**1f. Merge is universal, actual outcomes are not.** Preview :75 reads verified rows and :78 always merges (the with_outcomes flag is unused). :83 indexes real outcome rows by decision_id; :84 merges only a matching entry. AGE :4523-4524 injects `outcome.get("actual_action")` and `outcome.get("is_correct")`; absent Outcome means None, which does not trigger verification. Ordinary score-only rows are not promoted by this join. See D10 for metadata collision risk.

**1g. Persist does not verify decisions.** `routers/s2p_demo_control.py:145-176` takes `get_mutation_lock("s2p")`, saves a governance marker and `store.update_centroid(... caused_by_decision_id=None)`, validates the tensor, then saves a completed marker. It never writes Outcome or Decision verification fields. It cannot mark score-only rows verified. Separate read/read queue snapshots can race with a real concurrent learn; that is a consistency window, not persist manufacturing verification.

### Area 2: queue endpoint chain

**2a. Full active call chain:** `s2p_preview.py:373 preview_queue` -> :359 `_preview_queue_limit` -> :315 `_preview_queue_payload` -> :322 `queue_read_payload` -> `response_materializer.py:141 cached_response` -> :79 `ResponseMaterializer.get`. On miss: :325 `_get_scorer` (definition :69), :326 `_get_graph_store` (:73), `s2p_preview_data.py:74 read_decisions` -> :28 `_verified_rows` -> `graph/s2p_graph_reader.py:61 get_verified_decisions` -> GraphStore; also `store.get_all_decisions(domain="s2p")` -> :81 `merge_decisions` -> :53 `_decision_data`. Then preview :261 `score_pending` -> predicate :68 -> :223 `_invoice_id` / :227 `_decision_invoice` -> `invoice_enricher.py:27 enrich_from_fixture` -> :84 `_score_invoice` -> :77 `_score_read_only` -> SDK `score_read_only`; :333 `queue_payload`; :328 limit slice; :318 `_write_preview_observation_once` (:203) -> `_write_preview_observation` (:172) -> GraphStore observation write; response serialization. Cached path reuses computed invoice projection, but still executes observation-once handling.

**2b. No active double filter.** `_pending_decisions()` at :251 contains `if not _verified(row)`, but search finds only its definition. Active :326 passes `read_decisions(...)` directly to `score_pending`. Even two pure predicates on the same detached rows would not refresh the store or protect against a concurrent learn. Current read-only rescoring may show work that completed after the snapshot; submit-time validation must remain authoritative.

**2c. Empty is graceful.** :338 uses `... if invoices else 0.0`; :348 does likewise for approval rate. Empty scores return `([],0)`, then HTTP200 with status ok, graph source, empty exceptions/invoices, showing/scored_count/total zero. Exceptions instead become HTTP500 at :381-383. Frontend D02 hides that distinction.

**2d. Cache exists.** `clear_decision_cache()` is a no-op, but the separate app-owned ResponseMaterializer has a 30-second default TTL (:22). Score/learn invalidate it at router :2578/:2947. External/direct-store changes may leave a cached empty response until expiry/refresh; in-flight refresh serves an older snapshot without an age cap. Triage fetches only on mount, so it can stay empty even after the backend cache has recovered until navigation/reload. No permanent empty-cache bug is needed to explain saved 200-empty traces.

**2e. Dedup.** At :262 sort descending timestamp/decision_id; at :269 derive invoice_id; at :270 `if invoice_id in seen_invoice_ids: continue`; append up to n=50; confidence-sort those rows; total is len(seen_invoice_ids). Identity fallback includes decision_id, so distinct proper invoice IDs are not all collapsed. Colliding/wrong IDs can merge work, and latest-50 selection can postpone older work. More importantly, newer verified versions were already filtered out, leaving older pending versions (D05).

### Area 3: frontend errors, readiness, dependencies

**3a. Complete error-swallowing catalogue in current api.ts.** All numbers below are definition lines in `SDK/apps/s2p/frontend/src/api.ts`. Standard direct-null pattern is `.catch(() => null)`; it loses error/status information, although some consumers correctly display unavailable rather than empty. A nullable API is not inherently a bug if its caller explicitly handles unavailability.

- **Fabricated empty collections:** getPreviewQueue:134 (exceptions/total/rates zero); getPreviewSuppliers:177 (suppliers=[],total=0).
- **Direct null catches:** getPreviewConservation:151, fetchPromotionStatus:155, advanceS2PPromotion:159, fetchTwinStatus:169, fetchTwinDrift:173, getFingerprint:184, getTrajectory:190, scoreException:208, verifyDecision:212, scoreInvoice:216, getEvidenceTemplate:243, fetchS2PFingerprint:255, fetchS2PSimilar:261, fetchS2PCrossGraph:312, fetchS2PProcessSignals:316, fetchS2PProcessContext:323, getEarlyWarnings:336, getTrendSignals:340, fetchS2PAuditTrail:349, fetchS2PRules:359, fetchS2PEvolutionRules:363, fetchS2PEvolutionVariants:367, getS2PPromotionCheck:375, resetS2PEvolution:379, fetchS2PShadowResults:383, fetchS2PPromotedRules:387, fetchS2PCompliance:391, fetchS2PTrajectory:403, fetchS2PWhatIf:407, fetchS2PSummary:415, getNoveltyStatus:419, getNoveltyHistory:423, getNoveltyTriggeredDecisions:428, getAllCentroids:433, getCentroid:437, getCentroidExplanation:443, getCentroidDrift:447, getDKWeights:453, getFactorAnalysis:494, getFactorRecommendations:498, proposeFactorReplacement:502, getReceipts:509, getChainIntegrity:514, getAuditPack:518, getSimulationScenarios:522, getImpactSummary:526, getExtendedDiscoveries:530, getComplianceScreening:534, getRationalizationRecs:538, fetchAutoApproveStats:542, fetchExpansionProof:546, fetchSuppliers:605.
- **Shared silent-null helper:** apiGetNullable:553; callers getDiscoveryAlerts:395, getDisruptionRecovery:399, fetchIntents:557, classifyInvoice:561, fetchCTQueue:569, fetchVariants:574, fetchImpact:578, fetchFinancialImpact:583, fetchFinancialImpactTrend:587, fetchFinancialImpactCategory:591, fetchLeakage:597, fetchCycleTime:601, fetchSupplierProfile:617, fetchSupplierHeatmap:631, getPaymentStrategy:635, getPaymentBehavior:639, fetchSupplierClustering:644, getSupplierClusters:648, getSupplierSimilarity:652.
- **Aliases inheriting swallowing:** fetchPreviewQueue:147, getExceptionQueue:204, fetchConservation:200, getSupplierProfile:251, getSimilarInvoices:308, getAuditTrail:355, getS2PEvolutionVariants:371.
- **Special cases:** getConservationStatus:194 substitutes illustration then null; learnDecision:220 catches every failure to null including its 90-second abort; fetchSituation:266 returns null for non-OK/non-abort errors but emits an explicit timeout payload on its 10-second abort. fetchProcessFusion:329 catches to null **with console.debug**, so it is lossy but not entirely silent.
- **Strict exceptions to this pattern:** apiGet:71, apiPost:79, getCohortStatus:130, getPreviewQueueStrict:143, fetchSupplierProfiles:609, fetchDecliningSuppliers:613, fetchSupplierHistory:621. Generic apiGet/apiPost have no timeout. Supplier screen separately catches declining-profile errors to an unavailable empty collection (:83).

**3b.** Triage has separate loading, error, and empty JSX (:278-282), but the normal queue transport/500 error cannot reach its catch because api.ts has already returned a default. The user sees “No invoice exceptions” instead of an API error. Score/learn errors similarly become generic “Scoring failed”/“Learning failed”, losing the server's conservation/audit explanation.

**3c.** Triage :255 defines `const queueSettled = !loading && (queue !== null || queueError !== null)` and :258 sets `data-screen-ready={String(queueSettled)}`. It waits for the queue promise to settle, but not a healthy/nonempty queue or independently loaded child panels. Dashboard:48, Evidence:45, Insight:64, Suppliers:166 hardcode `data-screen-ready="true"` before their async data settles. `waitForAppShell()` checks only DOM/main content; `waitForScreenReady()` is separate and most failing helpers do not use it.

**3d.** Dashboard uses queue KPIs/recent/firstInvoice (:26,41-45). Evidence (:27) and Insight (:31) select the first queue invoice to drive per-invoice panels. An empty queue removes useful invoice context there even though aggregate panels can still mount. Suppliers loads profiles independently; its seasonality issue cannot be caused directly by the queue getter.

### Area 4: E2E infrastructure assessment

**4a–4c. Helpers and assumptions.** All six requested specs were read, as were shared `e2e/s2p/helpers.ts` and `e2e/helpers/ui.ts`.

| Spec | Setup/helper chain | Main readiness risk |
|---|---|---|
| active-age-smoke | ensureSelectedInvoice -> scoreFirstInvoice; clickTab/waitForAppShell; waitForTriageQueue; clickAndWaitForS2PScore | Empty passes queue helper, then selected-card assertion fails (20s); score wait 90s; test 180s |
| shadow-smoke | same selected/score pattern; health/graph probes | 5s availability probes can skip a slow healthy backend; queue90 + score120 can exceed test180 |
| new-surfaces | beforeEach health; openScoredTriage | queue90 + selected20 + score90 + panel waits exceed test150 at worst |
| rule-vs-reasoning | backendHealthy -> openScoredTriage | immediate selection count; fallback selector30s; score90; later optional blockquote is not optional in locator call |
| situation-analyzer | goToS2P -> goToTriage -> scoreSelected | queue45, invoice15, score90; shared serial beforeAll lacks its own matching long budget |
| sweep | openTab -> openScoredTriage | outer S2P project60s, queue helper90s; clicks score then sees already-mounted panel |

The selectors need **at least one selectable invoice**, not an exact pending count. Regular expressions expect `S2P-INV|STRESS-CONC-S2P|INV-|demo-s2p`; arbitrary valid IDs can fail fallback selection. Current `S2P-DEMO-*` is not necessarily matched by `demo-s2p` (word order differs). Prefer stable row selectors. The standalone `e2e/s2p/playwright.config.ts` has 30s and no global setup/retry config; umbrella `e2e/playwright.config.ts` has S2P60s, retries1, default workers1, globalSetup. Use the same invocation when comparing runs.

**4d. Latent failures after adding pending work.** Existing `rule-vs-reasoning-panel`, `rule-vs-reasoning-contrast`, and `situation-panel` test IDs are present in the components; no evidence they were removed. Known conditional defect: `rule-vs-reasoning.spec.ts:73` awaits `situation.locator("blockquote p").textContent()` even when the unavailable branch renders no blockquote. Timeout composition, slow supplier loading, real conservation blocking, and failed score/learn requests remain possible. A populated queue removes the observed first blocker; it does not prove downstream score/learn assertions will pass.

**4e. Supplier flake.** The saved report's suppliers:81 seasonality test passed, with no flaky outcome. Current source's equivalent is :81-plus-shift (the test following history), and the actual seasonality assertion is :97. `SupplierSeasonalChart` always mounts at screen :246 and always renders “Seasonality” at :429, even with supplier=null or no quarterly data. Thus removed data explains **insufficient chart data**, not disappearance of the panel. `selectFirstSupplierIfPresent()` uses an immediate count and shell-only navigation; that is a real readiness weakness, but cannot establish the cause of a missing-panel flake absent its failed trace. Request/preserve the original failed artifact if this recurs; do not claim the queue repair fixes it.

**4f. Exact explicit skips in available report (14).** All annotations say “No queued invoice available for scoring”: flows.spec.ts tests :148,161,188,206 (helper :75); phase1.spec.ts :79,93,142 (helper :57); triage.spec.ts :90,98,106,114,124,138,150 (helper :49). These are missing required acceptance data, not missing-feature skips. Other code has conditional skips: active-age non-AGE; counterfactual/day-zero/new-surfaces/rule tests unhealthy backend; shadow health timeout/unavailable AGE; factor-proposer unavailable analysis/recommendations; transfer-badge unavailable status. None of those explains these 14 in the saved report. Several situation tests also return early when backend/data is absent, recording a pass rather than an explicit skip; that is a coverage weakness.

**4g. Eight did-not-run tests.** `situation-analyzer.spec.ts:116` uses `test.describe.serial`; beforeAll (:120) creates one page, navigates and calls scoreSelected. Its setup fails on missing invoice. The report assigns workerIndex=-1/duration0 to remaining scored-state tests 3,4,5,6,7,8,11,12 (source :153,165,172,188,197,210,219,240). This is a serial dependency cascade, not a project dependency or missing browser config.

**Failure-by-file impact:** active-age 1, new-surfaces 2, rule-vs-reasoning 4, shadow 1, situation-analyzer 2, sweep 2 = **12 observed queue-blocked failures**. D01 should remove their current blocker and allow eight serial dependents plus 14 currently skipped tests to execute. Their eventual pass status remains unverified. D02/D04/D05–D10 and supplier fixture failures are not repaired by adding pending rows.

### Area 5: learn writes, gates and cascades

**5a. Exact write semantics.** The prompt's `backend/app/domains/s2p/scorer.py` does not exist in this checkout. `main.py:260` builds the SDK CompoundingScorer; its numerical ProfileScorer is the wrapped engine, not a separate S2P learn handler. Router :2798 -> :2860 `_learn_with_audit` -> :1995 `scorer.learn` -> SDK :1439 `_learn_impl` path. SDK computes `is_correct = actual_action == recommended_action` (:1450), builds `actual_index`, `verified_at`, `outcome`, and optional context (:1516-1522), then writes outcome+centroid atomically if supported (:1524-1537), otherwise write_outcome (:1539).

AGE directly updates Decision **status and correct** (:1475-1477). Optional Decision properties listed at :1479-1488 are outcome, verified_at_epoch, quality_signal, override_comment, verified_by, analyst_action, final_action, recommended_action, was_override; only non-None explicitly supplied arguments are written. The ordinary SDK call passes outcome in metadata, not all these optional direct arguments. Outcome stores decision_id/domain/actual_action/actual_index/is_correct/reward/verifier/override_reason/metadata/verified_at/created_at (:1511-1522). Read projection exposes actual_action/is_correct/actual_index/verified_at/outcome_metadata on the returned decision. Do not confuse joined fields with direct writes to the Decision node.

**5b. Cascades.** Numerical centroid update (:1502); durable outcome/centroid; SDK receipts/checkpoints/conservation/fingerprint and DK refresh where configured; invoice links; S2P audit sealing and extinction evidence (:2048-2084); proposal resolution only when applied (:2878); L5 centroid/conservation/DK persistence (:2897); outcome receipts (:2932); cache invalidation (:2947); supplier accumulation (:2949); evolver feedback (:2950); shadow write (:2957). These are not a guarantee of immediate rule promotion. A post-commit audit/extinction failure can return 503 after the outcome exists; the code intentionally leaves a pending audit marker for reconciliation. Timeout/retry clients must read back commit state rather than assuming all failures are pre-commit. Receipt duplication/idempotency deserves explicit tests; this sweep did not mutate evidence.

**5c. Gates.** Router :2813-2820 calls `_reject_red_write(governance)` then returns a held response for AMBER before write. SDK :1464-1467 captures conservation safety and returns a pause before numerical update/outcome write. Thus a previously pending row normally remains pending if these gates block. Once later operations have committed, failures do not automatically undo verification. PRESEED server configuration is not a general public-request bypass; SDK :1373 passes `bypass_conservation_gate=False` for public learn.

**5d. No ordinary return-to-pending API in this chain.** AGE outcome query :1507-1509 requires no prior Outcome and `d.status = 'pending'`; existing consistent outcomes have separate idempotency checks (:1597-1642), and inconsistent/resolved writes are rejected. Resetting status alone would leave correctness/actual_action branches verified and violate history semantics. Invoice re-score creates another Decision, explaining D05 rather than reversing the old Decision.

### Area 6: supplier fixtures and backend failures

The five recorded failures are the three fixture enrichment cases (required fields, quarterly trend presence, category-rate sum) plus declining-supplier expectations in the aggregate endpoint and aggregate helper tests. Current diff directly explains missing-key failures and zero computed declining suppliers. No suite was rerun in this diagnostic; session state provides the prior executed result.

E2E supplier tests check card OTIF/exception rates, declining indicators, seasonality/history/heatmap. Current tests allow “Insufficient seasonal data” and multiple empty/unavailable states, so a pass does not establish restored enrichment. SupplierSeasonalChart reads `lead_time_by_quarter` or `otif_by_quarter`; `supplier_profile_provider.py:224` maps the removed quarterly series. Fixture gaps and queue depletion are independent, with broader effect on suppliers rather than the 12 recorded triage failures.

### Area 7: cross-cutting inventory and lock assessment

- **7a. Comment search:** case-sensitive requested tokens across routers/services/domains yielded one match: `routers/s2p_clustering.py:17`, `# TODO: Move behavioral clustering helpers into app/services when the existing`. No FIXME/HACK/WORKAROUND matches in that search. This is a scoped search result, not proof of no technical debt.
- **7b. Runtime globals:** D08/D09/D12 enumerate the meaningful state. Also `routers/s2p_evolution.py:27` promotion ThreadPoolExecutor; `services/cross_copilot_signals.py:16` offline cache; `services/invoice_enricher.py:8` lazy fixture cache. Constant dicts such as factor templates, route mappings, disruption/intent maps are mutable Python objects but no runtime mutation defect was established. ResponseMaterializer is app-owned, unlike the financial globals.
- **7c. Locks:** `s2p.py:172` per-invoice Lock and :182 metadata Lock are used with holder/waiter accounting; acquire waits outside the metadata lock, release cleans up under it, scoring releases domain/invoice locks in finally (:2583). No self-reentrant Lock deadlock established. The domain acquire remains unbounded (D06). Advisory graph-link mutation is protected by RLock (:1960) and restored in finally (:2003); unrelated readers need not participate in that lock, so prefer avoiding temporary method replacement in future refactoring. L5 DK tracker uses RLock. ResponseMaterializer uses Condition(RLock), releases it during IO, publishes by generation, and notifies in finally; waits lack deadlines. PersistentBudgetPolicy :123 holds RLock over allocate/save and rolls back in-memory state on error; correct nested-lock choice but serializes slow storage. Proposal service uses per-instance RLocks around storage/mutation; no specific deadlock established. Cross-copilot offline cache uses RLock, with HTTP outside the lock and 0.2s default timeout. Evidence context cache uses RLock. No `_ContextualRewardFunction` remains in application code; `backend/tests/test_context_bridge_removed.py:44` explicitly asserts its absence. Its historical lock bug should not be reported as current. No concurrency stress was run.
- **7d. Disk versus live:** queue history is GraphStore-backed (`s2p_preview_data.py:78`), as are SDK conservation reads (`scorer.py:2898,3315`). There are deliberate static data readers: demo fixtures, invoice enrichment, supplier fallback, process/Celonis examples, isolated compounding simulation, initial centroids, frozen-twin snapshots, and cohort oracle validation artifacts. Their presence is not automatically a live-conservation violation. PVG/similar/context use static invoices and require the D11 contract cleanup. D04 is the demonstrated path where illustrative conservation reaches operational UI. Never fix the empty queue by making it read static JSON as if it were pending AGE work.

## Recommended Fix Prompt Sequence

Model labels below are suggested workload choices, not claims about which model was active in this investigation. Each prompt should preserve existing dirty changes and record actual test results.

| Prompt | Suggested model | Scope and acceptance | Dependency |
|---|---|---|---|
| S2P-F01 | GPT-6.1 Sol, high | Implement separate planted pending work cohort via score-only live API; explicit ownership/resume/replenishment rules; pending verification-field assertions; preserve extinction and S2P-09 measurement/order. Dry-run and mocked API tests, then authorized live preseed validation. | None |
| S2P-F02 | GPT-6.1 Sol, high | Strict queue/error contract and triage retry/loading states; remove operational fallback to illustrative conservation; test empty vs unavailable and real provenance on dashboard. | None; before acceptance rerun |
| S2P-F03 | GPT-6.1 Sol, high | Reconcile supplier fixture/enrichment schema with intended data policy; restore coherent sample fields or migrate consumers intentionally. Run five failed tests and supplier UI data tests. | None; owner intent if deletion was deliberate |
| S2P-F04 | GPT-6.1 Sol, high | E2E preflight and per-test work isolation, reliable queue/score/learn helpers, optional-blockquote handling, screen readiness, supplier deterministic tests. Stop skipping required acceptance work. Preserve exact report/config/artifacts. | F01–F03 for full live acceptance |
| S2P-F05 | GPT-6 Astra, high | Define invoice completion/supersession; authoritative verification projection, malformed-row handling, post-learn refresh; test old pending + newer verified + legitimate reopening. | F01 population contract; F02 UI states |
| S2P-F06 | GPT-6.1 Sol, high | Bounded queue reads/materialization, age/deadline policy, fail-visible slow-storage behavior, app-owned snapshot invalidation including financial state. Split backend performance and finance into separate reviews if needed. | F05 queue semantics |
| S2P-F07 | GPT-6 Astra, high | Durable receipt read model, append/idempotency/concurrency/chain-window semantics; restart and capacity tests; then app-owned auxiliary state and fixture/provenance cleanup. | F06 financial read contract; separate SDK changes only if proven necessary |

The first controlled PW rerun should follow F01–F04 and retain the original failure list as its acceptance checklist. Do not promise “all green” from a nonempty queue alone. F05–F07 address independently traced lifecycle and reliability defects; they should not be bundled into the initial seed repair.
