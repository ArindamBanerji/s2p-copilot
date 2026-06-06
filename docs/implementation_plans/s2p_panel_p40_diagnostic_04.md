# S2P Panel + P40 Auto-Approve Diagnostic 04

Date: 2026-06-05
Model: gpt-5.3
Task Type: Diagnostic document creation only; no source code changes.
Repo: s2p-copilot, with S2P frontend panel inspected in copilot-sdk.
Diagnostic Scope: P41 CentroidExplorerPanel live-vs-mock analysis and P40 DK-weighted auto-approve hook analysis.
Prior Diagnostics Read: `copilot-sdk/docs/implementation_plans/sdk_backend_endpoint_map_diagnostic_02.md`

## Executive Summary

* P41 CentroidExplorerPanel verdict: DROP. The panel uses live API wrapper calls, and matching backend explorer endpoints exist.
* P40 Auto-Approve DK hook verdict: MEDIUM SUPPLEMENT.
* Biggest finding: `CentroidExplorerPanel.tsx` is not at the direct candidate path; it exists at `copilot-sdk/apps/s2p/frontend/src/components/CentroidExplorerPanel.tsx` and calls `getDrift(category)` plus `getDKWeights()`.
* Biggest architecture risk: DK weight reading currently lives in router-local helpers in `s2p_explorer.py`; auto-approve business logic should not import a router helper directly.
* Recommended next prompt: implement a shared S2P DK-weight helper/service and pass DK-adjusted confidence into `_should_auto_approve` from the S2P score route.

## Path Resolution

* CLAUDE_SDK value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk`
* CLAUDE_S2P value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
* Repo path used: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
* Report path used: `s2p-copilot/docs/implementation_plans/s2p_panel_p40_diagnostic_04.md`
* CentroidExplorerPanel path: `copilot-sdk/apps/s2p/frontend/src/components/CentroidExplorerPanel.tsx`
* auto_approve.py path: `s2p-copilot/backend/app/domains/s2p/auto_approve.py`
* s2p_learning_gate.py path: `s2p-copilot/backend/app/services/s2p_learning_gate.py`
* s2p_explorer.py path: `s2p-copilot/backend/app/routers/s2p_explorer.py`
* Prior diagnostics found:
  * `copilot-sdk/docs/implementation_plans/sdk_backend_endpoint_map_diagnostic_02.md`: YES
  * Existing `s2p-copilot/docs/implementation_plans/s2p_panel_p40_diagnostic_04.md`: NO
  * Other directly relevant S2P docs found: `s2p_failure_causal_diagnostic_plan.md`, `gap_h1_s2p_agentevolver_design.md`, `s2p_age_cutover_design_plan.md`, `s2p_age_shadow_design_plan.md`

## CLAUDE.md Relevant Notes

* Both repos prohibit direct git usage.
* Docs are aspirational until proven in code; inspect actual source files.
* Cite file and line for behavioral claims.
* S2P must remain domain-isolated and must not depend on SOC-specific code or constants.
* S2P canonical tensor is `(5, 5, 7)` with `penalty_ratio = 5.0`.
* Tests are required after source changes in normal work, but this diagnostic explicitly prohibited tests and allowed only this Markdown write.

## Part 1 - P41 CentroidExplorerPanel

### File inspected

* `copilot-sdk/apps/s2p/frontend/src/components/CentroidExplorerPanel.tsx`
* `copilot-sdk/apps/s2p/frontend/src/api.ts`
* `s2p-copilot/backend/app/routers/s2p_explorer.py`
* `s2p-copilot/backend/app/main.py`

### Live API evidence

| Line | Signal | URL / Function | Evidence |
| ---: | ------ | -------------- | -------- |
| Panel L2 | API wrapper import | `getDKWeights`, `getDrift` | Panel imports live API wrapper functions from `../api`. |
| Panel L25 | API call | `Promise.all([getDrift(category), getDKWeights()])` | Panel loads drift and DK weights on category changes. |
| API L54 | API base URL | `VITE_API_URL || "http://127.0.0.1:8002"` | API wrapper targets runtime backend URL. |
| API L56-L57 | Fetch wrapper | `apiGet<T>(path)` uses `fetch(`${API_URL}${path}`)` | Live fetch path, not static data. |
| API L274 | Drift wrapper | `getDrift(category)` | Calls `/api/s2p/explorer/drift/${category}`. |
| API L278 | DK wrapper | `getDKWeights()` | Calls `/api/s2p/explorer/dk-weights`. |
| Explorer L17 | Backend router prefix | `/api/s2p/explorer` | S2P explorer router prefix. |
| Explorer L462-L463 | Backend endpoint | GET `/drift/{category}` | Provides category drift/centroids response. |
| Explorer L479-L486 | Backend endpoint | GET `/dk-weights` | Returns `{factors, weights, available}`. |
| Main | Router registration | `app.include_router(s2p_explorer_router)` | S2P app mounts the explorer router. |

### Mock/static data evidence

| Line | Signal | Classification | Evidence |
| ---: | ------ | -------------- | -------- |
| Panel L15 | Initial category state | LIVE_PRIMARY | Uses `S2P_CATEGORIES[0]` only to select initial live query category. |
| Panel L16-L17 | Nullable response state | LIVE_PRIMARY | `drift` and `dk` state start as null and are populated from API calls. |
| Panel L51 | Fallback factor source | LIVE_PRIMARY | Uses `drift?.factors ?? dk?.factors ?? []`; fallback is another live API response, not mock data. |
| Panel L29/L39 | Error fallback | LIVE_PRIMARY | Sets "Centroid explorer is unavailable" if live API fails. |

No `mock`, `fixture`, `hardcod`, `sample`, or `demo` signal was found in the panel scan.

### Data loading pattern

The panel uses `useState` for category, drift response, DK response, loading, and error state. A `useEffect` runs on category changes, sets loading/error state, calls `Promise.all([getDrift(category), getDKWeights()])`, then stores responses with `setDrift` and `setDk`. It shows loading and error UI and only renders centroid evidence when drift is available.

### P41 Verdict

* Verdict: DROP
* Remaining effort: validation only.
* Rationale: Frontend live calls and backend endpoints match by source inspection. No primary mock/static panel data path was observed.

## Part 2 - auto_approve.py

### File inspected

* `s2p-copilot/backend/app/domains/s2p/auto_approve.py`

### _should_auto_approve definition

* Signature: `_should_auto_approve(category: str, confidence: float, conservation_status: str, recommended_action: str, spot_check_fn: Callable[[], bool] | None = None) -> dict[str, Any]`
* Confidence input: plain `confidence: float`.
* Conservation input: `conservation_status: str`; auto-approve requires `"GREEN"`.
* Other inputs: `category`, `recommended_action`, optional `spot_check_fn`.
* Evidence:
  * Function definition at `auto_approve.py` L28-L34.
  * Category thresholds defined in `AUTO_APPROVE_THRESHOLDS` at L10-L16.
  * Raw confidence is compared to threshold at L45.
  * Conservation must equal `"GREEN"` at L54.
  * Recommended action must equal `AUTO_APPROVE_ACTION` at L63.

### Confidence origin

* Raw ProfileScorer output / adjusted / DK-weighted / unclear: raw scorer confidence at current call site.
* Evidence:
  * S2P score route obtains `scorer = _sdk_scorer(http_request)` and calls `scorer.score(...)` at `s2p.py` L1297-L1303.
  * The auto-approve call passes `score_result.confidence` at `s2p.py` L1326-L1331.
  * The response stores `auto_approve["confidence"] = score_result.confidence` at L1332.
  * No DK-adjusted confidence calculation was observed between scoring and `_should_auto_approve`.

### Stub/TODO review

* TODOs: none found in targeted scan.
* pass statements: none found in `auto_approve.py`.
* NotImplementedError: none found.
* Other stub signals: none found.

## Part 3 - s2p_learning_gate.py

### File inspected

* `s2p-copilot/backend/app/services/s2p_learning_gate.py`

### Computation summary

* DK/DiagonalKernel references:
  * Comments identify per-factor sigma thresholds and "DiagonalKernel GREEN threshold" at L27-L31.
  * Function accepts `sigma_max` at L49 and checks `sigma_max > S2P_SIGMA_RED` at L65.
* GREEN/AMBER/RED references:
  * Result status is documented as `"GREEN" | "AMBER" | "RED"` at L36.
  * RED returned when sigma is too high at L65-L74.
  * AMBER returned for insufficient decisions at L79-L91 and low override precision at L95-L106.
  * GREEN/AMBER returned after all conditions based on sigma at L113-L115.
* Threshold/confidence/q references:
  * Uses `MIN_VERIFIED_DECISIONS = 50` at L24 and `MIN_OVERRIDE_PRECISION = 0.40` at L25.
  * Uses sigma thresholds at L29-L31.
  * No `confidence` or `q` logic was found in the line evidence.
* Returns:
  * Returns `S2PLearningGateResult` with `status`, `learning_active`, `verified_decisions`, `override_precision`, `sigma_max`, `reason`, `recommendation`, and `gate_opened_at`.
* Evidence:
  * `evaluate_s2p_learning_gate` starts at L46.

### Relevance to P40

* Should be called by auto_approve.py: NO, not directly from the inspected code.
* Rationale: it evaluates learning activation state from aggregate decision count, override precision, and sigma max. P40 needs DK-weighted confidence at scoring/auto-approve time. The learning gate is relevant context for safety state, but it does not return a DK-adjusted threshold or confidence.

## Part 4 - s2p_explorer.py DK Weight Read Path

### File inspected

* `s2p-copilot/backend/app/routers/s2p_explorer.py`

### _read_dk_weights behavior

* Inputs: `scorer: Any`.
* Outputs: `list[float] | None`; returned weights are float-cast list values.
* Scorer assumptions:
  * Checks both `scorer` and `scorer.gae_scorer`.
  * Looks for attributes/methods named `dk_weights`, `precision_weights`, or `kernel_weights`.
  * Calls the value if callable and converts `.tolist()` values to Python lists.
* Evidence:
  * `_read_dk_weights` defined at L65.
  * Iterates over `(scorer, getattr(scorer, "gae_scorer", None))` at L66.
  * Checks `dk_weights`, `precision_weights`, and `kernel_weights` at L69.
  * Returns `float` list at L77.

### /dk_weights endpoint

* Exists: YES
* Path: `/api/s2p/explorer/dk-weights`
* Response shape: `{ "factors": list(config.factors), "weights": [], "available": False }` when unavailable/wrong length; otherwise `{ "factors": list(config.factors), "weights": _rounded(weights), "available": True }`.
* Evidence:
  * Router prefix is `/api/s2p/explorer` at L17.
  * Endpoint decorator is `@router.get("/dk-weights", response_model=GenericResponse)` at L479.
  * Function `dk_weights` starts at L480 and reads scorer/config/weights.
  * Return shapes are at L485-L486.

### Architecture boundary

* Router-local helper safe to reuse directly: NO
* Shared helper/service recommended: YES
* Rationale: `_read_dk_weights` currently lives in `app/routers/s2p_explorer.py`, which is presentation/API layer code. `auto_approve.py` is domain logic. Importing router helpers into domain logic would invert dependencies. The safer later implementation is to extract DK-weight reading and DK-adjusted confidence logic into a service/helper used by both `s2p_explorer.py` and the S2P scoring/auto-approve path.

## Part 5 - _should_auto_approve Call Sites

| File | Line | Caller | Confidence Source | Scorer Available? | DK Weights Available? | Evidence |
| ---- | ---: | ------ | ----------------- | ----------------- | --------------------- | -------- |
| `s2p-copilot/backend/app/domains/s2p/auto_approve.py` | 28 | Function definition | Input parameter `confidence: float` | N/A | N/A | Definition only. |
| `s2p-copilot/backend/app/routers/s2p.py` | 1326 | `score_procurement_event` | `score_result.confidence` from `scorer.score(...)` | YES | Not currently read; scorer is available and could be passed to shared helper | `scorer = _sdk_scorer(http_request)` at L1297; `score_result = scorer.score(...)` at L1299-L1303; `_should_auto_approve(... score_result.confidence ...)` at L1326-L1331. |

## Part 6 - Existing Backend Endpoints for P40/P41

| File | Line | Endpoint / Signal | Relevance | Evidence |
| ---- | ---: | ----------------- | --------- | -------- |
| `s2p_explorer.py` | 447 | GET `/api/s2p/explorer/centroid/{category}/{action}` | P41 backend endpoint | Reads scorer centroids for category/action. |
| `s2p_explorer.py` | 462 | GET `/api/s2p/explorer/drift/{category}` | P41 panel primary drift endpoint | Panel calls `getDrift(category)`. |
| `s2p_explorer.py` | 479 | GET `/api/s2p/explorer/dk-weights` | P41 panel DK endpoint; P40 weight-source clue | Panel calls `getDKWeights()`. |
| `s2p_explorer.py` | 489 | GET `/api/s2p/explorer/ranking` | DK-aware ranking endpoint | Uses `_safe_read_dk_weights`, falls back to sigma ranking. |
| `s2p_explorer.py` | 533 | GET `/api/s2p/explorer/contribution` | Contribution analysis | Uses stored score result/factor vector and centroid distances. |
| `s2p.py` | 1365 | GET `/api/s2p/auto-approve/stats` | P40 telemetry | Returns auto-approve stats from `auto_approve.py`. |
| `s2p.py` | 1370 | GET `/api/s2p/auto-approve/expansion-proof` | P40 threshold expansion proof | Calls `build_expansion_proof`. |
| `s2p.py` | 1595 | GET `/api/s2p/iks` | IKS endpoint | Uses `scorer.trajectory()` and `_iks_from_trajectory`. |
| `s2p.py` | 1606 | GET `/api/s2p/learning-gate` | Learning gate endpoint | Exposes learning gate state. |

## P40 Scope Recommendation

* Classification: MEDIUM SUPPLEMENT
* Likely files for later implementation:
  * New shared helper/service, likely under `s2p-copilot/backend/app/services/` or `s2p-copilot/backend/app/domains/s2p/`.
  * `s2p-copilot/backend/app/routers/s2p_explorer.py` to use the shared DK weight helper instead of its router-local helper.
  * `s2p-copilot/backend/app/routers/s2p.py` score path around L1297-L1332 to compute/pass DK-adjusted confidence.
  * `s2p-copilot/backend/app/domains/s2p/auto_approve.py` only if the domain gate should expose/report both raw and DK-adjusted confidence.
* Forbidden architecture shortcuts:
  * Do not import `_read_dk_weights` from `app.routers.s2p_explorer` into `auto_approve.py` or other domain logic.
  * Do not make the domain gate depend on FastAPI `Request` or router state.
  * Do not silently replace raw confidence without preserving/debugging the raw scorer confidence in output/telemetry.
* Required tests for later implementation:
  * Unit tests for shared DK weight extraction from scorer and `gae_scorer`.
  * Unit tests for DK-adjusted confidence calculation, including unavailable/wrong-length weights.
  * Router or domain tests proving `_should_auto_approve` uses the adjusted confidence in the score path.
  * Regression test ensuring `/api/s2p/explorer/dk-weights` still returns the same shape.
* Repo-local design plan needed before implementation: NO
* Rationale: scorer is already available at the `_should_auto_approve` call site, and DK weight extraction logic already exists, but it is router-local and must be extracted before reuse. This is more than a tiny pass-through but not a full architecture rebuild.

## Final Decision Table

| Prompt                    | Verdict | Remaining Effort | Key Evidence |
| ------------------------- | ------- | ---------------- | ------------ |
| P40 S2P-AUTO-APPROVE      | MEDIUM SUPPLEMENT | Shared helper plus score-path wiring and tests | Raw `score_result.confidence` is passed at `s2p.py` L1326-L1331; DK reading exists in `s2p_explorer.py` L65-L77. |
| P41 S2P-CENTROID-EXPLORER | DROP | Validation only | Panel calls live `getDrift` and `getDKWeights`; backend exposes matching `/drift/{category}` and `/dk-weights` endpoints. |

## Diagnostic Limitations

* This diagnostic does not validate runtime behavior.
* This diagnostic does not run tests.
* This diagnostic does not implement DK-weighted auto-approve.
* This diagnostic does not prove frontend/backend integration unless URLs and backend endpoints both match from code inspection.
* DROP verdict means code-inspection suggests no implementation prompt is needed, not that E2E validation passed.

## Recommended Next Step

Run a small implementation supplement for P40: extract shared DK weight reading/adjusted-confidence logic, wire it into the S2P score route before `_should_auto_approve`, and update explorer DK endpoint to reuse the shared helper. Drop P41 from implementation queue and keep only validation/E2E coverage.
