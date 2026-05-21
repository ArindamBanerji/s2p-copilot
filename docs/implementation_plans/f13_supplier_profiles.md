# F13 SupplierProfileAccumulator Architecture Plan

## 1. Executive Summary

Classification: PLAN_READY.

This is a plan-only document for a future `SupplierProfileAccumulator`. It does not implement accumulator code, endpoints, frontend, tests, storage, score/learn metadata changes, or fixture changes.

Current state:
- S2P already has supplier and invoice fixtures. Supplier fixture rows include `supplier_id`, `name`, `category`, `exception_rate`, `avg_invoice_amount`, `payment_terms`, `otif_score`, `total_invoices`, `total_exceptions`, and `recent_trend` (`data/s2p_demo_suppliers.json:2-13`). Invoice fixture rows include `supplier_id`, `supplier_name`, `amount`, `category`, factor values, and `metadata.invoice_date`/`metadata.due_date` (`data/synthetic_invoices.json:2-22`).
- The normal `/api/s2p/score` path writes supplier identity into decision metadata through `_invoice_decision_metadata()` (`backend/app/routers/s2p.py:82-91`) and persists it via `scorer.score(..., metadata=...)` (`backend/app/routers/s2p.py:385-391`).
- The learn/outcome flow can recover the stored decision and context after scoring (`backend/app/routers/s2p.py:472-483`, `backend/app/routers/s2p.py:527-547`).
- Current supplier endpoints are fixture-derived and deterministic, not accumulated from verified outcomes (`backend/app/routers/s2p_suppliers.py:54-66`, `backend/app/routers/s2p_suppliers.py:97-156`).

Target state:
- Add an S2P-local `SupplierProfileAccumulator` that consumes verified decisions after learn/outcome succeeds, updates per-supplier profiles in memory, and uses fixture supplier profiles as cold start.
- Keep `ProfileScorer` centroid learning, conservation, factor computers, and current triage flow unchanged.
- Expose supplier profile APIs that blend fixture cold start fields with computed exception rate, trend, categories, invoice counts, and history.

Recommended first implementation scope:
1. Add a small metadata prerequisite so scored decisions carry `invoice_date`, `due_date`, `po_number`, and any fixture date fields needed for seasonality.
2. Implement an in-memory accumulator and unit tests.
3. Hook `/api/learn` and `/api/s2p/outcome` after scorer learning succeeds.
4. Replace or extend supplier endpoints to read accumulator-backed profiles with fixture fallback.
5. Wire the Suppliers screen to the live profile responses.

P0 prerequisites:
- Add date metadata to `_invoice_decision_metadata()`. Invoice fixtures have `metadata.invoice_date` and `metadata.due_date` (`data/synthetic_invoices.json:20-22`), but `_invoice_decision_metadata()` currently stores only `invoice_id`, `source_invoice_id`, `supplier_id`, `supplier_name`, and `amount` (`backend/app/routers/s2p.py:82-91`).
- The normal score path has `supplier_id`, but the direct `/api/s2p/outcome` fallback decision writer does not store supplier metadata when it has to reconstruct a missing decision (`backend/app/routers/s2p.py:230-256`). First implementation should skip such decisions gracefully or add optional supplier/date fields to `OutcomeRequest`.

## 2. Current Architecture

Supplier fixture shape:
- Supplier rows include `supplier_id`, `name`, `category`, `exception_rate`, `avg_invoice_amount`, `payment_terms`, `otif_score`, `total_invoices`, `total_exceptions`, and `recent_trend` (`data/s2p_demo_suppliers.json:2-13`).

Invoice fixture shape:
- Invoice rows include `invoice_id`, `supplier_id`, `supplier_name`, `po_number`, `amount`, `currency`, `category`, `ground_truth_action`, factor values, and `metadata` (`data/synthetic_invoices.json:2-20`).
- Date fields are nested under metadata as `invoice_date` and `due_date` (`data/synthetic_invoices.json:20-22`).

Score endpoint:
- `ScoreRequest` accepts `event_id`, `category`, `amount`, `supplier_id`, optional `contract_id`, and the seven factor fields (`backend/app/routers/s2p.py:309-327`).
- The score endpoint loads a fixture invoice with `find_invoice(request.event_id)` and builds an invoice object (`backend/app/routers/s2p.py:378-379`).
- It computes factors, then calls `scorer.score(..., metadata=_invoice_decision_metadata(invoice))` (`backend/app/routers/s2p.py:381-391`).
- `ScoreResponse` is additive and includes `active_variant`, but it does not include supplier/date fields directly (`backend/app/routers/s2p.py:330-341`).

Learn/verify/outcome endpoints:
- `LearnRequest` includes `decision_id`, `action`, `outcome`, optional `context`, `reason_code`, and `variant_id` (`backend/app/routers/s2p.py:451-457`).
- `/api/learn` gets the stored decision, runs `_learn_with_scorer`, and then records PromptVariantEvolver outcome if `variant_id` is present (`backend/app/routers/s2p.py:472-497`).
- `OutcomeRequest` includes decision/action/reward fields and optional `variant_id`, but not supplier/date fields (`backend/app/routers/s2p.py:428-440`).
- `/api/s2p/outcome` writes the learning outcome through `_learn_with_scorer` and then records PromptVariantEvolver outcome if `variant_id` is present (`backend/app/routers/s2p.py:527-566`).

Decision metadata:
- `_invoice_decision_metadata()` writes `invoice_id`, `source_invoice_id`, `supplier_id`, `supplier_name`, and `amount` (`backend/app/routers/s2p.py:82-91`).
- It currently does not write `invoice_date`, `due_date`, `po_number`, fixture `metadata`, category, or `ground_truth_action` (`backend/app/routers/s2p.py:82-91`).
- `CompoundingScorer.score()` merges caller metadata into decision metadata and adds SDK fields such as `decision_id`, `domain`, `category_index`, `factor_vector`, `recommended_index`, `probabilities`, and `created_at` before calling `write_decision` (`copilot_sdk/scoring/scorer.py:201-220`).

GraphStore storage:
- The SDK GraphStore protocol supports `write_decision`, `write_outcome`, `get_decision`, and `get_decisions` (`copilot_sdk/graph/protocol.py:12-39`).
- `InMemoryGraphStore.write_decision()` deep-copies metadata into the stored decision (`copilot_sdk/graph/memory_store.py:28-55`).
- `InMemoryGraphStore.write_outcome()` stores outcome metadata and `get_verified_decisions()` merges decision and outcome rows with `outcome_metadata` (`copilot_sdk/graph/memory_store.py:58-73`, `copilot_sdk/graph/memory_store.py:91-105`).

Existing supplier endpoints:
- `/api/s2p/suppliers` returns fixture-backed summaries (`backend/app/routers/s2p_suppliers.py:54-66`, `backend/app/routers/s2p_suppliers.py:97-101`).
- `/api/s2p/suppliers/{supplier_id}/profile` returns deterministic fixture/profile data, including synthetic trends and recent fixture invoices (`backend/app/routers/s2p_suppliers.py:133-156`).
- `/api/s2p/suppliers/{supplier_id}/heatmap` returns fixture/category rates (`backend/app/routers/s2p_suppliers.py:159-197`).
- Preview suppliers are also fixture-backed from `s2p_demo_suppliers.json` (`backend/app/routers/s2p_preview.py:260-296`, `backend/app/routers/s2p_preview.py:398-412`).

Frontend Suppliers screen:
- The frontend has `fetchSuppliers()`, `fetchSupplierProfile()`, `fetchSupplierHeatmap()`, and `fetchSupplierClustering()` API helpers (`apps/s2p/frontend/src/api.ts:255-268`).
- `SupplierProfile` currently models fixture fields such as `supplier_id`, `name`, `exception_rate`, `avg_invoice_amount`, `payment_terms`, `otif_score`, and total counts (`apps/s2p/frontend/src/types.ts:71-90`).
- `SuppliersScreen` fetches `/api/s2p/suppliers`, auto-selects the first supplier, displays OTIF and exception rate, and renders `SupplierProfileCard` and `SupplierHeatmap` (`apps/s2p/frontend/src/screens/SuppliersScreen.tsx:30-56`, `apps/s2p/frontend/src/screens/SuppliersScreen.tsx:70-109`).

Preseed interaction:
- `seed_s2p_graph()` reads invoice and supplier fixtures and creates graph nodes/edges (`backend/app/seed_graph.py:45-52`, `backend/app/seed_graph.py:111-160`).
- It emits decision and invoice graph nodes directly from fixtures (`backend/app/seed_graph.py:163-226`); it does not call `/api/s2p/score` or `/api/learn`.

Lock/context bridge:
- Tests assert the old context bridge and `threading.Lock` are absent from `app/routers/s2p.py` (`backend/tests/test_context_bridge_removed.py:40-52`).
- The learn test verifies context now flows through stored decision/outcome metadata (`backend/tests/test_context_bridge_removed.py:55-93`).

## 3. Supplier ID Flow - P0 Chain

| Source | Evidence | Field names | Present? | Risk | Required fix if missing |
|---|---|---|---|---|---|
| Supplier fixture | `data/s2p_demo_suppliers.json:2-13` | `supplier_id`, `name`, `exception_rate`, `otif_score` | YES | Fixture fields are cold-start only, not learned | Preserve fixture fields as baseline |
| Invoice fixture | `data/synthetic_invoices.json:2-22` | `supplier_id`, `supplier_name`, `metadata.invoice_date`, `metadata.due_date` | YES | Dates are nested under metadata | Copy dates into decision metadata at score time |
| Score request/invoice lookup | `backend/app/routers/s2p.py:378-379` | `event_id` finds fixture invoice | YES | Non-fixture requests only have request fields | Add optional date fields later only if needed for live data |
| Decision metadata at score time | `backend/app/routers/s2p.py:82-91` | `supplier_id`, `supplier_name`, `amount` | PARTIAL | No invoice dates | P0: include `invoice_date`, `due_date`, `po_number`, and possibly raw fixture metadata |
| GraphStore decision storage | `copilot_sdk/scoring/scorer.py:201-220`; `copilot_sdk/graph/memory_store.py:41-55` | metadata persisted | YES | None for normal score path | No GraphStore change needed |
| Learn reads decision | `backend/app/routers/s2p.py:472-483` | stored decision | YES | Accumulator hook must run after scorer learn succeeds | Add S2P router hook post-learn |
| Outcome reads/reconstructs decision | `backend/app/routers/s2p.py:527-547`; `backend/app/routers/s2p.py:230-256` | stored or fallback decision | PARTIAL | Fallback decision has no supplier/date metadata | Skip missing supplier, or add supplier/date fields to `OutcomeRequest` |
| Outcome context | `backend/app/routers/s2p.py:123-169`; test `backend/tests/test_context_bridge_removed.py:83-93` | supplier and amount copied into learn context | YES on normal scored decisions | Context lacks invoice date unless metadata is fixed | Same metadata prerequisite |

Explicit answers:
- Does invoice fixture have `supplier_id`? YES (`data/synthetic_invoices.json:2-5`).
- Does score response include supplier info? NO. `ScoreResponse` fields do not include supplier fields (`backend/app/routers/s2p.py:330-341`).
- Does decision metadata store `supplier_id`? YES on the normal score path (`backend/app/routers/s2p.py:82-91`).
- Does learn/outcome recover stored metadata? YES through `get_decision()` and `_decision_context()` (`backend/app/routers/s2p.py:123-169`, `backend/app/routers/s2p.py:472-483`, `backend/app/routers/s2p.py:527-547`).
- Can accumulator hook read `supplier_id` after learn? YES for score-originated decisions, because the router already has the stored decision before learning and `_decision_context()` surfaces supplier fields (`backend/app/routers/s2p.py:123-169`, `backend/tests/test_context_bridge_removed.py:83-93`).
- Can accumulator hook compute seasonality from decision metadata today? NO, because invoice dates are present in fixtures but not copied into decision metadata (`data/synthetic_invoices.json:20-22`, `backend/app/routers/s2p.py:82-91`).

## 4. SupplierProfile Data Model

Target dataclass:

```python
@dataclass
class SupplierProfile:
    supplier_id: str
    supplier_name: str
    exception_rate: float
    exception_rate_trend: float | None
    otif: float | None
    otif_by_quarter: dict[str, float]
    avg_lead_time_days: float | None
    lead_time_by_quarter: dict[str, float]
    invoice_count: int
    last_invoice_date: str | None
    pricing_trend: float | None
    categories: list[str]
    last_updated: str
```

Semantics:
- `exception_rate_trend`, `pricing_trend`, `otif`, and lead-time fields are `None` when source data is insufficient. They must not default to `0.0`, because zero implies measured flat/no-risk behavior.
- Fixture fields remain available during cold start. Supplier fixtures already provide exception and OTIF baselines (`data/s2p_demo_suppliers.json:2-13`).
- Computed fields replace fixture fields only after the configured verified-decision threshold.
- `last_updated` is the accumulator update time, not the invoice date.

## 5. Accumulation Strategy

Option A - S2P router hook after learn/outcome:
- Call `accumulator.on_decision_verified(decision, outcome_payload)` after `_learn_with_scorer()` succeeds in `/api/learn` and `/api/s2p/outcome`.
- Evidence: `/api/learn` already gets the decision and calls `_learn_with_scorer()` (`backend/app/routers/s2p.py:472-483`). `/api/s2p/outcome` gets/creates the decision, calls `_learn_with_scorer()`, and then performs post-learning side effects (`backend/app/routers/s2p.py:527-566`).
- This is S2P-specific and avoids SDK changes.
- Recommended first implementation.

Option B - CompoundingScorer post-learn callback:
- Reusable but invasive. `CompoundingScorer.learn()` owns centroid update, conservation pause, outcome write, and checkpoint bookkeeping (`copilot_sdk/scoring/scorer.py:233-320`). Adding callbacks there would affect SDK-wide behavior.
- Not first implementation.

Option C - GraphStore outcome event/trigger:
- Heaviest option. The current protocol stores decisions/outcomes but has no supplier-profile event concept (`copilot_sdk/graph/protocol.py:12-39`).
- Not first implementation.

Recommendation:
- Use Option A.
- Hook after scorer learn/outcome succeeds.
- Do not block or alter reward/centroid learning.
- If `supplier_id` is missing, skip gracefully and record a debug/metric counter.
- Because tests verify no context bridge or `threading.Lock` in the S2P router (`backend/tests/test_context_bridge_removed.py:40-52`), there is no lock-release bridge that the accumulator must wait on. Keep the hook simple and synchronous for first implementation.

## 6. Metrics and Computation

Storage window:
- Keep a trailing window of 200 verified invoices per supplier.
- Store raw normalized events internally: supplier id/name, invoice id, invoice date, amount, category, recommended action, actual action, reward, is_correct, factor values, and outcome context.

`exception_rate`:
- Define as the fraction of verified decisions in the supplier window that are exceptions.
- For current live fields, exception should be true when:
  - `is_correct` is false from `CompoundingScorer.learn()` outcome metadata (`copilot_sdk/graph/memory_store.py:91-105`), or
  - outcome context indicates an override path, or
  - actual action differs from recommended action as computed in `CompoundingScorer.learn()` (`copilot_sdk/scoring/scorer.py:247-250`).
- Use the existing reward/outcome path only as an input, not as a replacement for scorer learning.

`exception_rate_trend`:
- Linear regression over a trailing 90-day window.
- Minimum 10 verified data points.
- Return `None` below minimum.
- Requires `invoice_date`; current score metadata must be fixed before trend is fully supported.

`categories`:
- Distinct exception categories observed in verified decision events.
- Categories should use live S2P category names from `S2PDomainConfig.categories` (`backend/app/domains/s2p/config.py:20-27`, `backend/app/domains/s2p/config.py:98-113`).

`pricing_trend`:
- Based on invoice `amount` and `amount_variance_ratio` if available. Amount is already included in decision metadata for the normal score path (`backend/app/routers/s2p.py:82-91`), and amount variance exists as a canonical factor (`backend/app/domains/s2p/config.py:38-47`).
- Return `None` if no amount/time series is available.

Lead time:
- Compute from invoice/PO/receipt dates only when real date pairs exist.
- Current invoice fixtures expose invoice and due dates (`data/synthetic_invoices.json:20-22`), but no goods-receipt delivery timestamp is present in the read fixture rows.
- Until PO/GR dates are available, keep `avg_lead_time_days` and `lead_time_by_quarter` fixture/deferred or `None`.

Seasonal Q1-Q4:
- Use `invoice_date` when present.
- Do not use `created_at` as a silent substitute. `created_at` is a score-time timestamp from SDK scoring (`copilot_sdk/scoring/scorer.py:203-212`), not invoice event time.
- If `invoice_date` is absent after the P0 fix, return empty quarterly maps and mark the profile as insufficient data.

## 7. OTIF Feasibility Decision

Options:
- Option A: compute OTIF from decision/factor data.
- Option B: hybrid fixture OTIF plus computed exception rate/trend.
- Option C: defer OTIF until GR/PO connector.

Recommendation: Option B for F13 first implementation.

Evidence:
- Supplier fixture provides `otif_score` (`data/s2p_demo_suppliers.json:2-13`).
- Invoice fixture rows read include invoice/due dates but not actual receipt/delivery timestamps (`data/synthetic_invoices.json:20-36`).
- Factor code can detect graph neighbors labeled GoodsReceipt for match status (`backend/app/domains/s2p/factors.py:135-143`), but the fixture evidence does not provide verified delivery timestamps needed for OTIF.

Decision:
- Display fixture-backed OTIF during F13.
- Compute exception rate and exception-rate trend from verified decisions.
- Mark `otif` as fixture-backed until a GR connector or fixture adds actual receipt/delivery dates.

## 8. Cold Start and Transition

Cold start:
- Use fixture supplier profiles until at least 20 verified decisions exist for a supplier.
- Existing supplier fixtures contain enough baseline fields for display (`data/s2p_demo_suppliers.json:2-13`).

Transition:
- At `verified_count >= 20`, computed `exception_rate` replaces fixture `exception_rate`.
- Fixture `otif_score` may remain the OTIF source until delivery data is available.
- `last_updated` is set when the accumulator processes a verified decision.

Reset:
- Reset clears accumulated event windows and computed metrics.
- Fixture profiles remain available after reset.
- Tests already use reset patterns for module-level S2P services (`backend/tests/test_s2p_score_endpoint.py:39-44`, `backend/tests/test_s2p_evolver.py:15-19`); F13 tests should do the same.

## 9. Storage Strategy

F13-IMPL first:
- In-memory dictionary keyed by `supplier_id`.
- Reason: current app already uses `InMemoryGraphStore` for the S2P scorer in app construction (`backend/app/main.py:42-54`), and supplier endpoints are demo/fixture-oriented.
- No SQLite schema migration in first implementation.

F13-PERSIST future:
- Add SQLite persistence table for supplier profile events and materialized profiles.
- Keep migration idempotent.

F13-GRAPH future:
- Add supplier profile nodes or event edges to a graph backend only after API semantics stabilize.
- Do not change SDK GraphStore for first implementation.

## 10. API Endpoint Design

Reuse or extend existing `/api/s2p/suppliers` router. Current router prefix is `/api/s2p/suppliers` (`backend/app/routers/s2p_suppliers.py:15`), and it is mounted in app main (`backend/app/main.py:78`).

Route-order constraint:
- Keep static collection routes such as `/clustering` and future `/declining` before any dynamic supplier-id route. The live router defines `/clustering` before dynamic `/{supplier_id}/profile` and `/{supplier_id}/heatmap` (`backend/app/routers/s2p_suppliers.py:104-160`), and tests already protect `/clustering` from being captured as a supplier id (`backend/tests/test_s2p_suppliers.py:101-105`).
- Prefer preserving existing `/{supplier_id}/profile` and `/{supplier_id}/heatmap` paths for compatibility. Add a concise `/{supplier_id}` alias only if static route ordering is tested.

`GET /api/s2p/suppliers`
- Response:
  ```json
  {
    "suppliers": [SupplierProfileSummary],
    "total": 10,
    "source": "accumulator_with_fixture_fallback"
  }
  ```
- Include fixture cold-start rows when no computed data exists.
- Preserve current `supplier_id`, `name`, `otif_score`, `exception_rate`, `invoice_count`, `category_distribution`, and `trend_direction` compatibility fields where possible because tests assert these fields (`backend/tests/test_s2p_suppliers.py:32-43`).

`GET /api/s2p/suppliers/{id}`
- Optional concise profile endpoint, or alias to existing `/{supplier_id}/profile`.
- Return the target `SupplierProfile` fields.
- Unknown id returns 404, matching existing supplier profile behavior (`backend/tests/test_s2p_suppliers.py:62-66`).

`GET /api/s2p/suppliers/declining`
- Return suppliers with positive/worsening `exception_rate_trend`.
- Suppliers below trend minimum should be omitted or returned with `trend: null` only if explicitly requested.

`GET /api/s2p/suppliers/{id}/history`
- Return normalized verified decision events for the supplier.
- Cap by default, for example last 200.
- Unknown id returns 404 if neither fixture nor accumulator knows the supplier.

Trend/insufficient data:
- Trend fields are `null` until minimum data exists.
- Quarterly maps are empty when invoice dates are unavailable.

## 11. Frontend Integration

Suppliers screen plan:
- Keep the existing `SuppliersScreen` placement and selected supplier flow (`apps/s2p/frontend/src/screens/SuppliersScreen.tsx:30-56`).
- Update types to include the target `SupplierProfile` fields in addition to current fixture fields (`apps/s2p/frontend/src/types.ts:71-90`).
- Wire list/profile/history endpoints through existing API patterns (`apps/s2p/frontend/src/api.ts:255-268`).

Supplier card:
- Display name, OTIF badge, exception rate, trend arrow, and lead time.
- Show fixture-backed OTIF with a subtle source label until computed OTIF is available.

Seasonal chart:
- Q1-Q4 OTIF or lead-time map if present.
- Empty state when quarterly data is empty.

Declining supplier indicator:
- Highlight suppliers where `exception_rate_trend > 0`.
- Use `None`/null trend as insufficient data, not stable.

UI states:
- Loading, error, and empty states are required. Current Suppliers screen already handles loading and empty list states (`apps/s2p/frontend/src/screens/SuppliersScreen.tsx:74-78`).
- Do not run E2E/Playwright unless live stack is confirmed.

## 12. Preseed Interaction

Recommended behavior:
- Preseed should flow through the accumulator only if it uses score+learn/outcome.

Current evidence:
- `seed_s2p_graph()` reads fixtures and emits deterministic graph nodes and edges directly (`backend/app/seed_graph.py:111-226`).
- It does not call `/api/s2p/score` or `/api/learn`.

Plan:
- F13 first implementation should not assume preseed populates accumulator state.
- Future preseed can either:
  - call score+learn so the router hook processes events, or
  - call `SupplierProfileAccumulator.seed_from_fixtures()` explicitly.
- Accumulator reset must be available for tests.

## 13. Downstream Scenarios

S6 - expertise survives turnover:
- Required fields: supplier id/name, categories, exception rate, history, last updated.
- F13 supports partially/fully after verified decisions accumulate.

S7 - supplier consolidation:
- Required fields: supplier behavioral vectors, category distribution, exception rate, invoice count, OTIF.
- F13 supports partially with fixture OTIF and computed exception rate.

S8 - lead time seasonality:
- Required fields: invoice/receipt dates, lead time by quarter.
- F13 supports only partially after the invoice-date metadata P0; full lead-time requires GR/receipt dates.

S11 - "fine until it wasn't" trend detection:
- Required fields: exception-rate trend over event time.
- F13 supports after invoice dates are written into decision metadata and at least 10 supplier events exist.

S12 - working capital / pricing/payment optimization:
- Required fields: pricing trend, payment terms, amount series, payment timing.
- F13 supports partially using amount and fixture payment terms; full support requires richer payment timing data.

## 14. What Does NOT Change

- `ProfileScorer` and centroid learning remain unchanged.
- Conservation law remains unchanged.
- Factor computers remain unchanged for first implementation.
- Score/learn reward semantics remain unchanged.
- Evidence templates remain unchanged.
- Triage flow remains unchanged except for a post-learn supplier-profile side effect.
- No SDK GraphStore changes in first implementation.
- No SDK source/test changes in first implementation.

## 15. Test Plan

Accumulator core:
- `on_decision_verified` updates `invoice_count`.
- Updates `exception_rate`.
- Trailing window caps at 200.
- Trend computed with at least 10 data points.
- Trend is `None` below 10.
- Fixture cold start returns supplier fixture fields.
- Threshold transition after 20 decisions.
- Multiple suppliers remain independent.
- Reset clears accumulated data.
- Reset preserves fixture cold-start.

Temporal:
- Q1-Q4 derived from invoice dates.
- Missing `invoice_date` produces empty quarterly data in F13 first implementation.
- Tests must prove no `created_at` or wall-clock proxy is used for invoice seasonality.
- Lead time remains `None` without date pairs.
- OTIF uses hybrid/deferred behavior.

API:
- Suppliers list returns profiles.
- Supplier by id returns target shape.
- Declining suppliers returns worsening trend rows.
- History returns supplier events.
- Unknown id returns 404.

Integration:
- `/api/learn` hook fires after scorer learning succeeds.
- `/api/s2p/outcome` hook fires after scorer learning succeeds.
- Missing `supplier_id` skips gracefully.
- Preseed interaction is explicit and resettable.

Frontend:
- Suppliers screen renders live profiles.
- OTIF and exception rate displayed.
- Declining visual indicator works.
- Loading/error/empty states covered.

## 16. Implementation Sequence

Prompt 1: Metadata prerequisite.
- Add `invoice_date`, `due_date`, `po_number`, and maybe raw fixture metadata to `_invoice_decision_metadata()`.
- Add optional supplier/date fields to `OutcomeRequest` only if direct outcome-created decisions must be accumulated.
- Tests verify stored decision metadata contains supplier and date fields.

Prompt 2: Accumulator core.
- Add S2P backend accumulator module.
- Load supplier fixtures for cold start.
- Implement event normalization, trailing windows, computed profile fields, reset, and unit tests.

Prompt 3: Router hook and API endpoints.
- Hook `/api/learn` and `/api/s2p/outcome` after `_learn_with_scorer()`.
- Extend supplier endpoints with accumulator-backed profiles, declining list, and history.
- Preserve static route ordering so `/clustering` and `/declining` are not captured by dynamic supplier-id routes.
- Backend tests.

Prompt 4: Frontend Suppliers screen.
- Update API/types and render live computed fields with fixture fallback labels.
- Typecheck/build only unless live stack confirmed.

Prompt 5: GPT-5.5 review.
- Line-by-line architecture review for metadata, accumulator, router hooks, APIs, and UI wiring.

## 17. Risks and Mitigations

- Supplier id missing from direct outcome-created decisions: skip gracefully in first implementation, or add optional supplier fields to `OutcomeRequest`.
- Invoice date missing from decision metadata: P0 metadata prerequisite before trends/seasonality.
- Lock/context bridge blocking: tests indicate the bridge and `threading.Lock` are absent from S2P router (`backend/tests/test_context_bridge_removed.py:40-52`).
- OTIF not computable: use fixture OTIF and label source until GR/receipt dates exist.
- Preseed bypass: document current direct graph seeding and add explicit accumulator seed or score+learn preseed later.
- In-memory loss on restart: acceptable for demo first slice; plan SQLite later.
- Sparse trend data: return `None`, not zero.
- Fixture/computed blending confusion: include source flags or threshold labels.
- Stale accumulator across tests: provide reset and use fixtures like existing S2P evolver tests (`backend/tests/test_s2p_evolver.py:15-19`).
- Dynamic supplier route collisions: keep static routes before dynamic `/{supplier_id}` routes and add regression tests for `/clustering` and `/declining` (`backend/app/routers/s2p_suppliers.py:104-160`, `backend/tests/test_s2p_suppliers.py:101-105`).

## 18. Files to Modify in Future Implementation

S2P backend:
- `backend/app/routers/s2p.py` for metadata prerequisite and post-learn hook.
- New `backend/app/services/supplier_profile_accumulator.py`.
- `backend/app/routers/s2p_suppliers.py` for accumulator-backed supplier endpoints.
- Backend tests under `backend/tests/`.

S2P data:
- None for first implementation unless test fixtures need an added explicit date/delivery field in a later prompt.

SDK frontend:
- `apps/s2p/frontend/src/api.ts`.
- `apps/s2p/frontend/src/types.ts`.
- `apps/s2p/frontend/src/screens/SuppliersScreen.tsx`.
- Existing supplier components if reused.

Forbidden in first implementation:
- `ProfileScorer`.
- Conservation code.
- SDK GraphStore/protocol.
- External repos.
- Factor computers, unless a future prompt explicitly scopes optional lead-time/OTIF support.

## 19. Reading Log

S2P:
- `CLAUDE.md:1-49` - repo rules and S2P isolation.
- `data/s2p_demo_suppliers.json:1-122` - supplier fixture fields.
- `data/synthetic_invoices.json:1-260` - invoice fixture supplier/date/amount/category/factor fields.
- `backend/app/domains/s2p/config.py:1-198` - categories, actions, factors, reason codes, preset config.
- `backend/app/domains/s2p/reward.py:1-38` - reward amount/recovery behavior.
- `backend/app/domains/s2p/factors.py:1-337` - event shape, seven factor computers, supplier history factor, GR/PO context behavior.
- `backend/app/routers/s2p.py:1-653` - score, learn, outcome, metadata and context chain.
- `backend/app/routers/s2p_preview.py:1-493` - fixture-backed preview suppliers.
- `backend/app/routers/s2p_suppliers.py:1-197` - current supplier endpoints.
- `backend/app/routers/s2p_data_helpers.py:1-35` - fixture loading helpers.
- `backend/app/main.py:1-84` - scorer setup and router mounts.
- `backend/app/seed_graph.py:1-260` - direct fixture graph seeding.
- `backend/tests/test_context_bridge_removed.py:1-93` - context bridge removal and supplier metadata in outcome context.
- `backend/tests/test_s2p_score_endpoint.py:1-621` - score/learn/outcome test patterns.
- `backend/tests/test_s2p_evolver.py:1-161` - reset fixture pattern.
- `backend/tests/test_s2p_evolution_router.py:1-94` - endpoint test pattern.
- `backend/tests/test_s2p_suppliers.py:1-127` - supplier endpoint expectations.

SDK:
- `CLAUDE.md:1-120` - SDK graphify/public API rules.
- `graphify-out/GRAPH_REPORT.md:1-120` - graphify report per SDK CLAUDE.
- `copilot_sdk/graph/protocol.py:1-89` - GraphStore protocol.
- `copilot_sdk/graph/memory_store.py:1-120` - metadata/outcome storage behavior.
- `copilot_sdk/scoring/scorer.py:1-35`, `copilot_sdk/scoring/scorer.py:180-320` - score/learn writes decisions/outcomes and centroid learning boundary.
- `apps/s2p/frontend/src/api.ts:1-35`, `apps/s2p/frontend/src/api.ts:250-268` - supplier API helpers.
- `apps/s2p/frontend/src/types.ts:1-90`, `apps/s2p/frontend/src/types.ts:120-135` - S2P/supplier frontend types.
- `apps/s2p/frontend/src/screens/SuppliersScreen.tsx:1-113` - current Suppliers screen.

## Prompt Verification Pass

1. Supplier id flow documented: YES.
2. Invoice date flow documented: YES, with P0 metadata prerequisite.
3. Hook placement recommended: YES, router hook after learn/outcome.
4. OTIF feasibility decision made: YES, hybrid fixture OTIF plus computed exception metrics.
5. Cold-start fixture path clear: YES.
6. Storage strategy staged: YES, in-memory first, SQLite/GraphStore later.
7. Preseed decision made: YES, current seed bypasses score+learn.
8. Downstream scenarios mapped: YES.
9. No source/test files changed: YES, this prompt only writes this plan document.
