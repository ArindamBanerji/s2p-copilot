# S2P-CT+PVG+SUP Implementation Plan

## 1. Executive Summary

This plan adds three S2P backend feature routers, corresponding S2P frontend surfaces, and E2E specs without touching shared SDK internals, DataOps, Trading, Purchasing, SOC, GAE, ci-platform, or generated demo repos.

Planned backend additions in `s2p-copilot`:

- `app/routers/s2p_control_tower.py`
- `app/routers/s2p_pvg.py`
- `app/routers/s2p_suppliers.py`
- router imports/includes in `app/main.py`
- behavioral tests for control tower, PVG, suppliers, and cross-feature integration

Planned frontend/E2E additions in `copilot-sdk`:

- S2P-only API/type/component/screen updates under `apps/s2p/frontend/src`
- S2P-only E2E specs under `e2e/s2p`

Implementation should be split into backend and frontend/E2E prompts with GPT-5.5 review after each implementation stage.

## 2. Repos and Scope

Implementation repos:

- Backend: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
- Frontend/E2E: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk`

Reference-only data/source:

- DataOps Celonis fixture: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\apps\dataops\backend\data\celonis_process_data.json`

Out of scope / forbidden:

- `gen-ai-roi-demo-v4-v50`
- `graph-attention-engine-v50`
- `ci-platform`
- SOC code
- DataOps, Trading, Purchasing app code
- source/test/frontend/config writes in Prompt 0

Repo guidance:

- `s2p-copilot/CLAUDE.md:41-49` requires S2P domain isolation from SOC and confirms S2P shape is 5 categories, 5 actions, 7 factors with penalty ratio 5.0.
- `s2p-copilot/CLAUDE.md:55-57` forbids direct git use and states Windows Python 3.11 asyncio rules.
- `copilot-sdk/CLAUDE.md:39-47` keeps SDK public interfaces domain-neutral and forbids S2P/SOC domain imports in SDK internals; planned changes are under the S2P app only.
- `copilot-sdk/CLAUDE.md:62-70` asks agents to inspect graphify for architecture questions; `graphify-out/GRAPH_REPORT.md:49-59` identifies current SDK architecture hubs but does not alter this S2P app-scoped plan.

## 3. Backend Current State Evidence

Router mount pattern:

- `s2p-copilot/backend/app/main.py:14-19` imports existing routers from `app.routers`.
- `s2p-copilot/backend/app/main.py:102-115` includes `/api/learn`, `/api/conservation/status` via SDK router with prefix `/api`, framework router, S2P score router, insight/evidence/performance routers, and preview router.
- `s2p-copilot/backend/app/main.py:91-94` creates one FastAPI app and stores `app.state.scorer`, `app.state.graph_store`, and the S2P reward function.

Existing S2P route prefixes:

- `s2p.py` uses `APIRouter(prefix="/api/s2p")` and `learn_router = APIRouter(prefix="/api")` at `s2p-copilot/backend/app/routers/s2p.py:23-24`.
- Existing score endpoint is `POST /api/s2p/score` at `s2p-copilot/backend/app/routers/s2p.py:312-316`.
- Existing SDK-shaped learn endpoint is `POST /api/learn` at `s2p-copilot/backend/app/routers/s2p.py:419-421`.
- Preview router uses `/api/s2p/preview` at `s2p-copilot/backend/app/routers/s2p_preview.py:16`.
- Insight router uses `/api/s2p/insight` at `s2p-copilot/backend/app/routers/s2p_insight.py:16`.
- Evidence router uses `/api/s2p/evidence` at `s2p-copilot/backend/app/routers/s2p_evidence.py:14`.
- Performance router uses `/api/s2p/performance` at `s2p-copilot/backend/app/routers/s2p_performance.py:9`.

Existing helper/data patterns:

- `s2p.py` resolves fixture data from repo-level `data` with `DATA_DIR = Path(__file__).resolve().parents[3] / "data"` at `s2p-copilot/backend/app/routers/s2p.py:26` and `_load_synthetic_invoices()` at `s2p.py:30-37`.
- `s2p.py` resolves invoices by `invoice_id` or `event_id` at `s2p.py:39-47`.
- `s2p_preview.py` uses `_repo_root()`, `_data_path()`, and `_load_json_fixture()` at `s2p_preview.py:27-37`.
- `s2p_preview.py` has `_load_celonis_cache()` that checks `$CLAUDE_SDK` and local `data/celonis_process_data.json` at `s2p_preview.py:40-55`; Prompt 0 should not rely on environment variables, so new implementation should use local repo data plus safe sibling-path lookup or return `available:false`.
- `s2p_insight.py` already has local `_load_invoices()`, `_find_invoice()`, `_load_suppliers()`, and `_load_celonis()` helpers at `s2p_insight.py:35-63`.
- `s2p_evidence.py` has local `_load_invoices()` and graph-store access helper at `s2p_evidence.py:25-39`.
- `s2p_performance.py` accesses `graph_store` from `app.state.graph_store` or `app.state.scorer.graph_store` at `s2p_performance.py:15-21`.
- New routers should keep small fixture/process helpers local or duplicate the same minimal pathlib/json helper shape independently. Do not import helper functions from another router module unless the imported helper is already stable and one-way; router-to-router helper imports make route modules easier to couple accidentally and can create circular import risk when `main.py` imports every router.

Scoring separation:

- Control Tower must not call the scorer. Existing scoring is centralized in `score_procurement_event`, which computes factors then calls `scorer.score(...)` at `s2p.py:346-357`.
- Existing factor computation is available via `compute_all_factors()` at `s2p-copilot/backend/app/domains/s2p/factors.py:315-329`.

Route-collision analysis:

- Existing backend search found only preview supplier routes under `/api/s2p/preview/suppliers` and no current `/api/s2p/suppliers/{supplier_id}` route.
- New suppliers router should use `APIRouter(prefix="/api/s2p/suppliers")`.
- Declare `@router.get("/clustering")` before `@router.get("/{supplier_id}/profile")` and `@router.get("/{supplier_id}/heatmap")` to ensure the static route is not shadowed by dynamic `supplier_id` matching.

## 4. Frontend Current State Evidence

API/fetch style:

- `apps/s2p/frontend/src/api.ts:22` defines `API_URL = import.meta.env.VITE_API_URL || "http://localhost:8002"`.
- `apiGet` and `apiPost` wrap fetch and throw on non-OK responses at `api.ts:24-42`.
- Public helpers catch failures and return safe fallbacks/null, for example `getPreviewQueue()` at `api.ts:44-50`, `getPreviewSuppliers()` at `api.ts:61-65`, and `scoreInvoice()`/`learnDecision()` at `api.ts:101-107`.

Current S2P screens/tabs:

- `apps/s2p/frontend/src/App.tsx:10-18` defines tabs: Dashboard, Exception Triage, Insight, Evidence, Suppliers, Performance.
- `App.tsx:21-27` sets amber S2P theme variables.
- `DashboardScreen.tsx:12-39` fetches preview queue and conservation state and uses recent preview exceptions.
- `InsightScreen.tsx:17-74` uses preview queue invoice selection and existing insight panels.
- `PerformanceScreen.tsx:9-43` renders trajectory, conservation, what-if, and operational summary.
- `SuppliersScreen.tsx:19-83` currently uses preview suppliers only and renders a simple supplier card grid.

Existing components:

- `apps/s2p/frontend/src/components` currently contains S2P panels including `CrossGraphInsightCard.tsx`, `OperationalSummary.tsx`, `TrajectoryChart.tsx`, `WhatIfSimulator.tsx`, and process/reasoning panels.
- Recharts is already installed at `apps/s2p/frontend/package.json:11-14` and used by `TrajectoryChart.tsx:2` with `ResponsiveContainer` and `LineChart`.
- Existing component style uses `copilot-card`, amber text, and safe empty states, e.g. `CrossGraphInsightCard.tsx:27-45`, `OperationalSummary.tsx:27-43`, `TrajectoryChart.tsx:25-43`.

Current E2E pattern:

- `e2e/helpers/ui.ts:3-17` provides `clickTab`.
- `e2e/helpers/ui.ts:19-46` provides robust `expectAnyText`.
- Existing S2P E2E specs live under `e2e/s2p` and use the tab labels from `App.tsx`, for example `dashboard.spec.ts:36-43` and `flows.spec.ts:4-11`.

## 5. Fixture/Data Shape Summary

Domain config:

- S2P categories are `price_variance`, `quantity_mismatch`, `duplicate_risk`, `contract_gap`, `format_compliance` at `s2p-copilot/backend/app/domains/s2p/config.py:20-27`.
- S2P actions are `auto_approve`, `hold_for_review`, `escalate_to_buyer`, `flag_leakage`, `refer_to_specialist` at `config.py:29-36`.
- S2P factors are `match_status`, `amount_variance_ratio`, `duplicate_score`, `supplier_exception_history`, `payment_terms_impact`, `commodity_index_correlation`, `tax_regulatory_compliance` at `config.py:38-47`.
- S2P penalty ratio is 5.0 via `PENALTY_RATIO` at `config.py:69-74`.

Invoice fixture:

- Repo-level `s2p-copilot/data/synthetic_invoices.json` has 50 invoices.
- Example invoice fields include `invoice_id`, `supplier_id`, `supplier_name`, `po_number`, `amount`, `currency`, `category`, `ground_truth_action`, `factors`, and `metadata` at `s2p-copilot/data/synthetic_invoices.json:1-40`.
- Factor fields present in fixtures include `match_status`, `amount_variance_ratio`, `duplicate_score`, `supplier_exception_history`, `payment_terms_impact`, `commodity_index_correlation`, and `tax_regulatory_compliance` at `synthetic_invoices.json:11-18`.
- Metadata includes invoice/due dates, line items, commodity, and contract reference at `synthetic_invoices.json:20-39`.

Supplier fixture:

- Repo-level `s2p-copilot/data/s2p_demo_suppliers.json` has 10 suppliers.
- Supplier fields include `supplier_id`, `name`, `category`, `exception_rate`, `avg_invoice_amount`, `payment_terms`, `otif_score`, `total_invoices`, `total_exceptions`, and `recent_trend` at `s2p_demo_suppliers.json:1-13`.

Graph contract:

- S2P graph contract defines procurement/process nodes such as `Invoice`, `Supplier`, `PurchaseOrder`, `GoodsReceipt`, `Contract`, `Commodity`, `ProcessModel`, `ProcessVariant`, and `Activity` at `s2p-copilot/backend/app/graph_contract.py:11-77`.
- Edge types include invoice/supplier and process edges at `graph_contract.py:78-88`.

Process/Celonis data:

- `s2p-copilot/data/celonis_process_data.json` is missing in the current repo.
- Reference-only DataOps Celonis fixture includes `process_model`, `variant`, `activities`, `cross_graph_insights`, `recommendations`, and `compounding_trajectory`.
- Reference activities include a bottleneck `Match Invoice to GR` with `avg_duration_hours: 42.0`, `system: billing_api`, and `bottleneck_cause: MATKL_V2`.

## 6. Endpoint Contract Specification

### Control Tower router

File: `s2p-copilot/backend/app/routers/s2p_control_tower.py`

Prefix: `/api/s2p/control-tower`

`GET /intents`

Response:

```json
{
  "intents": [
    {
      "intent_id": "invoice_price_variance",
      "label": "Invoice price variance",
      "primary_categories": ["price_variance"],
      "signals": ["amount_variance_ratio"],
      "default_priority_weight": 1.0
    }
  ],
  "count": 5,
  "source": "s2p_domain_config"
}
```

Required intent IDs:

- `invoice_price_variance`
- `invoice_match_failure`
- `invoice_duplicate_risk`
- `contract_compliance_gap`
- `format_compliance_issue`

`GET /classify?invoice_id=X&category=Y`

Response:

```json
{
  "invoice_id": "S2P-INV-0001",
  "category": "contract_gap",
  "intent_id": "contract_compliance_gap",
  "intent_label": "Contract compliance gap",
  "priority": 192.22,
  "amount": 22426.73,
  "max_factor": 0.859,
  "dominant_factor": "payment_terms_impact",
  "source": "category_mapping"
}
```

Behavior:

- `invoice_id` is required; find the invoice by `invoice_id` or `event_id`.
- If the supplied invoice id is not found, return HTTP 404 with a clear `detail` instead of fabricating a classification. This keeps Control Tower classifications invoice-specific and prevents frontend screens from showing a real-looking priority for missing data.
- `category` is optional and should override the invoice category only when it is one of `S2PDomainConfig.categories`; invalid categories should return HTTP 422 or HTTP 400 consistently with FastAPI validation style.
- Primary mapping:
  - `price_variance` -> `invoice_price_variance`
  - `quantity_mismatch` -> `invoice_match_failure`
  - `duplicate_risk` -> `invoice_duplicate_risk`
  - `contract_gap` -> `contract_compliance_gap`
  - `format_compliance` -> `format_compliance_issue`
- Fallback inference:
  - `duplicate_score > 0.7` -> `invoice_duplicate_risk`
  - `amount_variance_ratio > 0.3` -> `invoice_price_variance`
  - `match_status > 0.65` -> `invoice_match_failure`
  - `tax_regulatory_compliance > 0.65` or `payment_terms_impact > 0.7` -> `contract_compliance_gap`
  - otherwise use category mapping or `format_compliance_issue`.
- Priority formula: `amount * max_factor * 0.01`.
- This endpoint must not call `CompoundingScorer.score()` or `scorer.score(...)`.

`GET /queue?limit=20`

Response:

```json
{
  "total": 50,
  "showing": 20,
  "items": [
    {
      "invoice_id": "S2P-INV-0002",
      "supplier_id": "SUP-002",
      "supplier_name": "Pacifica Logistics",
      "category": "quantity_mismatch",
      "intent_id": "invoice_match_failure",
      "priority": 56.27,
      "amount": 9752.58,
      "dominant_factor": "tax_regulatory_compliance",
      "factors": {}
    }
  ],
  "source": "synthetic_invoices.json"
}
```

Behavior:

- Clamp `limit` to a useful range, e.g. `1..100`.
- Classify every invoice.
- Sort descending by `priority`.
- Return no scorer decision IDs; Control Tower classifies/prioritizes only.

### PVG router

File: `s2p-copilot/backend/app/routers/s2p_pvg.py`

Prefix: `/api/s2p/pvg`

Constants:

- `ANNUAL_BASELINE_USD = 680000`
- impact breakdown: leakage prevented 45%, cycle time saved 30%, auto approve efficiency 25%

`GET /variants`

Response:

```json
{
  "variants": [
    {
      "variant_id": "pvg-leakage-guard",
      "name": "Leakage guard",
      "status": "shadow",
      "target": "flag_leakage",
      "estimated_annual_impact_usd": 306000
    }
  ],
  "count": 3,
  "source": "fixture"
}
```

`GET /impact?period=monthly|quarterly|annual`

Response:

```json
{
  "period": "annual",
  "baseline_usd": 680000,
  "total_impact_usd": 680000,
  "breakdown": {
    "leakage_prevented": 306000,
    "cycle_time_saved": 204000,
    "auto_approve_efficiency": 170000
  }
}
```

Behavior:

- `monthly` scales annual baseline by `1/12`.
- `quarterly` scales annual baseline by `1/4`.
- `annual` uses full baseline.
- Invalid `period` should return 422 via `Query(pattern=...)` or equivalent validation.

`GET /leakage`

Response:

```json
{
  "total": 50,
  "flagged_count": 7,
  "estimated_leakage_usd": 12345.67,
  "items": [
    {
      "invoice_id": "S2P-INV-0002",
      "supplier_id": "SUP-002",
      "amount": 9752.58,
      "amount_variance_ratio": 0.577,
      "commodity_index_correlation": 0.316,
      "at_risk_usd": 5627.24
    }
  ],
  "rule": "amount_variance_ratio > 0.15 and commodity_index_correlation < 0.5"
}
```

Leakage rule:

- `amount_variance_ratio > 0.15`
- `commodity_index_correlation < 0.5`

`GET /cycle-time`

Response when process data is available:

```json
{
  "available": true,
  "process_model": "Purchase-to-Pay",
  "variant": "Standard with Returns",
  "bottleneck_activity": "Match Invoice to GR",
  "duration_hours": 42.0,
  "duration_median_min": 2520.0,
  "cycle_time_saved_usd": 204000,
  "source": "celonis_cache"
}
```

Response when process data is unavailable:

```json
{
  "available": false,
  "activities": [],
  "source": null,
  "message": "No S2P process data available"
}
```

Implementation should prefer `s2p-copilot/data/celonis_process_data.json` if it appears later, then optionally a sibling `copilot-sdk/apps/dataops/backend/data/celonis_process_data.json` best-effort read. Resolve both paths from `Path(__file__).resolve()` rather than the current working directory. It must not require that reference file and must not modify DataOps.

### Suppliers router

File: `s2p-copilot/backend/app/routers/s2p_suppliers.py`

Prefix: `/api/s2p/suppliers`

Route order:

1. `GET /`
2. `GET /clustering`
3. `GET /{supplier_id}/profile`
4. `GET /{supplier_id}/heatmap`

`GET /`

Response:

```json
{
  "total": 10,
  "suppliers": [
    {
      "supplier_id": "SUP-001",
      "name": "Aster Industrial Chemicals",
      "category": "industrial chemicals",
      "exception_rate": 0.12,
      "avg_invoice_amount": 18450.0,
      "payment_terms": "Net 45",
      "otif_score": 0.88,
      "total_invoices": 1240,
      "total_exceptions": 149,
      "recent_trend": "declining",
      "fixture_invoice_count": 5,
      "fixture_exception_amount": 112133.65
    }
  ],
  "source": "s2p_demo_suppliers.json"
}
```

`GET /clustering`

Threshold-based clusters, not ML:

- `high_exception`: `exception_rate >= 0.10`
- `cycle_time_watch`: `recent_trend == "declining"` or `otif_score < 0.88`
- `stable`: otherwise

Response:

```json
{
  "clusters": [
    {
      "cluster_id": "high_exception",
      "label": "High exception suppliers",
      "supplier_count": 2,
      "suppliers": []
    }
  ],
  "total_suppliers": 10,
  "method": "threshold"
}
```

`GET /{supplier_id}/profile`

Response:

```json
{
  "supplier_id": "SUP-001",
  "name": "Aster Industrial Chemicals",
  "category": "industrial chemicals",
  "exception_rate": 0.12,
  "avg_invoice_amount": 18450.0,
  "payment_terms": "Net 45",
  "otif_score": 0.88,
  "total_invoices": 1240,
  "total_exceptions": 149,
  "recent_trend": "declining",
  "invoice_count": 5,
  "open_amount_usd": 112133.65,
  "top_categories": [{"category": "contract_gap", "count": 2}],
  "risk_level": "amber"
}
```

Unknown supplier:

- Prefer HTTP 404 with `detail: "Supplier <id> not found"`, matching FastAPI error conventions already used by `s2p.py` for invalid scoring/learning failures at `s2p.py:318-324` and `s2p.py:422-426`.

`GET /{supplier_id}/heatmap`

Response:

```json
{
  "supplier_id": "SUP-001",
  "factors": [
    {"factor": "match_status", "value": 0.62},
    {"factor": "amount_variance_ratio", "value": 0.28}
  ],
  "categories": [
    {"category": "contract_gap", "count": 2, "amount": 44853.46}
  ],
  "invoice_count": 5
}
```

## 7. Backend Implementation Plan

Backend files:

- `s2p-copilot/backend/app/routers/s2p_control_tower.py`
- `s2p-copilot/backend/app/routers/s2p_pvg.py`
- `s2p-copilot/backend/app/routers/s2p_suppliers.py`
- `s2p-copilot/backend/app/main.py`

Shared implementation guidance:

- Keep helpers local to new routers unless importing safe existing helpers is clearly simpler.
- Avoid importing new-router helpers from other new routers. If Control Tower, PVG, and Suppliers each need invoice/supplier loading, duplicate the small pathlib/json helper or keep it local to each router. Do not create a shared module in this prompt because it would broaden the allowed backend source surface.
- Use repo-level data root: `Path(__file__).resolve().parents[3] / "data"`, consistent with `s2p.py:26` and `s2p_preview.py:27-32`.
- Do not import or call scorer in Control Tower.
- Use `compute_all_factors()` for factor derivation, not hardcoded factor logic, where invoice data exists.
- Use only canonical S2P categories/actions/factors from `S2PDomainConfig`.
- No SOC imports or vocabulary.
- Keep process/Celonis helper safe: invalid/missing files return `{}` or `available:false`.

Mount plan:

- Add imports in `app/main.py` adjacent to existing S2P router imports at `main.py:14-19`.
- Include new routers adjacent to existing S2P feature routers at `main.py:111-115`.
- Target paths:
  - `/api/s2p/control-tower/*`
  - `/api/s2p/pvg/*`
  - `/api/s2p/suppliers/*`

Suggested mount order:

```python
app.include_router(s2p_router)
app.include_router(s2p_control_tower_router)
app.include_router(s2p_insight_router)
app.include_router(s2p_evidence_router)
app.include_router(s2p_performance_router)
app.include_router(s2p_pvg_router)
app.include_router(s2p_suppliers_router)
app.include_router(s2p_preview_router)
```

## 8. Backend Test Plan

Backend test files:

- `s2p-copilot/backend/tests/test_s2p_control_tower.py`
- `s2p-copilot/backend/tests/test_s2p_pvg.py`
- `s2p-copilot/backend/tests/test_s2p_suppliers.py`
- `s2p-copilot/backend/tests/test_s2p_ct_pvg_integration.py`

Control Tower tests:

- `test_intents_returns_five_s2p_intents`
- `test_classify_category_mapping_price_variance`
- `test_classify_duplicate_factor_override`
- `test_classify_amount_variance_factor_override`
- `test_classify_unknown_invoice_404_or_structured_error`
- `test_queue_sorts_descending_by_priority`
- `test_queue_limit_is_respected`
- `test_control_tower_does_not_call_compounding_scorer`

PVG tests:

- `test_variants_returns_fixture_variants`
- `test_impact_annual_breakdown_sums_to_baseline`
- `test_impact_monthly_and_quarterly_scale_baseline`
- `test_impact_invalid_period_422`
- `test_leakage_flags_only_variance_and_low_correlation`
- `test_leakage_estimated_amount_nonnegative`
- `test_cycle_time_available_when_process_data_present`
- `test_cycle_time_unavailable_when_process_data_missing`

Suppliers tests:

- `test_suppliers_list_returns_10_fixture_suppliers`
- `test_clustering_returns_threshold_clusters`
- `test_clustering_static_route_not_shadowed`
- `test_supplier_profile_known_supplier`
- `test_supplier_profile_unknown_supplier_404`
- `test_supplier_heatmap_known_supplier`
- `test_supplier_heatmap_unknown_supplier_404`
- `test_supplier_profile_cross_references_invoice_fixture`

Integration/no-SOC tests:

- all new endpoints return 200 for happy path and assert meaningful payload invariants, not just status codes
- Control Tower priority and PVG leakage agree on high-variance invoices
- PVG cycle-time missing process data does not fail
- no SOC imports in new routers via narrow source scan

Test quality rules:

- No `or True`.
- No stale hardcoded global pass counts.
- Avoid source-string-only tests except the narrow no-SOC import scan.
- Test route behavior and response fields directly through FastAPI `TestClient`.

GPT-5.5 review step after backend:

- Review only backend new routers, `main.py`, and changed backend tests.
- Verify route paths, no scorer use in Control Tower, PVG formulas, supplier route order, missing process data handling, and no SOC imports.

## 9. Frontend Implementation Plan

Frontend files:

- `copilot-sdk/apps/s2p/frontend/src/api.ts`
- `copilot-sdk/apps/s2p/frontend/src/types.ts`
- `copilot-sdk/apps/s2p/frontend/src/screens/DashboardScreen.tsx`
- `copilot-sdk/apps/s2p/frontend/src/screens/InsightScreen.tsx`
- `copilot-sdk/apps/s2p/frontend/src/screens/PerformanceScreen.tsx`
- `copilot-sdk/apps/s2p/frontend/src/screens/SuppliersScreen.tsx`
- new components:
  - `ControlTowerPanel.tsx`
  - `FinancialImpactCard.tsx`
  - `LeakageDetectionPanel.tsx`
  - `CycleTimePanel.tsx`
  - `SupplierProfileCard.tsx`
  - `SupplierHeatmap.tsx`
  - `SupplierClusteringPanel.tsx`

API helper additions:

- `fetchS2PControlTowerIntents()`
- `fetchS2PControlTowerClassify(invoiceId, category?)`
- `fetchS2PControlTowerQueue(limit = 20)`
- `fetchS2PPVGVariants()`
- `fetchS2PPVGImpact(period = "annual")`
- `fetchS2PPVGLeakage()`
- `fetchS2PPVGCycleTime()`
- `fetchS2PSuppliers()`
- `fetchS2PSupplierClustering()`
- `fetchS2PSupplierProfile(supplierId)`
- `fetchS2PSupplierHeatmap(supplierId)`

API behavior:

- Follow current `apiGet` style at `api.ts:24-30`.
- Return `null` or safe empty objects on failure, consistent with `api.ts:44-65` and `api.ts:120-170`.
- Use `/api/s2p/suppliers`, not current stale singular helper path `/api/s2p/supplier/{id}/profile` at `api.ts:116-118`.
- URL-encode all invoice and supplier IDs with `encodeURIComponent()` or `URLSearchParams`, matching the existing invoice helper style at `api.ts:120-139`.
- Frontend types should include both snake_case response fields and camelCase aliases where components normalize values. New component props should use normalized local variables so response-shape drift is isolated to API/types/screen glue.

Type additions:

- `ControlTowerIntent`
- `ControlTowerClassifyResponse`
- `ControlTowerQueueItem`
- `ControlTowerQueueResponse`
- `PVGVariant`
- `PVGImpactResponse`
- `PVGLeakageItem`
- `PVGLeakageResponse`
- `PVGCycleTimeResponse`
- `SupplierCluster`
- `SupplierClusteringResponse`
- `SupplierProfileResponse`
- `SupplierCategorySummary`
- `SupplierHeatmapFactor`
- `SupplierHeatmapCategory`
- `SupplierHeatmapResponse`

Screens:

- Dashboard: add `ControlTowerPanel`, `FinancialImpactCard`, and compact PVG/supplier signal summaries without removing existing dashboard cards.
- Insight: add/position Control Tower classification detail for selected invoice, while preserving current fingerprint/similar/cross-graph/process panels.
- Performance: add `FinancialImpactCard`, `LeakageDetectionPanel`, and `CycleTimePanel` near existing trajectory/conservation/what-if/summary.
- Suppliers: replace current preview-only shell with supplier clustering, supplier list selector, selected `SupplierProfileCard`, and `SupplierHeatmap`.

Component safety:

- Components must tolerate `null`, missing arrays, and failed API calls.
- Use array guards such as `Array.isArray(response?.items) ? response.items : []`.
- Keep amber S2P styling and existing `copilot-card` conventions.
- Recharts is allowed because `recharts` is in `package.json:11-14`, but use simple markup where charts would add unnecessary fragility.
- No SOC vocabulary.

GPT-5.5 review step after frontend/E2E:

- Review S2P-only API/types/components/screens/E2E.
- Verify response shapes match backend contracts, null handling, supplier screen selection, E2E robustness, and no forbidden app/shared SDK changes.

## 10. E2E Creation Plan

E2E files:

- `copilot-sdk/e2e/s2p/control_tower.spec.ts`
- `copilot-sdk/e2e/s2p/pvg.spec.ts`
- `copilot-sdk/e2e/s2p/suppliers.spec.ts`

Patterns:

- Use `clickTab` from `e2e/helpers/ui.ts:3-17`.
- Use `expectAnyText` from `e2e/helpers/ui.ts:19-46`.
- Use actual tab labels from `App.tsx:12-18`.
- Do not direct-call backend APIs.
- Do not run Playwright unless live backend/frontend stack is confirmed.

Control Tower E2E:

- Dashboard shows Control Tower queue/prioritization.
- Insight or Dashboard classification shows intents such as price variance, match failure, duplicate risk, contract gap.
- Queue sorting visible with priority language.

PVG E2E:

- Performance shows financial impact, annual target, leakage, cycle time.
- Leakage panel shows variance/correlation terms or empty state.
- Cycle-time panel shows Celonis bottleneck when process data is available, or graceful unavailable text.

Suppliers E2E:

- Suppliers tab shows clustering.
- Supplier list renders fixture suppliers.
- Selecting a supplier shows profile fields and heatmap/factor signals.
- Unknown supplier is not directly navigated in UI unless the UI exposes a safe empty/error state.

## 11. Validation Commands

Backend:

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; python -m pytest tests\test_s2p_control_tower.py -v --timeout=120
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; python -m pytest tests\test_s2p_pvg.py -v --timeout=120
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; python -m pytest tests\test_s2p_suppliers.py -v --timeout=120
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; python -m pytest tests\test_s2p_ct_pvg_integration.py -v --timeout=120
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; python -m pytest tests\ -q --timeout=120
```

No-SOC scan:

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend"; Select-String -Path app\routers\s2p_control_tower.py,app\routers\s2p_pvg.py,app\routers\s2p_suppliers.py -Pattern "from app\.domains\.soc|import soc|credential_access|lateral_movement|data_exfiltration"
```

Frontend:

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\apps\s2p\frontend"; npx tsc --noEmit
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\apps\s2p\frontend"; npm run build
```

E2E typecheck:

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\e2e"; npx tsc --noEmit
```

Do not run Playwright unless the live stack is explicitly confirmed.

Baseline results from Prompt 0:

- `s2p-copilot/backend`: `python -m pytest tests\ -q --timeout=120` -> 280 passed, 562 warnings. Warnings included pytest-freezegun deprecation warnings and pytest cache permission warnings writing under `s2p-copilot\.pytest_cache`.
- `copilot-sdk/apps/s2p/frontend`: `npx tsc --noEmit` -> passed with no output.
- `copilot-sdk/e2e`: `npx tsc --noEmit` -> passed with no output.

## 12. Risks / Blockers

- `s2p-copilot/data/celonis_process_data.json` is absent. PVG cycle-time must return `available:false` when no process fixture is found.
- Existing `_load_celonis_cache()` uses `$CLAUDE_SDK` at `s2p_preview.py:40-55`; new code should not rely on env vars because this plan requires absolute/sibling path behavior or graceful absence.
- Supplier route declaration order matters. `/clustering` must be declared before `/{supplier_id}/profile` and `/{supplier_id}/heatmap`.
- Existing `api.ts` has a stale singular supplier profile path at `api.ts:116-118`; frontend implementation must replace or avoid it.
- Control Tower must not accidentally call scorer. Tests should monkeypatch or fake scorer calls to catch this.
- PVG leakage uses fixture factors. If invoices are missing factor dicts, implementation should call `compute_all_factors()` and default safely.
- E2E specs are creation/typecheck only unless live services are confirmed.

## 13. Prompt Verification Pass Results

1. Referenced repos and target directories exist: YES. `s2p-copilot` and `copilot-sdk` exist. `backend/docs/implementation_plans` was absent and is safe to create for the allowed plan file.
2. Backend/frontend repo boundaries are correct: YES. Backend changes belong in `s2p-copilot`; frontend/E2E changes belong in S2P-only areas of `copilot-sdk`.
3. Existing router and screen patterns are understood: YES. Router imports/includes are in `main.py:14-19` and `main.py:102-115`; frontend tabs are in `App.tsx:12-18`.
4. Existing data fixture field names are known: YES. Invoice fields are documented from `synthetic_invoices.json:1-40`; supplier fields from `s2p_demo_suppliers.json:1-13`.
5. Plan avoids meaningless tests such as `or True`: YES.
6. Plan avoids old hardcoded pass-count assertions: YES.
7. Plan handles missing Celonis/process data gracefully: YES, PVG cycle-time returns `available:false`.
8. Supplier route ordering is safe: YES, `/clustering` before dynamic supplier routes.
9. Frontend components consume actual backend response shapes: YES, endpoint contracts define fields for each planned component.
10. GPT-5.5 review prompts are included after backend and frontend/E2E implementation stages: YES.

READY_FOR_IMPLEMENTATION: YES

## 14. Reading Log

S2P backend:

- `s2p-copilot/CLAUDE.md:1-62`
- `s2p-copilot/backend/app/main.py:1-120`
- `s2p-copilot/backend/app/routers/s2p.py:1-561`
- `s2p-copilot/backend/app/routers/s2p_preview.py:1-464`
- `s2p-copilot/backend/app/routers/s2p_insight.py:1-181`
- `s2p-copilot/backend/app/routers/s2p_evidence.py:1-183`
- `s2p-copilot/backend/app/routers/s2p_performance.py:1-137`
- `s2p-copilot/backend/app/domains/s2p/config.py:1-180`
- `s2p-copilot/backend/app/domains/s2p/factors.py:1-337`
- `s2p-copilot/backend/app/graph_contract.py:1-89`
- `s2p-copilot/data/synthetic_invoices.json:1-80`
- `s2p-copilot/data/s2p_demo_suppliers.json:1-80`

S2P frontend/E2E:

- `copilot-sdk/CLAUDE.md:1-70`
- `copilot-sdk/graphify-out/GRAPH_REPORT.md:1-100`
- `copilot-sdk/apps/s2p/frontend/src/api.ts:1-170`
- `copilot-sdk/apps/s2p/frontend/src/types.ts:1-399`
- `copilot-sdk/apps/s2p/frontend/src/App.tsx:1-65`
- `copilot-sdk/apps/s2p/frontend/src/screens/DashboardScreen.tsx:1-132`
- `copilot-sdk/apps/s2p/frontend/src/screens/InsightScreen.tsx:1-76`
- `copilot-sdk/apps/s2p/frontend/src/screens/PerformanceScreen.tsx:1-43`
- `copilot-sdk/apps/s2p/frontend/src/screens/SuppliersScreen.tsx:1-93`
- `copilot-sdk/apps/s2p/frontend/src/components/CrossGraphInsightCard.tsx:1-56`
- `copilot-sdk/apps/s2p/frontend/src/components/OperationalSummary.tsx:1-54`
- `copilot-sdk/apps/s2p/frontend/src/components/TrajectoryChart.tsx:1-45`
- `copilot-sdk/apps/s2p/frontend/package.json:11-21`
- `copilot-sdk/e2e/helpers/ui.ts:1-73`
- `copilot-sdk/e2e/s2p/dashboard.spec.ts:1-51`
- `copilot-sdk/e2e/s2p/flows.spec.ts:1-188`
- `copilot-sdk/e2e/s2p/insight.spec.ts:1-39`
- `copilot-sdk/e2e/s2p/performance.spec.ts:1-43`

Reference process fixture:

- `copilot-sdk/apps/dataops/backend/data/celonis_process_data.json`
