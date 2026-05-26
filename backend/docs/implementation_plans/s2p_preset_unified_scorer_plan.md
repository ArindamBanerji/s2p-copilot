# S2P-PRESET Unified Scorer Plan

## 1. Executive Summary

S2P has a confirmed split scorer path. The live FastAPI app constructs `app.state.scorer` with `build_s2p_scorer`, which calls `CompoundingScorer.from_preset("s2p", graph_store=SQLiteGraphStore(...), reward_function=S2PRewardFunction())` and exposes the same scorer as `app.state.graph_store` and `app.state.s2p_reward_function` (`app/main.py:54-66`). The score, learn, and outcome endpoints use that SDK scorer via `_sdk_scorer(http_request)` (`app/routers/s2p.py:90-95`, `app/routers/s2p.py:679-685`, `app/routers/s2p.py:804-816`, `app/routers/s2p.py:869-889`). The IKS endpoint still imports and calls `get_s2p_iks()` from `app.domains.s2p.scorer`, which builds a separate legacy `ProfileScorer` singleton and returns a learning-disabled static/cold-start IKS payload (`app/routers/s2p.py:905-923`, `app/domains/s2p/scorer.py:15-44`, `app/domains/s2p/scorer.py:77-120`).

The request assumptions are mostly confirmed: the SDK has an S2P scoring preset and RL preset registry entry, and the S2P preset shape is 5 categories x 5 actions x 7 factors with penalty ratio 5.0 (`copilot_sdk/scoring/presets/__init__.py:8-13`, `copilot_sdk/rl/presets.py:16-33`, `copilot_sdk/scoring/presets/s2p.py:16-49`). A scope repair is needed for one assumption: `CompoundingScorer.from_preset("s2p")` without an explicit app-owned graph store failed in this environment with `sqlite3.OperationalError: unable to open database file`; the app already avoids that by passing `SQLiteGraphStore(effective, domain="s2p", decision_id_prefix="S2P-")` (`app/main.py:54-60`, `copilot_sdk/scoring/scorer.py:142-150`). Implementation should keep the app factory path and not switch to a bare no-arg preset constructor.

Proposed strategy: leave `app/main.py` intact unless tests reveal a reset helper issue; change only the `/api/s2p/iks` implementation in `app/routers/s2p.py` to read the active `app.state.scorer`, call `CompoundingScorer.trajectory()`, and adapt that result into the existing IKS response fields. Do not remove `app/domains/s2p/scorer.py` in the first implementation; migrate production routing first, then update tests that intentionally cover the legacy module.

READY_FOR_IMPLEMENTATION: YES, with the scope repair above.

## 2. Verified Preset / RL Registry Status

| Claim | Status | Evidence |
|---|---|---|
| S2P in scoring preset registry | YES | `PRESET_REGISTRY` imports `S2PPreset` and maps `"s2p": S2PPreset` (`copilot_sdk/scoring/presets/__init__.py:6-13`). Runtime command also printed `SCORING_HAS_S2P= True`. |
| S2P in RL preset registry | YES | `RL_PRESET_REGISTRY` maps `"s2p"` to `GradedFinancialRewardFunction` and `penalty_ratio: 5.0` (`copilot_sdk/rl/presets.py:16-33`). Runtime command also printed `RL_HAS_S2P= True`. |
| `from_preset("s2p")` works | PARTIAL | With `db_path=":memory:"`, runtime construction succeeded and produced a `CompoundingScorer`; without an explicit path it failed opening the SDK package data DB. The SDK default path is created from `Path(__file__).resolve().parents[1] / "data"` (`copilot_sdk/scoring/scorer.py:142-150`). The app uses an explicit `SQLiteGraphStore` instead (`app/main.py:54-60`). |
| S2P preset shape is 5x5x7 | YES | `S2PPreset.shape` sets `n_categories=5`, `n_actions=5`, and `n_factors=7` (`copilot_sdk/scoring/presets/s2p.py:16-45`). |
| S2P preset penalty ratio is 5.0 | YES | `S2PPreset.penalty_ratio` returns `5.0` (`copilot_sdk/scoring/presets/s2p.py:47-49`). |
| Actual app scorer has RL components | YES | `build_s2p_scorer` passes a graph store and `S2PRewardFunction` into `from_preset` (`app/main.py:54-60`); `from_preset` fills missing credit and exploration components when RL is enabled (`copilot_sdk/scoring/scorer.py:160-178`). Runtime import of `app.main.app` showed `reward_type=S2PRewardFunction`, `has_credit=True`, and `has_explorer=True`. |

Live S2P preset values from source:

- Categories: `price_variance`, `quantity_mismatch`, `duplicate_risk`, `contract_gap`, `format_compliance` (`copilot_sdk/scoring/presets/s2p.py:22-28`).
- Actions: `auto_approve`, `hold_for_review`, `escalate_to_buyer`, `flag_leakage`, `refer_to_specialist` (`copilot_sdk/scoring/presets/s2p.py:29-35`).
- Factors: `match_status`, `amount_variance_ratio`, `duplicate_score`, `supplier_exception_history`, `payment_terms_impact`, `commodity_index_correlation`, `tax_regulatory_compliance` (`copilot_sdk/scoring/presets/s2p.py:36-44`).

These match the app domain config names and dimensions (`app/domains/s2p/config.py:20-47`, `app/domains/s2p/config.py:80-90`, `app/domains/s2p/config.py:98-117`).

## 3. Current Scorer Architecture

### App SDK scorer setup

`app/main.py` defines `build_s2p_scorer(db_path: str | None = None)` and constructs `CompoundingScorer.from_preset("s2p", graph_store=SQLiteGraphStore(effective, domain="s2p", decision_id_prefix="S2P-"), reward_function=S2PRewardFunction())` (`app/main.py:54-60`). The app initializes one persistent scorer at startup and exposes its graph store and reward function on app state (`app/main.py:62-66`).

The scorer’s SDK constructor stores `_reward_fn`, `_credit`, and `_explorer` fields (`copilot_sdk/scoring/scorer.py:91-108`). `from_preset` loads the S2P preset, creates a `ProfileScorer` internally, and fills RL components through `get_rl_components` when `enable_rl` is true and any component is missing (`copilot_sdk/scoring/scorer.py:123-188`). The SDK learn path writes outcomes, saves centroid checkpoints, computes reward, updates exploration, and assigns factor credit (`copilot_sdk/scoring/scorer.py:249-385`).

### Legacy S2P scorer setup

`app/domains/s2p/scorer.py` defines module globals `_scorer` and `_initialized`, lazily builds a `gae.ProfileScorer`, and exposes `get_scorer()`, `reset_scorer()`, `update_scorer()`, `get_s2p_iks()`, and `score_event()` (`app/domains/s2p/scorer.py:15-44`, `app/domains/s2p/scorer.py:47-74`, `app/domains/s2p/scorer.py:77-148`). It is not initialized at import time because `_scorer` starts as `None`; it initializes on `get_scorer()` (`app/domains/s2p/scorer.py:15-26`).

`LEARNING_ENABLED` is false in the app config (`app/domains/s2p/config.py:85-90`). As a result, legacy `get_s2p_iks()` returns a cold-start payload without reading the active SDK scorer (`app/domains/s2p/scorer.py:77-120`).

### Production caller map

- `/api/s2p/score` validates category, builds or enriches invoice input, computes all factor values with `compute_all_factors`, then calls `scorer.score(...)` on `_sdk_scorer(http_request)` (`app/routers/s2p.py:638-685`).
- `/api/learn` validates action/reason code, resolves `_sdk_scorer(http_request)`, and calls `_learn_with_scorer(...)` (`app/routers/s2p.py:792-839`).
- `/api/s2p/outcome` validates outcome/action/factor vector, resolves `_sdk_scorer(http_request)`, ensures a decision exists, and calls `_learn_with_scorer(...)` (`app/routers/s2p.py:842-902`).
- `/api/s2p/iks` imports and calls `get_s2p_iks()` from the legacy module (`app/routers/s2p.py:905-923`).
- Search found no production router caller of legacy `score_event`; the production matches were limited to `/api/s2p/iks` importing `get_s2p_iks` (`app/routers/s2p.py:911-912`).

Current flow:

- `/api/s2p/score` -> `app.state.scorer` -> SDK `CompoundingScorer.score`.
- `/api/learn` -> `app.state.scorer` -> SDK `CompoundingScorer.learn`.
- `/api/s2p/outcome` -> `app.state.scorer` -> SDK `CompoundingScorer.learn`.
- `/api/s2p/iks` -> legacy `app.domains.s2p.scorer.get_s2p_iks()` -> legacy `ProfileScorer`/static cold-start path.

### Test caller map

The tests already use the app-state scorer pattern in many files by assigning `app.state.scorer = build_s2p_scorer()` and updating `app.state.graph_store` and `app.state.s2p_reward_function` (examples: `tests/test_s2p_core_router.py:61-63`, `tests/test_s2p_score_endpoint.py:41-46`, `tests/test_s2p_outcome.py:30-32`).

Tests still directly import or assert legacy behavior:

- `tests/test_s2p_iks.py` imports `get_s2p_iks` and `reset_scorer` from `app.domains.s2p.scorer` (`tests/test_s2p_iks.py:15-20`) and asserts cold-start `iks == 0.0`, `learning_active is False`, and `status == "CALIBRATING"` (`tests/test_s2p_iks.py:44-80`).
- `tests/test_s2p_scorer.py` imports `get_scorer`, `reset_scorer`, and `score_event` from the legacy scorer module and asserts `ProfileScorer` behavior (`tests/test_s2p_scorer.py:13-44`, `tests/test_s2p_scorer.py:58-61`).
- `tests/test_domain_isolation.py` imports legacy `score_event` for a domain isolation test (`tests/test_domain_isolation.py:208-213`).
- `tests/test_s2p_demo.py` imports legacy `reset_scorer` and `score_event` (`tests/test_s2p_demo.py:11-20`).

## 4. Frontend / API IKS Contract

### Current backend IKS response

The current `/api/s2p/iks` endpoint returns whatever legacy `get_s2p_iks()` returns, optionally overriding `decisions` from Neo4j (`app/routers/s2p.py:905-923`). The cold-start legacy payload contains:

- `iks`
- `d_max`
- `mean_drift`
- `decisions`
- `domain`
- `status`
- `learning_active`
- `interpretation`

Those keys are visible in `get_s2p_iks()` when `LEARNING_ENABLED` is false (`app/domains/s2p/scorer.py:77-95`) and in the learned branch (`app/domains/s2p/scorer.py:100-120`).

### SDK trajectory shape

`CompoundingScorer.trajectory()` returns `compute_trajectory(...)` over graph-store centroid checkpoints and verified decisions (`copilot_sdk/scoring/scorer.py:418-438`). The SDK `TrajectoryResult` dataclass has fields `points`, `current_iks`, `current_win_rate`, `decisions_total`, and `days_active` (`copilot_sdk/scoring/trajectory.py:17-24`). With no decisions, `compute_trajectory` returns one point with `current_iks=0.0`, `current_win_rate=0.50`, and `decisions_total=0` (`copilot_sdk/scoring/trajectory.py:35-43`). With decisions, it computes trajectory points from verified decisions and sets `current_iks` from the last point (`copilot_sdk/scoring/trajectory.py:45-72`).

Runtime inspection of `app.state.scorer.trajectory()` returned a `TrajectoryResult` dict with `points`, `current_iks`, `current_win_rate`, `decisions_total`, and `days_active`, confirming the live app scorer supports the SDK contract.

### Frontend expectations

The S2P frontend source path was found at `..\..\copilot-sdk\apps\s2p\frontend\src`. Search found active S2P calls for fingerprint and performance trajectory, but no active `fetchS2PIKS` or `/api/s2p/iks` API call. The S2P shell currently passes a literal `iks={0}` to `CopilotShell` (`copilot-sdk/apps/s2p/frontend/src/App.tsx:48-64`).

The active S2P trajectory component imports `fetchS2PTrajectory` (`copilot-sdk/apps/s2p/frontend/src/components/TrajectoryChart.tsx:3-11`), which calls `/api/s2p/performance/trajectory` (`copilot-sdk/apps/s2p/frontend/src/api.ts:243-244`). That response type expects `points`, `total_checkpoints`, `verified`, and `current_q` (`copilot-sdk/apps/s2p/frontend/src/types.ts:696-703`), and the backend performance endpoint returns exactly those keys from graph-store checkpoints and verified counts (`app/routers/s2p_performance.py:70-86`).

No frontend file was found that requires `/api/s2p/iks` to expose the raw SDK trajectory shape. Therefore the IKS implementation should preserve the existing backend response keys and use the SDK trajectory as an internal source.

### Required adapter contract

Do not return `TrajectoryResult` directly from `/api/s2p/iks`. Add a local adapter in `app/routers/s2p.py` that maps the SDK scorer state to the existing IKS response fields:

- `iks`: `trajectory.current_iks`.
- `decisions`: `trajectory.decisions_total`.
- `mean_drift`: either a conservative compatibility field such as `0.0` until SDK exposes centroid drift, or a value derived only if a trustworthy source exists.
- `d_max`: preserve existing compatibility value `0.20` unless a canonical SDK value is available.
- `domain`: `"s2p"`.
- `status`: derived from IKS and/or decision count with the same cold-start meaning as existing tests; e.g., `CALIBRATING` for zero decisions, then a documented GREEN/AMBER/RED or LOW/MODERATE/HIGH mapping.
- `learning_active`: true only when the active SDK scorer has verified/learned decisions or an equivalent learned-state signal. Do not set this to true merely because RL components exist; the legacy cold-start contract uses `learning_active=False` when no verified outcomes have been applied (`app/domains/s2p/scorer.py:86-99`), while SDK RL component presence is a separate wiring fact (`copilot_sdk/scoring/scorer.py:160-178`).
- `interpretation`: preserve a human-readable text field using a local helper.

This adapter protects current API consumers while letting `/api/s2p/iks` reflect the same scorer used by score and learn.

## 5. Implementation Plan

| File | Change | Why | Risk |
|---|---|---|---|
| `app/routers/s2p.py` | Change `get_iks` to accept `http_request: Request`, resolve `scorer = _sdk_scorer(http_request)`, call `scorer.trajectory()`, and return a compatibility IKS dict through a new local adapter. Remove the in-endpoint import of `app.domains.s2p.scorer.get_s2p_iks`. | This removes the production dual-scorer path while preserving the route path and response keys (`app/routers/s2p.py:905-923`). | Medium: existing tests assert legacy cold-start semantics and will need updates. |
| `app/routers/s2p.py` | Add small private helpers such as `_iks_from_trajectory`, `_interpret_sdk_iks`, and optionally `_trajectory_dict` using existing `_json_safe`. | The SDK trajectory shape differs from the legacy IKS response (`copilot_sdk/scoring/trajectory.py:17-24`), so an adapter is safer than returning the SDK dataclass directly. | Low: helper naming and exact status thresholds must be stable for tests. |
| `app/main.py` | Prefer no change. Keep `build_s2p_scorer` as the canonical construction path because it already uses the S2P preset, app-owned SQLiteGraphStore, decision prefix, and S2P reward function (`app/main.py:54-66`). | Avoids the no-arg `from_preset("s2p")` default DB failure and avoids unnecessary app initialization churn. | Low. |
| `app/domains/s2p/scorer.py` | Prefer no source change in first implementation. Leave legacy module for tests/demo compatibility, but ensure no production router calls it. Optionally add deprecation in a later cleanup prompt. | Tests still import `get_s2p_iks`, `reset_scorer`, and `score_event` directly (`tests/test_s2p_iks.py:15-20`, `tests/test_s2p_scorer.py:13-44`). | Low if left unchanged; removing it now would broaden scope. |
| `tests/test_s2p_iks.py` | Update endpoint tests to set `app.state.scorer = build_s2p_scorer(tmp_path or memory)`, call score/learn as needed, and assert `/api/s2p/iks` reflects SDK trajectory fields through compatibility keys. Move direct legacy `get_s2p_iks()` assertions to a separate legacy-only test or remove if no longer a product contract. | Current tests assert legacy cold-start and import legacy module directly (`tests/test_s2p_iks.py:15-80`). | Medium: must avoid persistent `app/data/s2p.db` state. |
| `tests/test_s2p_core_router.py` | Strengthen `test_core_status_endpoints_return_json_safe_dicts` to assert IKS compatibility keys and finite numeric values after the new adapter (`tests/test_s2p_core_router.py:192-197`). | Keeps router coverage aligned with the new endpoint behavior. | Low. |
| `tests/test_s2p_score_endpoint.py` or new focused test | Add score -> learn -> iks/trajectory assertions using one isolated app scorer instance. Also add a sentinel-scorer or monkeypatch test where `/api/s2p/iks` returns a deliberately unique `trajectory.current_iks` value from `app.state.scorer`. | Proves `/score`, `/learn`, and `/iks` read/write the same SDK scorer. Existing score/learn tests already use `app.state.scorer = build_s2p_scorer()` (`tests/test_s2p_score_endpoint.py:41-46`). A score-only test is insufficient because SDK trajectory is computed from verified decisions, not unverified score records (`copilot_sdk/scoring/scorer.py:418-438`). | Medium: test isolation must restore app state or rebuild scorer per test. |
| `tests/test_s2p_scorer.py`, `tests/test_domain_isolation.py`, `tests/test_s2p_demo.py` | Do not update unless implementation removes legacy public functions. If retained, mark them as legacy module tests and keep them out of production-route assertions. | Legacy direct imports remain test/demo compatibility, not production routing. | Low. |

Implementation details:

1. In `get_iks`, change signature from `def get_iks() -> dict:` to `def get_iks(http_request: Request) -> dict[str, Any]:`.
2. Resolve `scorer = _sdk_scorer(http_request)`.
3. Call `trajectory = scorer.trajectory()`.
4. Convert dataclass fields safely. `dataclasses.asdict` is already imported at the top of `app/routers/s2p.py` (`app/routers/s2p.py:9`), and `_json_safe` exists in the router for JSON cleanup.
5. Produce the compatibility response described in Section 4.
6. Optionally keep the Neo4j decision-count enrichment only if it cannot override SDK graph-store truth. The current endpoint can override `decisions` from Neo4j (`app/routers/s2p.py:914-921`); that should be removed or changed to a secondary field because it can desynchronize IKS from the SDK trajectory.

## 6. Tests / Validation Plan

Add or update focused tests:

1. `/api/s2p/iks` returns 200 and compatibility keys from the SDK scorer:
   - File: `tests/test_s2p_iks.py`.
   - Assert keys `iks`, `decisions`, `domain`, `status`, `learning_active`, and `interpretation`.
   - Assert finite JSON-safe numeric values.
2. `/api/s2p/iks` does not call legacy `get_s2p_iks`:
   - File: `tests/test_s2p_iks.py`.
   - Monkeypatch legacy `app.domains.s2p.scorer.get_s2p_iks` to raise, call endpoint, expect 200. This directly proves the production route no longer depends on the legacy function.
3. `/api/s2p/score`, `/api/learn`, and `/api/s2p/iks` use the same scorer state:
   - File: `tests/test_s2p_iks.py` or `tests/test_s2p_score_endpoint.py`.
   - Install an isolated `app.state.scorer = build_s2p_scorer()` and keep `app.state.graph_store = app.state.scorer.graph_store`.
   - Score an event, learn the returned `decision_id`, then verify IKS sees the same graph-store verified state through compatible `decisions`.
   - Do not assert IKS increments after score alone. `CompoundingScorer.trajectory()` is built from `get_verified_decisions(...)`, so unverified score records are not enough evidence of IKS movement (`copilot_sdk/scoring/scorer.py:418-438`).
4. Score -> learn -> IKS/trajectory flow:
   - File: `tests/test_s2p_score_endpoint.py` or `tests/test_s2p_iks.py`.
   - Use `/api/s2p/score`, then `/api/learn` with the returned `decision_id`, then `/api/s2p/iks`.
   - Assert IKS has a valid range and `decisions` reflects verified decisions from the active graph store.
5. RL components are present on `app.state.scorer`:
   - File: `tests/test_s2p_score_endpoint.py` or a small new focused test.
   - Assert `_reward_fn`, `_credit`, and `_explorer` are present after `build_s2p_scorer()`. The app currently wires `S2PRewardFunction` and SDK fills credit/explorer (`app/main.py:54-60`, `copilot_sdk/scoring/scorer.py:160-178`).
   - Keep the assertion to component presence and wiring. Do not claim that IKS/trajectory itself proves exploration policy quality or credit-assignment correctness; those behaviors are separate parts of the SDK learn path (`copilot_sdk/scoring/scorer.py:355-367`).
6. Legacy scorer has no production router caller:
   - Prefer behavioral test from item 2 instead of brittle source-string assertions.
7. Sentinel scorer routing test:
   - File: `tests/test_s2p_iks.py`.
   - Temporarily install an object on `app.state.scorer` with a `trajectory()` method returning a unique JSON-safe `current_iks`, plus the minimal graph-store fields needed by the endpoint.
   - Assert `/api/s2p/iks` returns the sentinel value. This test fails against the old endpoint because the old endpoint ignores `app.state.scorer` and calls legacy `get_s2p_iks()` (`app/routers/s2p.py:905-923`).
8. Existing `tests/test_s2p_scorer.py` can remain as legacy-module coverage if the module is retained.
9. Existing regression tests pass.

Exact validation commands for the implementation prompt:

```powershell
python -m pytest tests/test_s2p_iks.py -v --timeout=120
python -m pytest tests/test_s2p_score_endpoint.py tests/test_s2p_core_router.py -v --timeout=120
python -m pytest tests/ -q --timeout=120
```

Do not run these in this planning prompt. They are for implementation validation.

## 7. Architecture Guardrails

- No SDK changes. The SDK already has S2P scoring and RL preset registry entries (`copilot_sdk/scoring/presets/__init__.py:8-13`, `copilot_sdk/rl/presets.py:16-33`).
- No frontend changes. No active S2P frontend `/api/s2p/iks` consumer was found; the S2P shell currently passes `iks={0}` (`copilot-sdk/apps/s2p/frontend/src/App.tsx:48-64`).
- Do not replace `build_s2p_scorer` with a bare `CompoundingScorer.from_preset("s2p")` call because the no-arg path failed in this environment and the app-owned graph store is already the correct persistence boundary (`app/main.py:54-60`, `copilot_sdk/scoring/scorer.py:142-150`).
- Do not return SDK `TrajectoryResult` directly from `/api/s2p/iks`; adapt it to the current IKS response keys.
- Do not let Neo4j decision-count enrichment override SDK graph-store decision counts, because score/learn persistence and trajectory are now anchored on `app.state.scorer.graph_store`.
- Avoid broad changes to other `s2p_*.py` routers. `s2p_performance` already reads from app graph-store state for trajectory (`app/routers/s2p_performance.py:70-86`) and is outside the immediate dual-scorer bug.
- Keep legacy `app/domains/s2p/scorer.py` until production routing and tests no longer rely on it. Removing the module now would expand the blast radius because tests still import it directly.
- Preserve test isolation by rebuilding `app.state.scorer`, `app.state.graph_store`, and `app.state.s2p_reward_function` in tests that mutate score/learn state, matching existing fixture patterns (`tests/test_s2p_core_router.py:61-63`, `tests/test_s2p_score_endpoint.py:41-46`).
- When a test replaces `app.state.scorer`, avoid asserting behavior of routers that captured the startup scorer at include time. The conservation router uses a dynamic lambda over `app.state.scorer.graph_store` (`app/main.py:74-80`), but the transfer router is created with the startup scorer object (`app/main.py:82`). This implementation scope should not change or test transfer routes.
- If tests need S2P evolution state, reset it explicitly. `app.state.s2p_evolution` is initialized from the startup scorer (`app/main.py:66`), while existing score endpoint tests also call `reset_s2p_evolver()` in their scorer reset helper (`tests/test_s2p_score_endpoint.py:40-46`).

## 8. Scope Repair / Blockers

Scope repair:

- The implementation request should say “same `build_s2p_scorer` / `CompoundingScorer.from_preset("s2p", graph_store=...)` instance,” not “bare `CompoundingScorer.from_preset("s2p")`,” because a no-arg runtime construction failed to open the default SDK data DB in this checkout. The app already uses the safe graph-store path (`app/main.py:54-60`).
- The IKS endpoint should be unified by adapter, not by returning `scorer.trajectory()` raw, because the existing API contract is an IKS dict while SDK trajectory is a dataclass with different fields (`app/domains/s2p/scorer.py:77-120`, `copilot_sdk/scoring/trajectory.py:17-24`).
- Legacy module cleanup should be deferred unless the implementation prompt explicitly includes updating/removing legacy tests and demo imports.

Blockers: none for a narrow implementation.

Deferred work:

- Decide whether `app/domains/s2p/scorer.py` should be removed, deprecated, or left as legacy demo/test support after production routes no longer call it.
- Decide whether S2P frontend should display live IKS in `CopilotShell`; current source passes a literal zero (`copilot-sdk/apps/s2p/frontend/src/App.tsx:48-64`), so that is a separate frontend scope.
- Decide whether `/api/s2p/performance/trajectory` should use SDK `scorer.trajectory()` instead of raw checkpoint rows. It currently returns checkpoint rows and verified counts (`app/routers/s2p_performance.py:70-86`), which is adjacent but not required to fix `/api/s2p/iks`.

## 9. Prompt C Implementation Inputs

Approved files for implementation:

- `app/routers/s2p.py`
- `tests/test_s2p_iks.py`
- `tests/test_s2p_core_router.py`
- `tests/test_s2p_score_endpoint.py`
- Optional only if implementation chooses to formalize legacy coverage: `tests/test_s2p_scorer.py`, `tests/test_domain_isolation.py`, `tests/test_s2p_demo.py`

Do not modify:

- `copilot_sdk/**`
- frontend files
- unrelated S2P routers
- config files

Exact endpoint/function changes:

- Change `app/routers/s2p.py:get_iks` to use `_sdk_scorer(http_request)` and `scorer.trajectory()`.
- Add a local adapter from SDK `TrajectoryResult` to the existing IKS response dict.
- Remove the production dependency on `app.domains.s2p.scorer.get_s2p_iks` from the endpoint.

Adapter contract:

```python
{
    "iks": <float from trajectory.current_iks>,
    "d_max": 0.20,
    "mean_drift": <float compatibility value>,
    "decisions": <int from trajectory.decisions_total>,
    "domain": "s2p",
    "status": <status derived from decisions/iks>,
    "learning_active": <bool derived from SDK scorer state>,
    "interpretation": <human readable string>,
}
```

Tests to run:

```powershell
python -m pytest tests/test_s2p_iks.py -v --timeout=120
python -m pytest tests/test_s2p_score_endpoint.py tests/test_s2p_core_router.py -v --timeout=120
python -m pytest tests/ -q --timeout=120
```

Known risks:

- Existing tests currently assert legacy cold-start `learning_active is False` and may need precise replacement semantics (`tests/test_s2p_iks.py:57-80`).
- The default persistent app DB already contained verified trajectory state during runtime inspection; tests must isolate `app.state.scorer` to avoid depending on local data.
- `mean_drift` and `d_max` are legacy IKS fields; if product wants them to be mathematically meaningful under SDK trajectory, that requires a separate formula decision.

## 10. How This Implementation Could Create a Fixer

- Returning the raw SDK `TrajectoryResult` from `/api/s2p/iks` would break the existing IKS dict contract. The implementation must adapt `current_iks` and `decisions_total` into the existing response keys (`app/domains/s2p/scorer.py:77-120`, `copilot_sdk/scoring/trajectory.py:17-24`).
- Treating score-only records as IKS movement would create brittle tests. SDK trajectory uses verified decisions, so the meaningful behavior test is score -> learn -> IKS (`copilot_sdk/scoring/scorer.py:314-319`, `copilot_sdk/scoring/scorer.py:418-438`).
- Leaving the Neo4j enrichment as an override can desynchronize `decisions` from the SDK graph store. If kept, it should be a secondary field, not the source of the IKS decision count (`app/routers/s2p.py:914-921`).
- Setting `learning_active` based only on RL component presence would change cold-start semantics incorrectly. Use verified learned state for that field and test RL wiring separately (`app/domains/s2p/scorer.py:86-99`, `copilot_sdk/scoring/scorer.py:160-178`).
- Removing `app/domains/s2p/scorer.py` in the same implementation would break direct legacy tests and demo imports. The production fix only needs to stop `/api/s2p/iks` from calling it (`tests/test_s2p_iks.py:15-20`, `tests/test_s2p_scorer.py:13-44`, `tests/test_s2p_demo.py:10-20`).
- Replacing `app.state.scorer` in tests without also updating `app.state.graph_store` and `app.state.s2p_reward_function` can create false failures in score/learn code paths that read those app-state aliases (`app/main.py:62-66`, `tests/test_s2p_core_router.py:60-63`).
- Broadening into `/api/s2p/performance/trajectory` or frontend `CopilotShell` live IKS would expand scope beyond the dual-scorer fix. Those are documented deferred decisions, not blockers for unifying `/api/s2p/iks`.

READY_FOR_IMPLEMENTATION: YES
