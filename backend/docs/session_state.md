# Session State

## S2P-GRATE (Sep 17, 2026)

Verified rather than duplicated: S2P mounts the shared SDK conservation router,
which already evaluates the three-layer `CompositeGate` (G-ABS + G-REL +
G-RATE) and returns `g_rate` in `/api/conservation/status`. Parameters are
the required defaults: `W_short=20`, `m_rate=0.85`. S2P's local
`framework/composite_gate.py` is an unrelated auto-approval discriminant and
was intentionally not modified. Validation: clean 20-outcome stream GREEN;
20-outcome sudden drop AMBER with active G-RATE; 15 existing conservation
tests passed. No SOC vocabulary in the S2P discriminant.

Repo: s2p-copilot/backend
Branch: not inspected (no git per task rule)
Baseline before this prompt: 1837 passed, 0 failed, 3676 warnings in 395.72s

---
## Prompt 1: DS-B1 Reset Contract + S2P-1 Proposal Lifecycle
Timestamp: 2026-09-07T10:47:16.2340145Z

### Changed files
- app/services/proposal_service.py
- app/routers/s2p.py
- tests/conftest.py
- tests/test_s2p_evolver.py
- tests/test_compounding_ledger.py
- ../../copilot-sdk/copilot_sdk/evolution/graph_store.py
- docs/session_state.md

### Reset contract
Option chosen: A — implement real logical reset.

Reason: reset is a product/API feature, not test-only behavior. The backend exposes POST /api/s2p/evolution/reset via app/routers/s2p_evolution.py, and app/services/s2p_evolver.py calls evolver.reset() then re-registers initial variants. The previous test fixture globally monkeypatched GraphVariantStore.reset() to call the in-memory test graph reset, hiding that the real append-only store raised RuntimeError.

New behavior: GraphVariantStore.reset() appends a variant_reset EvolutionEvent with a fresh epoch id. Active variant/outcome reads consider only events after the latest reset marker. Historical EvolutionEvents remain in the append-only graph; no production history is deleted.

### Proposal lifecycle
Before: POST /api/s2p/score created a proposal and returned proposal_id. POST /api/learn and POST /api/s2p/outcome performed learning and receipts, but did not resolve the proposal. tests/test_compounding_ledger.py manually called proposal_service.confirm(proposal_id), proving the missing service step existed outside the browser/API lifecycle.

After: POST /api/learn and POST /api/s2p/outcome resolve the proposal implicitly by decision_id after _learn_with_scorer(...) succeeds. confirm outcomes mark proposals confirmed; override outcomes mark proposals overridden with the analyst action. ProposalService.resolve_for_decision(...) is idempotent: missing proposals return None, and already-resolved proposals no-op.

### Current baselines
- Mypy changed files: pass
- Targeted changed tests:
  - tests/test_s2p_evolver.py: 18 passed
  - tests/test_s2p_evolution_router.py: 10 passed
  - tests/test_pydantic_responses.py: 5 passed
  - tests/test_compounding_ledger.py: 23 passed
- Sampling gate, unchanged files: 33 passed
- Full S2P backend suite: 1842 passed, 0 failed, 3686 warnings in 268.66s

### Blast-radius and scans
- Reset callers reviewed: router reset endpoint, s2p_evolver reset wrapper, and test fixtures.
- Proposal resolution callers reviewed: app/routers/s2p.py only; proposal lookup added to SQLite and graph stores.
- copilot-sdk import scan found no dependency from copilot-sdk into s2p-copilot app code.
- SOC frontend S2P preview scan found no affected proposal-resolution endpoint usage.
- S2P frontend directory was not present at ../frontend from backend.
- Banned pattern scan over backend/app returned no body_iterator or type-ignore matches.

### State for next prompt
DS-B1 is fixed by a real append-only epoch reset and removal of the global reset monkeypatch. S2P-1 is fixed by implicit, idempotent proposal resolution in learn/outcome after successful learning. Backend suite increased from 1837 to 1842 tests and remains fully green. 0 new regressions introduced.
---

---
## SLOT E ENTRY: S2P Contract Fixes — Frozen Twin, IKS, Queue Errors, Process Fusion Path
Timestamp: 2026-09-07T15:20:47.1829516Z

### Changed files
- app/routers/s2p_demo_beats.py
- app/routers/s2p.py
- app/routers/s2p_preview.py
- app/routers/s2p_control_tower.py
- app/routers/s2p_process_fusion.py
- tests/test_s2p_demo_beats.py
- tests/test_s2p_iks.py
- tests/test_s2p_preview.py
- tests/test_s2p_control_tower.py
- tests/test_process_fusion.py
- docs/session_state.md

### Findings and before/after contracts
- S2P-2 Frozen Twin field mismatch:
  - Before: /api/s2p/learning/frozen-twin returned S2P fields only: frozen_available, current_decisions, compared_decisions, frozen_decisions_would_miss, delta_accuracy, delta_coverage, coverage/accuracy fields, visual_diff, evidence fields.
  - After: same fields remain, plus SOC-compatible/current shell fields: current_vs_frozen, decisions_frozen_would_have_missed, replay_candidates, source. Computation is unchanged.
- S2P-3 IKS zero ambiguity:
  - Before: /api/s2p/iks returned iks/decisions/status but no reason, so a zero looked the same whether computed from no verified decisions or stale/uncomputed state.
  - After: response includes reason='insufficient_data' when decisions=0 and reason='computed' when verified decisions are present. Existing scorer.trajectory() computation remains the source of truth.
- S2P-4 swallowed queue errors:
  - Before: queue responses had no status discriminator, and preview/control queue failures could be indistinguishable from empty data to consumers.
  - After: successful preview/control queue responses include status='ok'. Unexpected queue failures raise HTTPException with detail status='error', message, and exceptions=[]. Existing list fields remain backward compatible.
- S2P-8 dormant 404 risk:
  - Before: process fusion was registered only at /api/s2p/enterprise/process-fusion.
  - After: /api/s2p/process-fusion also responds; legacy /api/s2p/enterprise/process-fusion remains available.

### Baseline before / after
- Before: 1842 passed, 0 failed, 3686 warnings in 268.37s
- After: 1846 passed, 0 failed, 3694 warnings in 204.53s

### Validation
- Changed-file mypy: pass
- Targeted tests:
  - tests/test_s2p_demo_beats.py: 18 passed
  - tests/test_s2p_iks.py: 9 passed
  - tests/test_s2p_preview.py: 39 passed
  - tests/test_s2p_control_tower.py: 17 passed
  - tests/test_process_fusion.py: 9 passed
- Sampling gate, unchanged files: 33 passed
- Full S2P backend suite: 1846 passed, 0 failed
- Banned pattern scan: no body_iterator or type-ignore matches under backend/app

### SOC preview tab cross-check
Still works: yes. TestClient checks returned 200 for /api/s2p/preview/queue, /api/s2p/preview/conservation, /api/s2p/preview/suppliers, and /api/s2p/insight/process-signals. SOC frontend scan confirms S2PPreviewTab calls /api/s2p/preview/queue and ProcessFusionPanel calls /api/s2p/insight/process-signals.

### State for next prompt
Slot B remains intact. Slot E fixes are additive/backward-compatible: Frozen Twin adds expected shell aliases, IKS reports computed vs insufficient_data, queue responses include status and surface failures as HTTP errors, and process fusion has both native and legacy paths. 0 new regressions introduced.
---

---
## SLOT J ENTRY: B5 Internal Conservation Coverage Alignment
Timestamp: 2026-09-07T12:07:40.9985867-07:00

### Changed files
- app/routers/s2p.py
- tests/test_s2p_conservation_coverage.py
- tests/test_s2p_preseed_integration.py
- tests/test_s2p_evolution_router.py
- docs/session_state.md

Pre-existing Slot B/E dirty files were present and left intact:
app/routers/s2p_control_tower.py, app/routers/s2p_demo_beats.py,
app/routers/s2p_preview.py, app/routers/s2p_process_fusion.py,
tests/test_process_fusion.py, tests/test_s2p_control_tower.py,
tests/test_s2p_demo_beats.py, tests/test_s2p_iks.py, tests/test_s2p_preview.py.

### Diagnosis
- _read_conservation_counts() already read verified_count, correct_count,
  total_decisions, penalty_ratio, categories_total, and categories_with_data.
- Public /api/conservation/status uses compute_conservation_status_payload(),
  which forwards category coverage to GAE conservation_status().
- Internal _current_conservation_status() bypassed that public payload builder,
  omitted categories_with_data/total_categories, and forced zero verified
  decisions to GREEN.
- _score_write_governance() also forced zero verified decisions to GREEN,
  creating another internal/public disagreement.
- GAE conservation_status() returns RED when verified_count or total_decisions
  is zero, and also returns RED when category coverage args are absent.

### Before / after
- Before: internal conservation could disagree with the public conservation
  endpoint because it dropped coverage args and rewrote zero evidence to GREEN.
- After: internal _current_conservation_status() uses the same
  compute_conservation_status_payload() path as the public endpoint, so coverage
  and zero-evidence handling stay aligned.
- After: _score_write_governance() no longer rewrites zero verified decisions
  to GREEN.
- COLD_START remains learning-allowed if supplied by the SDK because
  _is_learning_paused("COLD_START") returns False.
- Promotion-check test expectation was updated from the stale unavailable reason
  to the live conservation red gate now surfaced by the endpoint.

### Tests added / updated
- Added internal/public agreement coverage for zero decisions, bootstrap volume
  (1-9 decisions), and normal volume (50+ decisions).
- Added a coverage forwarding test proving categories_with_data and
  total_categories reach the conservation engine through the SDK payload path.
- Added a COLD_START learning-allowed guard.
- Added S2P-side regression coverage for SDK preseed_all_copilots.py S2P
  seeding, using fake health/trajectory/API calls against seed_s2p_domain().

### Validation
- Pre-check baseline: 1846 passed, 0 failed, 3694 warnings in 287.51s.
- Targeted conservation/preseed tests: 8 passed, 18 warnings.
- Targeted conservation/evolution/preseed rerun: 9 passed, 20 warnings.
- Affected score/auto-approve/learn target set: 122 passed, 246 warnings.
- Sampling gate random files:
  - tests/test_scaffold.py
  - tests/test_cross_copilot_signals.py
  - tests/test_provenance_label.py
  - Result: 23 passed, 48 warnings.
- Mypy changed Slot J Python files: pass.
- Banned pattern scan under backend/app: no body_iterator or type-ignore matches.
- Full S2P backend suite after fix: 1852 passed, 0 failed, 3706 warnings in
  303.50s.

### State for next prompt
Internal and public S2P conservation status now share the same SDK payload
calculation and category coverage inputs. Backend suite increased from 1846 to
1852 tests and remains fully green. 0 new regressions introduced.
---

---
## SLOT N ENTRY: Proposal Resolution Guard + Cold-Start/Bootstrap Verification
Timestamp: 2026-09-07T20:38:19.1992742-07:00

### Changed files
- app/routers/s2p.py
- tests/test_compounding_ledger.py
- tests/test_s2p_conservation_coverage.py
- docs/session_state.md

### Baseline before
- S2P backend pre-check: 1852 passed, 0 failed, 3706 warnings in 287.12s.

### B finding: proposal resolution after blocked learn
Before:
- /api/learn called _learn_with_scorer(), then always called _resolve_decision_proposal().
- /api/s2p/outcome called _learn_with_scorer(), then always called _resolve_decision_proposal().
- A paused or blocked SDK learn result could leave learning unapplied while marking the proposal resolved.

After:
- _learn_result_applied() centralizes learn-result interpretation.
- /api/learn resolves proposals only when learning_applied is true and the payload is not paused, blocked, held, gate=BLOCKED, conservation RED/AMBER/UNKNOWN, or a conservation failure reason.
- /api/s2p/outcome uses the same guard before proposal resolution.
- Blocked/paused learn responses return learning_applied=false and gate=BLOCKED; the proposal remains proposed/pending and can be retried after conservation clears.

### J finding: COLD_START/BOOTSTRAP integration
Before:
- COLD_START was already learning-allowed in _is_learning_paused().
- BOOTSTRAP was treated as paused/blocked, which conflicted with the Slot N requirement that BOOTSTRAP is learning-allowed.

After:
- _is_learning_paused() treats BOOTSTRAP like COLD_START: learning allowed.
- RED, AMBER, PAUSED, and UNKNOWN remain learning-blocking.
- Tests verify both string and dict BOOTSTRAP payloads are not classified as paused.

### Conservation chain verification
- POST /api/learn -> SDK learn() -> paused/conservation_red -> proposal NOT resolved: verified by test_learn_paused_keeps_proposal_pending_until_retry.
- POST /api/learn -> SDK learn() -> learned/learning_applied=true -> proposal resolved: verified by retry portion of test_learn_paused_keeps_proposal_pending_until_retry and existing proposal lifecycle tests.
- POST /api/s2p/outcome -> SDK learn() -> blocked/conservation_unavailable -> proposal NOT resolved: verified by test_outcome_blocked_keeps_proposal_pending.
- COLD_START/BOOTSTRAP are learning-allowed at S2P pause-classifier boundary: verified by tests/test_s2p_conservation_coverage.py.

### Blast radius
- resolve_for_decision is implemented in app/services/proposal_service.py and called through app/routers/s2p.py only.
- _is_learning_paused callers remain S2P-side governance/evolver/outcome paths; no external app imports were changed.

### Validation
- Targeted changed test files: 25 passed + 8 passed, 0 failed.
- Mypy changed files: pass.
- Sampling gate random files: tests/test_s2p_simulation_router.py, tests/test_s2p_active_age_parallel.py, tests/test_framework_discipline.py: 12 passed, 0 failed.
- Full S2P backend suite after fix: 1855 passed, 0 failed, 3712 warnings in 230.58s.
- Banned pattern scan under backend/app for body_iterator and type-ignore: no matches.

### State for next prompt
Proposal resolution is now conditional on successful learning. Blocked or paused SDK learn results preserve pending proposals and expose gate=BLOCKED. COLD_START and BOOTSTRAP are both learning-allowed; RED/AMBER/UNKNOWN remain blocking. 0 new regressions introduced.
---

---
## SLOT: S2P Stage 1 Multihop Evaluation
Timestamp: 2026-09-09

### Changed files
- scripts/evaluate_multihop_stage1.py
- tests/test_multihop_evaluation.py
- data/s2p_multihop_stage1_results.json
- data/s2p_multihop_stage1_report.md

### Inputs read
- data/s2p_multihop_stage1.json
- data/s2p_multihop_schema_extensions.json
- app/domains/s2p/config.py

### Implementation
- Added ScenarioGraphStore for planted S2P Stage 1 scenarios.
- Added four comparator arms: single_pass, breadth, content_rule, and VLD.
- Initialized S2P centroids from Stage 1 surface_factors with 5 categories, 5 actions, and 7 factors.
- Generated all 200 result rows for 50 scenarios x 4 arms.
- Generated markdown report with acceptance test, per-kind results, controls, per-rho table, and cross-copilot comparison.

### Results
- Acceptance test: FAIL.
- Headline score_keyed rho >= 0.70: S2P VLD=0.562, SP=0.625, delta=-0.062.
- Controls:
  - Flat VLD <= SP: PASS (SP=0.600, VLD=0.600).
  - rho=0.50 VLD near chance 0.20 +/- 0.15: PASS (VLD=0.200).
- Per-kind accuracy:
  - content_keyed: SP=0.600, breadth=0.600, content_rule=0.600, VLD=0.600, N=15.
  - prerequisite: SP=0.700, breadth=0.600, content_rule=0.900, VLD=0.900, N=10.
  - score_keyed: SP=0.680, breadth=0.480, content_rule=0.600, VLD=0.520, N=25.

### Validation
- Pre-check baseline: 1855 passed, 0 failed, 3712 warnings.
- scripts/validate_stage1.py: ALL 10 QUALITY CHECKS + SPEC CONSTRAINTS PASSED.
- Result row gate: 200 rows.
- backend/app blast-radius gate: empty diff.
- Targeted multihop tests: 10 passed, 22 warnings.
- Mypy on changed script/test: pass.
- Full S2P backend suite after changes: 1865 passed, 0 failed, 3732 warnings in 246.89s.

### State for next prompt
S2P Stage 1 planted positive-control evaluation is implemented and reproducible from scripts only. Controls pass, but the main score_keyed acceptance trend fails because VLD does not outperform SP at rho >= 0.70. 0 new regressions introduced.
---

## SH-05a — S2P Tensor Reference Hygiene — 2026-09-15

- Completed the mechanical stale-reference cleanup. The live S2P tensor remains 5x5x8: `N_FACTORS = 8`, with profile centroid shape `(5, 5, 8)`.
- Category A documentation: 2 descriptive references fixed; `5x5x7` references changed to `5x5x8`. No `175` tensor-cell references required replacement.
- Category B test assertions: 0 stale references found or changed.
- Category C backend source constants: 0 stale computational/descriptive references found or changed.
- Category D frontend TypeScript: 0 stale references found or changed.
- Category E configuration/JSON: 0 stale references found or changed.
- Verification scan: 0 stale `5x5x7`/175-cell references remain in the excluded-build-output-free source/document scan; 2 live 5x5x8 confirmations remain in `backend/app/domains/s2p/config.py`.
- Tests: prior recorded baseline 1865 passed / 0 failed; after cleanup 1851 passed / 0 failed / 3705 warnings in 175.98s. Documentation-only changes introduced no test failures.

## S2P-PLANT (Sep 17, 2026)

3 demo fixture files created in `data/demo_fixtures/`.
`preseed_demo_scenarios.py` loads and validates the fixtures, tags all records
with `planted: true`, performs repeat-safe registry loading, and verifies the
compliance and frozen-twin endpoints when available. Tests: 1,851 passed / 0
failed. No source-of-truth backend changes.

## BUILD-B3 Dollar Thresholds (Sep 17, 2026)

Added `/api/s2p/confidence/thresholds` (GET) and
`/api/s2p/confidence/route` (POST), with four dollar bands and explicit
`geometry_derived` calibration metadata. K14 noise rate is reported as 0.92;
surface-label confidence is not used as the calibration source. Added six
targeted tests: all passed. S2P BE total: 1,857 passed. The already-running
port-8002 process predates the mount and returned 404 for the new live route;
restart is required for live endpoint verification.

## S2P-SCORE-CONTRACT (Sep 18, 2026)

Documented the `POST /api/s2p/score` request and response contract. Created
three stable Playwright/demo payloads in
`data/demo_fixtures/pw_score_payloads.json`.

Valid categories: `price_variance`, `quantity_mismatch`, `duplicate_risk`,
`contract_gap`, `format_compliance`.
Valid invoice IDs used: `S2P-INV-0001`, `S2P-INV-0002`, and `S2P-INV-0009`.
Response shape: action, confidence, factor_vector, factor_names,
decision_id, plus probabilities and optional governance/enrichment fields.
Score contract: `docs/s2p_score_contract.md`.
All three payloads returned HTTP 200 from the live S2P backend. S2P BE:
1,857 passed, 0 failed.

## RL-CTRL-PROD (Sep 18, 2026)

Mounted `AdaptiveBudgetPolicy(safety_lambda=0.0)` from the SDK through the
existing S2P investigation classifier contract. Mounted
`GET /api/self/investigation-budget`; live response verified as
`{"controller":"adaptive","decisions":0,"safety_lambda":0.0}`.
S2P backend: 1,857 passed, 0 failed. No frozen SDK files were modified.

## S2P-CONTRACT-FIX (Sep 18, 2026)

F1 (P1): Twin serialization reads `action_name`, not `action`.
F2/F5: Budget callback uses `get_verified_count()`.
F3: Budget allocation uses action confidence, not classifier confidence.
F4: Twin GET suppresses governance event recording.
F7: Fallback sets `frozen_available=false`.
F8: Twin drift responses include the panel accuracy/coverage fields.
F11: Confidence routing rejects non-finite amounts.
Conservation provider now supplies ordered verified outcomes to the shared gate.
Tests: +14. S2P BE: 1,871 passed, 0 failed.

## S2P-AUDIT-FIX (Sep 19, 2026)

S8-01 (P1): unconditional graph-store audit verification RESOLVED.
One configured-store verification path now recomputes decision/outcome hashes,
checks sequence and previous-hash links, validates outcome-to-decision links,
and checks a persisted chain head (including tail deletion). Audit writes seal
entries in GraphStore ledger storage; incomplete writes leave a fail-closed marker.
Removed pytest detection and the separate _LEDGER implementation. main.py injects
the configured store; exports and POST /api/s2p/audit/verify verify the request's
store rather than falling back to another application's/global store.
Unreadable/malformed storage and unsealed active or archived history return
verified=false, reason=verification_unavailable. Genuine empty stores verify.

Validation: baseline 1,871 passed; final S2P BE 1,923 passed, 0 failed (+52 cases).
Replaced 10 legacy audit tests with 62 cases using real InMemoryGraphStore and
SQLiteGraphStore adapters, explicitly injected without mocks. Includes tamper,
missing entries/head, outcome links, read failure, interrupted writes, archival,
concurrent writes, HTTP verification, and SQLite close/reopen persistence.
Focused audit/export tests: 71 passed. Mypy run after every Python edit;
initial type errors corrected, final check clean for all four changed files.
Frozen SDK hashes unchanged: investigation.py 3441dcbd,
investigation_router.py 08f4df7a, scorer.py 24ac9e49.
Standing rule #81 enforced for this audit verifier; no retroactive certification.

Deployment caveats: the current main scoring path does not invoke this audit
writer, so its unsealed decisions now correctly make verification unavailable.
Complete scoring-to-audit wiring needs a separate change beyond the allowed
files. No legacy history was re-sealed or migrated. Live AGE write/tamper tests
were not run; AGE's ledger/governance read/write implementation was inspected,
and storage behavior was exercised through the same verifier with real memory
and SQLite stores. The stored head is not an external trust anchor; GraphStore
also provides no atomic cross-process append, so conflicting appends fail closed
rather than providing a multi-worker availability guarantee.

## S2P-BLOCK2 (Sep 19, 2026)

S8-01b implementation: POST /api/s2p/score seals the SDK-created decision
through app.state.audit_writer, using the configured scorer store. Both
/api/s2p/outcome and /api/learn seal applied outcomes without writing the SDK
decision/outcome twice. Actual ground-truth actions are preserved and hashed.
The adapter reuses audit.py's canonical entry models and locked append
primitive; audit.py was not modified. Failed sealing returns 503; pending
outcomes and unsealed legacy decisions remain unverifiable, not rubber-stamped.

S8-03 (S2P): removed ambient pytest detection from main.py. S2P_PROFILE
explicitly selects production (default), test, or development; invalid values
fail configuration. Tests set S2P_PROFILE=test before app creation. No
sys.modules/pytest or PYTEST_CURRENT_TEST detection remains in backend/app.

Validation: baseline 1,923 passed. Added 29 passing cases in
backend/tests/test_s2p_audit_wiring.py, using real scorers and explicitly
configured InMemoryGraphStore/SQLiteGraphStore through HTTP routes. Covers
single-write persistence, hashes/links, both outcome routes, overridden
ground-truth actions, tamper detection, failed sealing, and explicit profiles.
Focused existing audit/export/outcome regression run: 83 passed.
Mypy clean for all four changed Python files; checked after every Python edit.

Full suite: 1,933 passed, 19 failed (1,952 total). Acceptance remains blocked:
8 failures in test_l5_dk_s2p_hook.py use a FakeGraphStore without audit
persistence; 8 in test_s2p_active_age_phase_b.py use a FakeAGEStore without
ledger/governance methods; 3 in test_s2p_score_endpoint.py use SlowScoreScorer
with an empty SimpleNamespace store. These now fail closed with 503 rather
than silently bypassing sealing. The real AGE adapter exposes ledger and
governance methods, delegated by S2PActiveAGEGraphStore. Those three existing
test files are outside the allowed edits; permission is required to update
their fixtures. No production bypass was added to accommodate these fakes.

Live server on port 8002 still returns 404 for the audit verification route;
it was not restarted or mutated for validation. Live AGE sealing remains
unverified. No legacy history was migrated or retroactively sealed.
Frozen SDK hashes unchanged: 3441dcbd, 08f4df7a, 24ac9e49.
audit.py SHA256 unchanged:
1d70a39545952c5bc0dc13ddfbff8b497daff7e5edcfebed9a7cecc814af6752.

### S2P-BLOCK2 follow-up — approved test-store fixes (Sep 19, 2026)

Updated the three legacy test files with user approval. FakeGraphStore in
test_l5_dk_s2p_hook.py and FakeAGEStore in test_s2p_active_age_phase_b.py now
delegate ledger/governance persistence to real InMemoryGraphStore instances.
SlowScoreScorer in test_s2p_score_endpoint.py now uses an s2p-domain memory
store and persists its decision before the production route seals it.
The L5 outcome fixture starts from a decision sealed by the real writer.
Added assertions verifying complete audit chains after score/outcome and
concurrent score calls. No audit call was suppressed or replaced with a no-op.

The required store contract is save/get/list/delete ledger and governance,
not async_record_decision/async_record_outcome (which are writer functions).
Production files, audit.py, and frozen SDK files were unchanged in this
follow-up. Mypy passed after every Python edit and in the combined check.
Targeted regression: 112 passed.
Full backend suite: 1,952 passed, 0 failed in 221.59 seconds (3,906 existing
deprecation warnings). The previous 19-failure acceptance blocker is cleared.
Live-server restart/AGE verification caveats above remain unchanged.

## S2P-BLOCK3 (Sep 19, 2026)
S7-F8: RESOLVED. Twin endpoint returns frozen/live accuracy and gap fields,
including the UI contract aliases `frozen_accuracy`, `live_accuracy`, and
`accuracy_gap`.
S8-R1: RESOLVED. Test factory injection and app/evolver state are scoped to
fixtures and restored after each test; no import-time factory overrides remain.
S8-R3: RESOLVED. Added an unpatched full-flow test using the real scorer,
graph store, evidence receipts, outcome receipts, conservation snapshot, and
the both-persistence-paths-failed 503 guard.
Tests: +5. S2P BE: 1,957 passed, 0 failed.

## S2P-PRESEED (Sep 19, 2026)
Created `scripts/preseed_s2p_demo.py` and
`data/demo_fixtures/s2p_demo_decisions.json`.
The script plans Copper/container decisions, 30 warm-up decisions, 20
post-freeze decisions, and investigation warm-up calls. It uses HTTP-only
seeding, records failures, and skips decision IDs already listed by the API.
S2P-02 cold-start: NOT AVAILABLE in the current score contract; no
`prior_verified_count` or isolated scorer response exists.
S2P-01/03/05/07/09: seedable portions are represented, but the current live
API lacks respectively the required `accept`/clause-7.3 reasoning, context
reasoning fields, extinction response fields, budget `allocations` telemetry,
and a responsive freeze/comparison path. The live batch run was stopped after
the running backend became nonresponsive; no complete live-preseed claim is
made. Dry-run and mypy passed.

## S2P-ENDPOINT-ENRICH (Sep 20, 2026)
S2P-01: Score response includes `action_name`, human-readable `reasoning`,
and `rule_override`.
S2P-02: Score response includes `prior_verified_count` and `cold_start`.
S2P-03: Score response includes invoice/domain `context` and domain-aware
reasoning terms.
S2P-05: Compliance response includes `extinction_classes` and
`class_timeline`, with compatibility aliases for existing consumers.
S2P-07: Investigation-budget response includes `warm`, `allocations`, and
`recent_allocations` telemetry.
S2P-09: Frozen-twin POST freeze returns 200 idempotently, and GET includes
`comparison_points`, numeric `gap`, and `frozen_at` fields.
Tests: +15 endpoint-enrichment tests. S2P BE: 1,972 passed, 0 failed.
Focused mypy checks passed for all changed Python files. Frozen SDK hashes
remain investigation.py 3441dcbd, investigation_router.py 08f4df7a, and
scorer.py 24ac9e49.

## S2P causal extinction and allocation contracts (Sep 20, 2026)
S2P-05: Added persisted class-extinction evidence to the common audited
outcome/learn path. Under the existing domain mutation lock, a category's
last pending decision must become verified and leave no pending decisions
before its decision_id is stored as earning_decision_id. Wrong-category,
unverified, empty-before, and bulk-drain attribution do not earn an event.
Governance records are idempotent and survive SQLite reopen. They describe
historical queue depletion, not permanent elimination of the category.
Compliance returns these records through extinction_classes/extinct_classes/
queue_extinctions. Fixture bucket declines remain separate invoice trends;
no fixture ID is substituted for a stored decision. Planted evidence is labeled.
Compliance cache now receives its request/store and invalidates on learning,
so a previously cached empty response does not hide a new extinction event.

S2P-07: Allocation rows include profile, reads, confidence, category, and
decision_id. Confidence bands use the actual policy thresholds: >=0.85 easy,
>=0.65 medium, otherwise hard. Request-local attribution and a history lock
prevent concurrent investigations from exchanging decision IDs/categories.
The full bounded history is exposed; recent_allocations remains the last 10.
Cold-start and empty-history paths do not fabricate allocation samples.
Direct legacy allocations without request attribution keep decision_id=null.

Verification: 16 new real-store/policy regressions pass, including SQLite
persistence, both outcome routes, warm-cache invalidation, and concurrent
allocation attribution. Two existing test stores now implement get_decisions.
New tests restore supplier accumulator telemetry after exercising real outcomes.
Mypy passes for all 9 changed Python files. Frozen SDK hashes remain unchanged.
Full suite before the final cache regression addition: 1,986 passed.
Final full suite: 1,987 passed, 1 failed (existing random auto-approval test
test_audit_event_status_shadow_only_not_verified expected shadow_only but
sampled spot_check_required; default rate 2%, unseeded RNG). That test passed
unchanged on isolated retry; no clean final full-suite claim is made.
No E2E specs changed. Restart S2P to load these contracts. Live demo closure
still requires an actual last-active-decision outcome and real easy/hard
investigations; historical extinction IDs and allocation samples were not invented.

## S2P-FINAL (Sep 20, 2026)

Plan written before implementation: docs/s2p_final_plan.md.
Demo operations require S2P_DEMO_MODE=true; no pytest detection was added.

- S2P-01/03: Explicitly select the existing copper/container synthetic fixtures
  by matching event/category/supplier in demo mode, without mixing another
  legacy invoice's graph context. Add provenance and domain reasoning.
  Copper exposes accept only when the real scorer chooses auto_approve;
  canonical_action, stored decisions, audit entries and learning retain the
  canonical action. Unrelated contracts no longer claim clause 7.3.
- S2P-02: Reserved PW-DAY-ZERO-001 request uses a disposable canonical
  ProfileScorer, with zero prior history and no live graph/outbox/audit writes.
  Response explicitly says persistent=false and has no persisted decision ID.
- S2P-05: Preserve and test the live-store causal extinction pipeline: only
  the outcome resolving the final pending decision earns the extinction ID.
  Versioned V3 warmup rows create new provenance-labelled outcomes instead of
  reusing old rows that predate the evidence writer. First ten warmup decisions
  cover all five categories; the next forty exclude format_compliance.
  Unrelated pending history is not auto-resolved or deleted to force extinction.
- S2P-07: Guarded GET /api/demo/s2p/budget-candidates searches actual live
  centroid boundaries. Preseed scores the resulting ambiguous/easy invoices
  and investigates with their returned decision_id/category/factor_vector.
  Real integration tests verify allocations of 1 read vs 4 reads.
- S2P-09: POST /api/demo/s2p/learning/re-freeze requires explicit confirmation,
  archives the prior snapshot, saves a new immutable snapshot and atomically
  selects it with origin=demo_reseed, archived_previous=true provenance.
  Original production snapshot/freeze semantics remain unchanged. Demo-mode
  comparison reads the selected snapshot, including after reopening it.
  Fixed the comparison's raw-Decision filter: it now uses the canonical
  verified Decision/Outcome join, which supplies actual_action/is_correct.
  A real reviewer correction produces a positive live/frozen accuracy gap in
  regression tests; the seeder never invents or clamps a positive gap.

Preseed now checks demo-control availability before writes, performs bounded
synthetic reviewer learning after re-freeze, and returns nonzero on errors or
unmet demo requirements. Dry-run and the 10+40 category schedule check pass.

Validation: baseline 1,988 passed. Added 15 regression functions exercised
against both real memory and SQLite stores (30 cases). Final full backend suite:
2,018 passed, 0 failed. Focused contracts plus architectural scanner: 38 passed.
Mypy passes for all six changed Python files. Production-pattern scan: clean.
SDK frozen hashes remain 3441dcbd / 08f4df7a / 24ac9e49.
No specs, audit.py, test conftests, or sibling-repo sources were changed.

Live closure is NOT yet claimed: the running S2P process returns 404 for the
new demo-control route. Its old compliance response has zero extinction events,
its twin compares zero decisions, and its budget history contains only easy
allocations. Restart S2P with S2P_DEMO_MODE=true in the launcher shell, then run
scripts/preseed_s2p_demo.py and the unchanged six demo specs. Existing unrelated
pending decisions may still prevent genuine class extinction; any such unmet
condition is reported instead of fabricated.

## S2P-FINAL live reseed verification (Sep 20, 2026)

Confirmed the restarted process inherited S2P_DEMO_MODE=true: guarded
GET /api/demo/s2p/budget-candidates returned 200 with real easy/hard candidates.
Two initial seed attempts stopped before writes while urllib read the large
GET /api/self/decisions?limit=500 response (WinError 10054). The same response
was readable through PowerShell and http.client. Updated only the preseed HTTP
transport to consume responses before closing, preserving timeout/error handling.
Mypy passed. The subsequent live seed reached its final checks.

Observed live results:
- Archived demo re-freeze: 200, origin=demo_reseed, archived_previous=true.
- Adaptive budget: two easy allocations at 1 read, two hard at 4 reads,
  all linked to real scored decision IDs.
- Twin: 410 compared decisions, delta_accuracy=0.007317073170731714
  (approximately +0.73 percentage points).
- Compliance: zero persisted extinction events; reseed exited 1 rather than
  claiming all demo requirements were met. Cause still requires investigation;
  do not assume it is older pending history without checking the complete store.

Unchanged six-spec Playwright run: 4 passed, 2 skipped, 0 failed.
Passed S2P-01/02/07/09. S2P-03 still skips: live container decisions recommend
refer_to_specialist rather than hold_for_review (observed confidence ~0.5359).
S2P-05 still skips because the causal extinction timeline is empty.
Full backend regression suite after transport change: 2,018 passed, 0 failed.
Frozen SDK hashes remain 3441dcbd / 08f4df7a / 24ac9e49.
No specs or backend source files changed during this verification run.

## S2P-DEMO-FINAL (Sep 20, 2026)
S2P-03: Added `action_category` to the score response. `refer_to_specialist` maps to `HOLD`; auto approval maps to `APPROVE`; compliance/leakage escalation maps to `ESCALATE`; unknown actions map to `REVIEW`. The SDK spec now asserts `action_category == HOLD` for the container story.
S2P-05: Verified warmup scheduling uses the real `/api/learn` outcome commit for the first ten decisions, including `format_compliance`, then excludes `format_compliance` from subsequent decisions so the class can produce a persisted verified extinction transition.
Tests: +6 action-category tests. S2P BE: 2,024 passed, 0 failed. Mypy clean. Live preseed/spec verification requires a restart to load the new router; the currently running process returned no `action_category`, and the bounded preseed was stopped after the local API became nonresponsive.
