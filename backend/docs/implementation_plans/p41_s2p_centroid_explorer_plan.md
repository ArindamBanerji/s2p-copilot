# P41 S2P Centroid Explorer Implementation Plan

Last updated: 2026-06-14

## Executive Verdict

READY_FOR_IMPLEMENTATION: YES, for a read-only backend/API implementation and a small frontend integration against the active S2P app.

P41 should not reuse the existing `/api/s2p/explorer/*` implementation as-is. That router already exposes centroid-related routes, but it reads private scorer internals (`gae_scorer.centroids` / `scorer.centroids`) in `s2p-copilot/backend/app/routers/s2p_explorer.py:39-45`, and it includes a mutating centroid import route that assigns `gae_scorer.centroids = imported` in `s2p-copilot/backend/app/routers/s2p_explorer.py:381-415`. P41 should create a new read-only route family under `/api/s2p/centroid/*` and use public scorer/read-store APIs only.

Smallest safe implementation:

- Create `app/services/s2p_centroid_explorer.py`.
- Create `app/routers/s2p_centroid.py`.
- Mount the new router in `app/main.py`.
- Add backend tests in `tests/test_s2p_centroid_explorer.py`.
- Add API E2E in `gen-ai-roi-demo-v4-v50/frontend/tests/e2e/s2p_centroid_explorer_api.spec.ts`.
- Optionally update `copilot-sdk/apps/s2p/frontend` to consume the new API after backend closeout.

## Discovery Findings

### Authority And Repo Grounding

- `s2p-copilot/CLAUDE.md` and `copilot-sdk/CLAUDE.md` require repo-grounded claims and no git commands.
- `copilot-sdk/CLAUDE.md` requires reading `copilot-sdk/graphify-out/GRAPH_REPORT.md` for architecture questions.
- The graph report identifies `CompoundingScorer` and `SQLiteGraphStore` as core graph/scoring nodes, so P41 should avoid new cross-cutting SDK mutations and stay in S2P service/router code.

### Current Scoring And Decision Flow

- `POST /api/s2p/score` is implemented by `score_procurement_event()` in `s2p-copilot/backend/app/routers/s2p.py:1540-1646`.
- The route computes S2P factors and a factor vector at `s2p-copilot/backend/app/routers/s2p.py:1574-1580`.
- It calls the SDK scorer at `s2p-copilot/backend/app/routers/s2p.py:1581-1587`.
- The response preserves `factor_vector`, `factor_names`, and `decision_id` at `s2p-copilot/backend/app/routers/s2p.py:1632-1645`.
- `CompoundingScorer.score()` persists the scorer input vector and metadata before returning a score result at `copilot-sdk/copilot_sdk/scoring/scorer.py:240-285`.
- `SQLiteGraphStore.write_decision()` stores `factor_vector_json`, `recommended_action`, `recommended_index`, `confidence`, and `probabilities_json` at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:985-1007`.
- `SQLiteGraphStore.get_decision()` returns a reconstructed decision record at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:1826-1833`.
- The decision record includes `category`, `category_index`, `factors`, `factor_vector`, `recommended_action`, `recommended_index`, `confidence`, `probabilities`, `metadata`, and `created_at` at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2843-2867`.

## Centroid Access

### Public API

- `CompoundingScorer.from_preset()` loads the latest persisted centroids through `graph_store.load_latest_centroids(preset.name)` and falls back to bootstrap centroids when missing at `copilot-sdk/copilot_sdk/scoring/scorer.py:189-195`.
- `CompoundingScorer.get_centroid(category, action)` is a public read method at `copilot-sdk/copilot_sdk/scoring/scorer.py:334-351`.
- The method validates category/action, checks tensor shape `(n_categories, n_actions, n_factors)`, and returns a copied list at `copilot-sdk/copilot_sdk/scoring/scorer.py:336-351`.

### Shape

Runtime S2P shape comes from `S2PPreset`:

- Categories: `price_variance`, `quantity_mismatch`, `duplicate_risk`, `contract_gap`, `format_compliance` at `copilot-sdk/copilot_sdk/scoring/presets/s2p.py:22-28`.
- Actions: `auto_approve`, `hold_for_review`, `escalate_to_buyer`, `flag_leakage`, `refer_to_specialist` at `copilot-sdk/copilot_sdk/scoring/presets/s2p.py:29-35`.
- Factors: `match_status`, `amount_variance_ratio`, `duplicate_score`, `supplier_exception_history`, `payment_terms_impact`, `commodity_index_correlation`, `tax_regulatory_compliance` at `copilot-sdk/copilot_sdk/scoring/presets/s2p.py:36-44`.
- Penalty ratio: `5.0` at `copilot-sdk/copilot_sdk/scoring/presets/s2p.py:47-49`.
- Bootstrap centroids are initialized as a `(5, 5, 7)` tensor at `copilot-sdk/copilot_sdk/scoring/presets/s2p.py:72-85`.

### Implementation Rule

P41 must use `scorer.get_centroid(category, action)` for one centroid and iterate categories/actions for `/all`. It should not read `scorer._scorer`, `scorer.gae_scorer`, `centroids`, `mu`, or `_mu` directly. Existing private access in `s2p_explorer.py` is a reference-only anti-pattern for P41.

## Decision Vector Access

### Lookup Path

Use `graph_store.get_decision(decision_id)` when the caller supplies a decision id. If no exact id is found, the implementation may optionally scan `get_all_decisions("s2p")` by metadata invoice id, but that fallback must be labeled as lookup fallback, not primary identity.

Public GraphStore methods exist in `copilot-sdk/copilot_sdk/graph/protocol.py:39-54`. SQLite implements `get_decision`, `get_all_decisions`, and `get_verified_decisions` at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:1826-1885`.

### Required Fields

The explanation endpoint can use:

- `decision["factor_vector"]` from `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2859`.
- `decision["category"]` and `decision["category_index"]` from `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2856-2857`.
- `decision["recommended_action"]`, `recommended_index`, `confidence`, and `probabilities` from `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2860-2863`.
- `decision["metadata"]` for invoice/supplier identifiers from `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2865`.

If a decision is missing or lacks a 7-value vector, return 404 or a safe `explain_available=false` response with a clear reason. Do not reconstruct values from fixtures unless the response explicitly says reconstruction was used and not the original scorer input.

## DK Weights Access

### Public API

- `CompoundingScorer.get_dk_weights()` is public and returns a copied `list[list[float]]` or `None` at `copilot-sdk/copilot_sdk/scoring/scorer.py:327-332`.
- `s2p_evidence.py` already calls `get_dk_weights`, `get_category_phase`, `get_verified_count`, and `get_centroid` read-only for trust evidence at `s2p-copilot/backend/app/routers/s2p_evidence.py:63-75`.
- `format_trust_explanation()` normalizes DK weights and falls back to learning/pre-transition behavior when weights are missing at `s2p-copilot/backend/app/services/s2p_trust_explanations.py:133-193`.
- Missing DK weights return `learning_message = "System is learning factor reliability. All factors weighted equally."` at `s2p-copilot/backend/app/services/s2p_trust_explanations.py:11` and `s2p-copilot/backend/app/services/s2p_trust_explanations.py:179-181`.

### P41 Behavior

P41 should include per-factor DK weight provenance:

- `source="scorer"`, `provenance_tier="learned"`, `measured=true` only when DK weights exist.
- `source="unavailable"`, `provenance_tier="unavailable"`, `measured=false`, label `DK trust weight learning/unavailable` when missing.
- Use uniform display weights only for visualization fallback, and label them as display fallback, not learned DK.

## P39 Evidence Boundary

P39 supplier enrichment is available but must remain display/context only for P41.

- P39B namespace is `s2p_supplier_metrics` at `s2p-copilot/backend/app/services/s2p_enrichment.py:22`.
- The service reads persisted enrichment through `read_entity_enrichment()` at `s2p-copilot/backend/app/services/s2p_enrichment.py:203-216`.
- Summary/alerts use persisted enrichment rows through `list_entity_enrichments()` at `s2p-copilot/backend/app/services/s2p_enrichment.py:218-268`.
- Verified metrics use `ProvenancedValue.from_verified()` only where verified decision counts exist, while fixture lead-time and OTIF values use fixture provenance at `s2p-copilot/backend/app/services/s2p_enrichment.py:394-410`.
- P38 already attaches enrichment under `supplier_node.properties["enrichment"]` without changing the base supplier node source at `s2p-copilot/backend/app/services/s2p_context_builder.py:188-213` and reads it through `read_entity_enrichment()` at `s2p-copilot/backend/app/services/s2p_context_builder.py:228-247`.

P41 may display supplier enrichment for a decision's supplier id, but it must not use P39 context/fixture values as scoring proof, centroid deltas, DK weights, or factor inputs.

## Centroid History And Drift

### Available Store API

- `GraphStore.get_centroid_checkpoints(domain, **kwargs)` exists in the protocol at `copilot-sdk/copilot_sdk/graph/protocol.py:78-83`.
- SQLite stores centroid checkpoints in `centroid_checkpoints` and supports `get_centroid_checkpoints()` at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2326-2353`.
- SQLite checkpoint rows include centroid arrays and metadata when converted by `_checkpoint_from_row()` at `copilot-sdk/copilot_sdk/graph/sqlite_store.py:2901-2915`.
- S2P already exposes a generic trajectory endpoint that reads `get_centroid_checkpoints()` at `s2p-copilot/backend/app/routers/s2p_performance.py:194-210`.
- S2P learn/outcome paths attempt to persist L5 centroid state after verified learning at `s2p-copilot/backend/app/routers/s2p.py:1738-1756` and `s2p-copilot/backend/app/routers/s2p.py:1855-1870`, while `_persist_l5_centroid_state()` writes only through a centroid learning store at `s2p-copilot/backend/app/routers/s2p.py:439-493`.

### P41 Behavior

Drift endpoint should be honest:

- If `get_centroid_checkpoints` is unavailable, return `supported=false`, `points=[]`, `reason="centroid checkpoint history unsupported"`.
- If available but empty, return `supported=true`, `points=[]`, `reason="no centroid checkpoint history for category/action"`.
- If checkpoints exist, filter by category/action when possible and derive distances from real checkpoint centroids only.
- Do not synthesize drift from current bootstrap centroids.

## Backend API Proposal

Create `s2p-copilot/backend/app/routers/s2p_centroid.py` with prefix `/api/s2p/centroid`.

### GET `/api/s2p/centroid/all`

Read-only.

Response:

```json
{
  "domain": "s2p",
  "tensor_shape": [5, 5, 7],
  "categories": ["..."],
  "actions": ["..."],
  "factors": ["..."],
  "centroids": [
    {
      "category": "price_variance",
      "action": "auto_approve",
      "values": [{"factor": "match_status", "value": 0.95}]
    }
  ],
  "source": "scorer",
  "read_only": true,
  "warnings": []
}
```

Errors: 503 when scorer is missing or lacks public `get_centroid`; 500 only for unexpected serialization failure.

### GET `/api/s2p/centroid/{category}/{action}`

Read-only.

Response includes category/action indexes, factor rows, source/provenance, and `read_only=true`. Unknown category/action returns 404.

### GET `/api/s2p/centroid/explain/{decision_id}`

Read-only.

Response:

```json
{
  "decision_id": "...",
  "category": "...",
  "recommended_action": "...",
  "confidence": 0.91,
  "factor_names": ["..."],
  "factor_values": [{"factor": "...", "value": 0.7, "source": "stored_decision"}],
  "centroid": [{"factor": "...", "value": 0.5, "source": "scorer"}],
  "contributions": [
    {
      "factor": "amount_variance_ratio",
      "value": 0.42,
      "centroid_value": 0.30,
      "distance": 0.12,
      "dk_weight": null,
      "weighted_contribution": 0.12,
      "factor_value_provenance": {"source": "stored_decision"},
      "dk_weight_provenance": {"source": "unavailable", "provenance_tier": "unavailable"}
    }
  ],
  "trust_available": false,
  "learning_message": "System is learning factor reliability. All factors weighted equally.",
  "supplier_enrichment": {},
  "read_only": true,
  "warnings": []
}
```

Error cases:

- 404 if decision id does not exist.
- 422/409 if stored decision category/action is outside S2P config.
- 409 if factor vector length does not equal 7.
- 503 if scorer centroid API is unavailable.

Contribution math:

- `distance = abs(factor_value - centroid_value)`.
- `weighted_contribution = distance * dk_weight` when DK exists.
- fallback `weighted_contribution = distance` when DK is unavailable, explicitly labeled learning/unavailable.
- Sort descending by weighted contribution, then factor name.

### GET `/api/s2p/centroid/drift/{category}/{action}`

Read-only.

Response:

```json
{
  "category": "...",
  "action": "...",
  "supported": true,
  "points": [],
  "reason": "no centroid checkpoint history for category/action",
  "source": "graph_store.get_centroid_checkpoints",
  "read_only": true
}
```

Unknown category/action returns 404. Unsupported history returns 200 with `supported=false` and empty points.

## Backend Service Design

Create `S2PCentroidExplorerService` in `app/services/s2p_centroid_explorer.py`.

Constructor inputs:

- `scorer: Any`
- `graph_store: Any | None`
- `supplier_enrichment_service: S2PSupplierEnrichmentService | None = None` or direct read helper

Methods:

- `all_centroids()`
- `centroid(category, action)`
- `explain_decision(decision_id)`
- `drift(category, action, limit=50)`

Hard rules:

- No scorer mutation.
- No `learn()`.
- No `write_outcome()`.
- No `write_decision()`.
- No P39 writes.
- No private scorer attributes unless a test-only fake lacks public methods; production path must reject missing public APIs.

## Frontend Proposal

### Active Frontend Location

Primary active S2P UI is `copilot-sdk/apps/s2p/frontend`:

- It has its own package and Vite app at `copilot-sdk/apps/s2p/frontend/package.json`.
- It depends on Recharts at `copilot-sdk/apps/s2p/frontend/package.json`.
- It has S2P API helpers, including existing explorer helpers for centroid, drift, and DK weights at `copilot-sdk/apps/s2p/frontend/src/api.ts:271-282`.
- It has typed S2P categories/actions/factors at `copilot-sdk/apps/s2p/frontend/src/types.ts:1-25`.
- It already has `CentroidExplorerPanel.tsx`, but that panel consumes existing `/api/s2p/explorer/*` endpoints at `copilot-sdk/apps/s2p/frontend/src/components/CentroidExplorerPanel.tsx:1-25`.
- Current app tabs are defined in `copilot-sdk/apps/s2p/frontend/src/App.tsx:10-18`; `PerformanceScreen` is a natural mount point for centroid explorer work at `copilot-sdk/apps/s2p/frontend/src/screens/PerformanceScreen.tsx:24-45`.

`gen-ai-roi-demo-v4-v50/frontend` remains the active API E2E location for backend route smoke tests because S2P API specs already live there, including P38/P39/P40 specs.

### Frontend Implementation

Recommended sequence:

1. Backend/API first.
2. Update `copilot-sdk/apps/s2p/frontend/src/api.ts` to call `/api/s2p/centroid/*`.
3. Add/replace a `CentroidExplorerPanel` that shows:
   - category/action selectors,
   - current centroid vector,
   - explain-by-decision-id input,
   - contribution table,
   - DK status,
   - P39 enrichment context badges if present,
   - honest empty drift state.
4. Mount on `PerformanceScreen` near existing `TrajectoryChart`.

Use existing Recharts dependency; do not add chart packages.

## Existing Route Compatibility

Existing `/api/s2p/explorer/*` tests and UI should continue passing during P41. P41 should not remove or mutate:

- `/api/s2p/explorer/centroid/{category}/{action}`
- `/api/s2p/explorer/drift/{category}`
- `/api/s2p/explorer/dk-weights`
- `/api/s2p/explorer/contribution`
- `/api/s2p/explorer/import/centroids`

However, P41 code should not call the import route or use its mutating helpers.

## Test Plan

### Backend Unit/Service Tests

Create `tests/test_s2p_centroid_explorer.py`.

Required tests:

- `test_all_centroids_uses_public_get_centroid_only`
- `test_centroid_endpoint_returns_5_5_7_shape`
- `test_centroid_unknown_category_404`
- `test_centroid_unknown_action_404`
- `test_explain_decision_uses_stored_factor_vector`
- `test_explain_missing_decision_404`
- `test_explain_missing_factor_vector_safe_error`
- `test_explain_does_not_reconstruct_from_fixture_unless_explicitly_labeled`
- `test_explain_contributions_sorted_descending`
- `test_explain_uses_dk_weights_when_available`
- `test_explain_missing_dk_uses_learning_unavailable_provenance`
- `test_explain_factor_value_provenance_separate_from_dk_weight_provenance`
- `test_explain_includes_p39_enrichment_as_display_only`
- `test_p39_fixture_context_metrics_do_not_affect_contribution_math`
- `test_drift_unsupported_returns_empty_with_reason`
- `test_drift_empty_history_returns_empty_with_reason`
- `test_drift_uses_real_centroid_checkpoints_when_present`
- `test_service_does_not_call_learn_write_outcome_or_write_decision`
- `test_existing_explorer_routes_still_pass`
- `test_p37_trust_explanation_still_present`
- `test_p38_context_builder_still_passes`

### Backend Route Tests

Route tests should use `TestClient` and fakes/spies that fail if:

- `learn()` is called.
- `write_outcome()` is called.
- `write_decision()` is called.
- `write_entity_enrichment()` is called.
- private scorer attributes are touched in the production service path.

### API E2E

Create `gen-ai-roi-demo-v4-v50/frontend/tests/e2e/s2p_centroid_explorer_api.spec.ts`.

Coverage:

- `GET /api/s2p/centroid/all` returns shape and read-only metadata.
- `GET /api/s2p/centroid/{category}/{action}` returns seven factor rows.
- Unknown category/action returns safe 404.
- Missing decision explain returns safe 404.
- Drift returns either real points or an honest empty/unsupported response.
- Responses do not claim DK learned weights when DK is unavailable.
- Responses do not include P39 fixture/context metrics as scoring inputs.

Use `S2P_API_BASE_URL ?? "http://127.0.0.1:8002"`. Do not assume Playwright starts the backend.

### Frontend Tests

If UI is implemented:

- Run `npm run typecheck` in `copilot-sdk/apps/s2p/frontend`.
- Add or update `copilot-sdk/e2e/s2p/observability.spec.ts` or a new P41 UI spec only after the backend API is available.
- Do not run Playwright unless the live stack is explicitly confirmed.

## Validation Plan

Backend:

```powershell
cd s2p-copilot\backend
python -m pytest tests/test_s2p_centroid_explorer.py -q --timeout=120
python -m pytest tests/test_explorer.py tests/test_s2p_explorer_router.py tests/test_s2p_context_builder.py tests/test_s2p_evidence.py tests/test_s2p_trust_explanations.py -q --timeout=120
python -m pytest tests/ -q --timeout=120
python -m py_compile app/services/s2p_centroid_explorer.py app/routers/s2p_centroid.py
```

Frontend:

```powershell
cd copilot-sdk\apps\s2p\frontend
npm run typecheck
```

API E2E, only after live backend is explicitly confirmed:

```powershell
cd gen-ai-roi-demo-v4-v50\frontend
$env:S2P_API_BASE_URL="http://127.0.0.1:8002"
npx playwright test tests/e2e/s2p_centroid_explorer_api.spec.ts
```

Later live validation with canonical launcher:

```powershell
cd copilot-sdk
python demo.py --s2p --no-browser
```

## Forbidden Scope

- No scorer internals modification.
- No GraphStore protocol modification.
- No conservation changes.
- No P39 mutation.
- No scorer learning, outcome writes, or decision writes.
- No P39 factor feedback or `factor_eligible` consumption.
- No package dependency changes unless separately approved.
- No git commands.
- No AGE smoke gate during P41 implementation.

## Explicit No-Go Conditions

Stop and return a design blocker if:

- `scorer.get_centroid(category, action)` is unavailable in the active app state.
- `graph_store.get_decision(decision_id)` and `get_all_decisions("s2p")` are both unavailable.
- Stored decisions do not include the original factor vector and no explicitly approved reconstruction strategy exists.
- DK weight access requires private scorer fields.
- P39 enrichment can only be read by writing or recomputing enrichment on the hot path.
- Centroid drift can only be shown by fabricating history.
- A requested UI mount would require adding chart dependencies.

## AGE Smoke Gate Note

P41 is the last planned S2P item before the AGE smoke gate. Do not run the AGE smoke gate during P41. After P41 closeout, run the AGE smoke gate separately. If AGE fails, generate a migration/readiness fixer rather than folding AGE repair into P41.

## Implementation Addendum - 2026-06-14

Backend/API implementation scope:

- Created `app/services/centroid_explorer.py`.
- Created `app/routers/centroid_router.py`.
- Mounted the router additively from `app/main.py`.
- Added `tests/test_centroid_explorer.py`.
- Added API E2E spec `gen-ai-roi-demo-v4-v50/frontend/tests/e2e/s2p_centroid_explorer_api.spec.ts`.

Implemented endpoint contract:

- `GET /api/s2p/centroid/all`
- `GET /api/s2p/centroid/{category}/{action}`
- `GET /api/s2p/centroid/explain/{decision_id}`
- `GET /api/s2p/centroid/drift/{category}/{action}`

Closest-action semantics:

- `closest_action` is computed as the action with minimum total L2 distance from the stored decision factor vector to each action centroid in the decision category.
- `factor_contributions` are computed against the `closest_action` centroid and sorted by weighted distance.
- Summary wording is non-causal. When `closest_action` differs from `recommended_action`, the response states that centroid comparison is explanatory context and not a replacement for the scorer decision.

DK fallback:

- DK weights are read only through public `get_dk_weights()` when available.
- Missing or invalid DK weights return `dk_status="learning"` and use uniform display weights for ordering only.
- Missing DK is not labeled learned trust.

P39 boundary:

- P39 supplier enrichment is read through `read_entity_enrichment()` only when a supplier id is present.
- P39 evidence is returned as display/provenance context and is not used for L2 distance, closest-action selection, contribution weighting, or summary ranking.

Drift behavior:

- Drift reads only `graph_store.get_centroid_checkpoints()`.
- If checkpoint history is unavailable, the endpoint returns `supported=false`, `reason="centroid_history_unavailable"`, and `points=[]`.
- If checkpoint history is supported but empty, the endpoint returns an honest empty state.
- No drift is fabricated from current centroids.

Frontend UI:

- Deferred. This pass added backend/API and API E2E only.

AGE smoke gate:

- Still deferred until P41 closeout.

Validation results:

- `python -m pytest tests/test_centroid_explorer.py -q --timeout=120`: 22 passed.
- `python -m pytest tests/test_s2p_auto_approve_gate.py tests/test_s2p_enrichment.py tests/test_s2p_context_builder.py tests/test_s2p_evidence.py tests/test_s2p_trust_explanations.py -q --timeout=120`: 154 passed.
- `python -m pytest tests/ -q --timeout=120`: 1289 passed, 10 skipped.
- `python -m py_compile app/services/centroid_explorer.py app/routers/centroid_router.py`: passed.
- `npm run typecheck` in `copilot-sdk/apps/s2p/frontend`: passed.
- Live API E2E was not run because no live backend was confirmed.
