
## AGE-JM-PHASE-05B (Sep 22, 2026)
S2P production profile guards: `_VLDKDecisionConnection` rejects SQLite K-utility persistence in explicit production profile; production investigation startup omits the legacy SQLite K store. `build_s2p_scorer` ignores the legacy `db_path` when an injected graph store is present in production. Test/offline K-utility construction remains supported.
S2P tests: 936 passed before one pre-existing active-AGE factory assertion failure (expected kwargs omitted `profile="test"`; failure is outside `backend/app/main.py`). Requested `tests/` path does not exist; `backend/tests/` was executed. Mypy: clean.
Phase 5B status: guards complete; backend suite requires follow-up on the unrelated active-AGE factory expectation.

## AGE-JM-PHASE-06B (Sep 22, 2026)
G036: S2P PVG/preview/factor fixture paths demo-gated.
Production rejects fixture JSON and Celonis cache fallback; missing factors cause abstention. Preview simulation uses an isolated scorer and cannot mutate the canonical S2P store/scorer. Demo/test profiles preserve fixture paths.
S2P tests: 989 passed before the existing unrelated test_s2p_audit_wiring.py::test_profile_from_env_not_pytest failure (development alias currently resolves to offline); targeted G036 tests: 6 passed. mypy: clean for all three target modules and the new test module.

## STEP-A2 (Sep 22, 2026)

- Fixed test_profile_from_env_not_pytest: explicit offline profile and development-to-offline alias; generic profile variables cleared for deterministic default coverage. Invalid-profile test follows the shared resolver error contract.
- Health: imports shared copilot_sdk.backend.health_builder; supplies typed startup GraphConfig so the legacy requested_backend field cannot misreport offline storage as AGE-ready. Preserves service=s2p-copilot and version while keeping canonical status/readiness and HTTP 200/503 behavior. Both /health and /api/health covered.
- Focused regression: 1 passed, 2080 deselected. Focused profile/health/startup/route tests: 43 passed, including four connected/disconnected alias cases. mypy backend/app/main.py --config-file pyproject.toml: clean.
- Full suite with --timeout=120 -x: 364 passed, 1 failed at backend/tests/test_evidence_receipt_wiring.py::test_learn_appends_evidence_receipt_before_outcome_write (expected 2 receipts, received 1). Same failure after approved outbox access rerun. Baseline check loading original app.main health code in memory reproduces the failure; no on-disk rollback performed. This unrelated regression remains outside STEP-A2; full-suite gate is not green.
- Results: ../.codex_tmp/step_a2_s2p_suite.xml and ../.codex_tmp/step_a2_s2p_baseline.xml.
- No git used; compliance report and execution plan not read. Live /health verification remains for user after restart.

## S2P-PREVIEW-FIX (Sep 22, 2026)

Implemented and verified Sep 23, 2026.

- Removed _load_fixture_json and its production guard. Queue and supplier endpoints read domain-scoped graph decisions; no preview invoice/supplier fixture reads or profile-based response branches.
- Centroids come from the live scorer's public get_centroid interface; no global fixture centroid cache.
- Queue: pending/unverified decisions, newest 50 candidates, read-only live scoring, confidence ranking, existing frontend keys and observation writes preserved. Actual decision IDs included; pending ground truth remains null. Stored factor names/vectors and graph metadata are normalized. Static queue cache removed; tab-state registration invalidates on score/learn/reset.
- Suppliers: grouped by supplier identity; deduplicated invoice counts and latest verified outcomes. Exception rates/trends use actual actions, OTIF uses recorded delivery evidence, amounts/names/PO details come from graph metadata. Pending re-scores preserve verified history. Missing OTIF/lead-time/financial-health evidence remains null; no fixture substitutions.
- Read-only AGE transformation check: 801 decisions, 313 pending, 488 verified; 50 recent invoice rows normalized; 79 supplier profiles; 227 distinct-invoice outcomes; 0 OTIF observations. No graph writes or server restart during this probe.
- mypy backend/app/routers/s2p_preview.py --config-file pyproject.toml: clean after each change.
- Focused preview/observation/production-mode/AGE compatibility tests: 85 passed. Tests seed isolated graph stores; fixture JSON is used only to prepare test data.
- Requested full suite (python -m pytest backend/tests/ -q --timeout=120 -x): 364 passed, 1 failed at test_evidence_receipt_wiring.py::test_learn_appends_evidence_receipt_before_outcome_write (expected 2 receipts, got 1). Same baseline-confirmed failure recorded in STEP-A2. No focused failures; full-suite zero-new-failures gate cannot be fully established because -x stops here.
- Evidence: ../.codex_tmp/s2p_preview_fix_focused.xml, ../.codex_tmp/s2p_preview_fix_suite.xml, ../.codex_tmp/s2p_preview_graph_probe.json.
- No git used. LIVE VERIFY REQUIRED after backend restart: /api/s2p/preview/queue and /api/s2p/preview/suppliers.

## FINAL-MOCKUPS-S2P (Sep 23, 2026)

- Module data shadows: absent; deleted unused centroid, fixture-path, Celonis enrichment, softmax, and queue-selection helpers. Queue and suppliers read request app graph/scorer on every call.
- Removed _load_celonis_cache and the score endpoint's dependent process-context cache. Score process enrichment now queries S2P decision history for the requested invoice, selects the latest recorded process evidence, and merges cross-copilot signals. No profile checks or fixture substitutions added.
- Extracted graph record/profile transformations into backend/app/services/s2p_preview_data.py and compounding calculations into backend/app/services/s2p_preview_simulation.py; frontend response fields preserved. The isolated simulation store factory stays in the preview router as required by the existing static check.
- Exact requested s2p_preview.py re-scan: 0 matches. No Celonis loader/cache references remain in application or test Python files.
- Mypy: clean for both changed routers, both new services, and all three changed test files.
- Focused preview, observation, score, production-mode, and static-validation tests: 136 passed. Includes current app scorer replacement, graph process-context refresh, invoice isolation, and signal merging in production.
- Requested full suite with --timeout=120 -x: 364 passed, 1 failed at test_evidence_receipt_wiring.py::test_learn_appends_evidence_receipt_before_outcome_write (expected 2 receipts, got 1). Same baseline-confirmed failure documented in STEP-A2 and S2P-PREVIEW-FIX. Full-suite gate remains blocked by that failure; later tests were not reached.
- Evidence: ../.codex_tmp/final_mockups_s2p_focused.xml and ../.codex_tmp/final_mockups_s2p_suite.xml.
- No git used. Live endpoint verification after backend restart remains pending.

## MOCK-FIX-S2P (Sep 23, 2026)
Files modified: backend/tests/conftest.py, backend/tests/test_financial_router.py, backend/tests/test_s2p_performance.py.
FakeGraphStore: replaced in 2 REPLACE files (financial_router, s2p_performance) with InMemoryGraphStore-backed protocol stores seeded via write_decision/write_outcome/save_centroids. 17 REPLACE-classified files remain for follow-up.
FakeScorer: replaced in 0 files this pass.
@pytest.mark.age tests: 0 created; shared age_store fixture added and skips when GRAPH_DSN is not set.
Circular tests deleted: 0.
S2P tests: 2094 passed, 0 skipped under -m "not age". Focused migrated tests: 26 passed. Targeted mypy for changed routers: clean.
Full backend/app mypy: still fails on pre-existing errors in vld_preseed.py, evidence_provider.py, routers/s2p.py, and main.py; no touched app file errors.

## MOCK-FIX-S2P-B (Sep 23, 2026)
Files modified: backend/tests/test_centroid_explorer.py, backend/tests/test_l5_conservation_s2p_hook.py, backend/tests/test_l5_dk_s2p_hook.py, backend/tests/test_s2p_auto_approve_gate.py, backend/tests/test_s2p_context_builder.py, backend/tests/test_supplier_intelligence.py, backend/tests/test_s2p_audit_export.py.
Total S2P REPLACE complete: 7 of 19. Direct fake-pattern cleanup also completed in auto-approve gate and supplier intelligence.
FakeGraphStore replacements this pass: centroid explorer, L5 conservation hook, L5 DK hook, context builder, audit export, supplier intelligence.
FakeScorer replacements this pass: 0; minimal scorer/spy doubles retained where tests cover scorer math or no-learn call boundaries rather than graph persistence.
@pytest.mark.age tests created: 0.
Circular tests deleted: 0.
Focused migrated tests: 148 passed.
S2P tests: 2094 passed, 0 skipped under -m "not age".
Mypy backend/app: still fails on pre-existing app errors in vld_preseed.py, evidence_provider.py, routers/s2p.py, and main.py; no touched test-file failures.

## MOCK-FIX-S2P-C (Sep 23, 2026)
Files modified: backend/tests/test_s2p_active_age_phase_b.py, backend/tests/test_s2p_shadow_phase2.py, backend/tests/test_s2p_score_endpoint.py, backend/tests/test_s2p_graph_reader.py, backend/tests/test_s2p_situation_pattern.py, backend/tests/test_situation_traversals.py, backend/tests/test_l5_full_flow_s2p.py.
Remaining REPLACE files checked without code changes: backend/tests/test_learning_state_scoping.py, backend/tests/test_s2p_autonomy.py, backend/tests/test_situation_closure.py, backend/tests/test_situation_graph_enrichment.py.
Total S2P REPLACE complete: 19 of 19.
S2P tests: 2094 passed under -m "not age".
Notes: active/shadow AGE tests now use InMemoryGraphStore-backed tracking stores; score/context and graph-reader tests use protocol stores; situation traversals use real memory-store entity links; L5 full-flow is isolated from proposal-service SQLite side effects.

## C5-SILENT-SUB (Sep 23, 2026)
Fixed: P2-049/050/051.
Learn response includes persistence status.
The `/api/learn` response now reports `persistence.conservation_l5`,
`persistence.centroid_l5`, and `persistence.dk_l5`, with top-level status
`complete` only when all three persistence operations succeed, otherwise
`partial`. L5 conservation and DK persistence helpers now return explicit
success/failure values; centroid failure remains non-destructive and does not
clear scorer consolidation state.
Tests: 23 focused L5 persistence tests and 16 S2P core-router tests passed; one broader pre-existing ledger
test failed on a missing `proposal_id` field. Mypy: clean for
`backend/app/routers/s2p.py`.

## C5-SILENT-SUB (Sep 23, 2026)
Fixed: P2-049/050/051.
Learn response includes persistence status; legacy outcome response now mirrors the same conservation_l5/centroid_l5/dk_l5 status.
Tests: 161 passed, 1933 deselected for -k "learn or persist". mypy: clean.
## PERF-FIX-S2P (2026-09-24)
Accumulation bug: root cause=summary cache keyed by recreated graph-store identity, causing unbounded entries and misses. Fix=domain-scoped bounded cache with 5s TTL.
Broken endpoints: fixed.
TTL cache: verified/added for tab state and preview decision reads.

## PERF-S2P-V2 (2026-09-25)
preview/queue: 0.18s R2, 0.17s R3 (was 4.75s). Method: 30s scored-invoice cache keyed by stable scorer state and verified-count token, not graph-store identity.
preview/suppliers: 0.40s R2, 0.44s R3 (was 5.61s). Method: 30s graph supplier-profile cache preserving the full supplier/outcome contract.
performance/summary: 0.01s R2, 0.01s R3 (was 3.98s). Method: stable summary cache plus AGE aggregate action count and aggregate category coverage.
evidence/compliance: 0.02s R2, 0.01s R3 (was HTTPError). Fix: compliance keeps cached-static contract but falls back when optional factor/extinction graph evidence is unavailable.
insight/fingerprint: 0.02s R2, 0.02s R3 (was HTTPError). Fix: robust numeric factor fallback for invoice fingerprint payloads.
