# G5+G6+G7 S2P Enhancement — Investigation Report
**Generated:** 2026-05-24 · **Repo:** s2p-copilot · **Baseline:** 701 passed

## Ready for Implementation
- G5 (Control Tower + PVG): PARTIAL — Control Tower and PVG routers are already mounted, but the exact `/api/s2p/financial-impact` endpoint is absent and fixture fields requested for risk/recovery/cycle time are absent from the primary invoice fixture.
- G6 (Suppliers): PARTIAL — Supplier preview, clusters, profiles, histories, per-supplier heatmap, early-warning, and payment behavior endpoints exist, but the requested aggregate `/suppliers/trends`, `/suppliers/heatmap`, and `/suppliers/correlations` paths are absent and requested supplier fixture fields are absent.
- G7 (Novelty): PARTIAL — A `NoveltyTracker` exists and `/api/s2p/novelty/status` plus `/history` are mounted, but `/rate`, `/auto-pause`, and `novelty_score` in `/api/s2p/score` are absent.

## Router Map
| Router file | Lines | Prefix | Endpoints | Mount | G5/G6/G7 relevance |
|---|---:|---|---|---|---|
| `app/routers/framework_router.py` | 777 | `APIRouter()` at `app/routers/framework_router.py:22` | Many `/soc/...` framework routes | Not included in `app/main.py:70-95` | Not target S2P enhancement |
| `app/routers/s2p.py` | 973 | `/api/s2p` at `app/routers/s2p.py:35` | POST `/score` at `app/routers/s2p.py:624`; GET `/auto-approve/stats` at `app/routers/s2p.py:723`; GET `/auto-approve/expansion-proof` at `app/routers/s2p.py:728`; POST `/outcome` at `app/routers/s2p.py:827`; GET `/iks` at `app/routers/s2p.py:890`; GET `/learning-gate` at `app/routers/s2p.py:911` | Mounted at `app/main.py:79` | G7 side-effect novelty recording occurs from score path, but response lacks `novelty_score` |
| `app/routers/s2p_clustering.py` | 221 | `/api/s2p/suppliers` at `app/routers/s2p_clustering.py:14` | GET `/clusters` at `app/routers/s2p_clustering.py:117`; GET `/similarity` at `app/routers/s2p_clustering.py:136` | Mounted at `app/main.py:91` | G6 supplier clusters exist |
| `app/routers/s2p_control_tower.py` | 173 | `/api/s2p/control-tower` at `app/routers/s2p_control_tower.py:13` | GET `/intents` at `app/routers/s2p_control_tower.py:117`; GET `/classify` at `app/routers/s2p_control_tower.py:126`; GET `/queue` at `app/routers/s2p_control_tower.py:147` | Mounted at `app/main.py:82` | G5 Control Tower exists |
| `app/routers/s2p_data_helpers.py` | 35 | None | Fixture helper functions, no router | Imported by routers | Shared data loading; `_DATA_DIR` points to repo `data` at `app/routers/s2p_data_helpers.py:9` |
| `app/routers/s2p_discovery.py` | 350 | `/api/s2p/discovery` | GET `/alerts`, `/disruptions`, `/extended`, `/supplier/{supplier_id}`, `/propagation/{discovery_id}` | Mounted at `app/main.py:83` | Adjacent, not G5/G6/G7 core |
| `app/routers/s2p_early_warning.py` | 233 | `/api/s2p/suppliers` at `app/routers/s2p_early_warning.py:13` | GET `/early-warnings` at `app/routers/s2p_early_warning.py:133`; GET `/trend-signals` at `app/routers/s2p_early_warning.py:147` | Mounted at `app/main.py:92` | G6 trend signals exist, but not exact `/suppliers/trends` |
| `app/routers/s2p_evidence.py` | 355 | `/api/s2p/evidence` at `app/routers/s2p_evidence.py:16` | Audit trail, receipts, integrity, audit pack, template, rules, compliance | Mounted at `app/main.py:86` | Not direct target |
| `app/routers/s2p_evolution.py` | 54 | `/api/s2p/evolution` | Rules, variants, promotion-check, reset, shadow-results, promoted | Mounted at `app/main.py:80` | Not direct target |
| `app/routers/s2p_explorer.py` | 255 | `/api/s2p/explorer` | Export, centroid, drift, weights, contribution endpoints | Mounted at `app/main.py:81` | Not direct target |
| `app/routers/s2p_governance.py` | 334 | `/api/s2p/governance` | Compliance, conservation, rationalization endpoints | Mounted at `app/main.py:87` | Adjacent supplier governance |
| `app/routers/s2p_insight.py` | 167 | `/api/s2p/insight` | Fingerprint, similar, cross-graph, process-signals | Mounted at `app/main.py:85` | Adjacent |
| `app/routers/s2p_novelty.py` | 25 | `/api/s2p/novelty` at `app/routers/s2p_novelty.py:10` | GET `/status` at `app/routers/s2p_novelty.py:14`; GET `/history` at `app/routers/s2p_novelty.py:19` | Mounted at `app/main.py:90` | G7 partially exists; `/rate` and `/auto-pause` absent |
| `app/routers/s2p_payment.py` | 256 | `/api/s2p/suppliers` | GET `/payment-strategy`; GET `/payment-behavior` | Mounted at `app/main.py:93` | G6 adjacent |
| `app/routers/s2p_performance.py` | 149 | `/api/s2p/performance` | Trajectory, what-if, summary | Mounted at `app/main.py:88` | Not direct target |
| `app/routers/s2p_preview.py` | 493 | `/api/s2p/preview` at `app/routers/s2p_preview.py:16` | GET `/queue` at `app/routers/s2p_preview.py:314`; GET `/conservation` at `app/routers/s2p_preview.py:344`; GET `/compounding` at `app/routers/s2p_preview.py:381`; GET `/suppliers` at `app/routers/s2p_preview.py:399`; GET `/config` at `app/routers/s2p_preview.py:416` | Mounted at `app/main.py:95` | G5/G6 preview source exists |
| `app/routers/s2p_pvg.py` | 209 | `/api/s2p/pvg` at `app/routers/s2p_pvg.py:14` | GET `/variants` at `app/routers/s2p_pvg.py:79`; GET `/impact` at `app/routers/s2p_pvg.py:120`; GET `/leakage` at `app/routers/s2p_pvg.py:138`; GET `/cycle-time` at `app/routers/s2p_pvg.py:173` | Mounted at `app/main.py:89` | G5 PVG exists; exact `/financial-impact` alias absent |
| `app/routers/s2p_simulation.py` | 248 | `/api/s2p/simulation` | Scenarios, scenario detail, what-if, impact-summary | Mounted at `app/main.py:84` | Adjacent |
| `app/routers/s2p_suppliers.py` | 256 | `/api/s2p/suppliers` at `app/routers/s2p_suppliers.py:17` | GET `""` and `/` at `app/routers/s2p_suppliers.py:155-157`; GET `/clustering` at `app/routers/s2p_suppliers.py:167`; GET `/declining` at `app/routers/s2p_suppliers.py:196`; GET `/{supplier_id}/profile` at `app/routers/s2p_suppliers.py:202`; GET `/{supplier_id}/history` at `app/routers/s2p_suppliers.py:210`; GET `/{supplier_id}/heatmap` at `app/routers/s2p_suppliers.py:219` | Mounted at `app/main.py:94` | G6 exists, but aggregate heatmap/correlations paths absent |

Direct answers:
- Control Tower router exists: yes, `app/routers/s2p_control_tower.py`, mounted at `app/main.py:82`.
- PVG router exists: yes, `app/routers/s2p_pvg.py`, mounted at `app/main.py:89`.
- Supplier analytics/suppliers routers exist: yes, split across `s2p_suppliers.py`, `s2p_clustering.py`, `s2p_early_warning.py`, and `s2p_payment.py`.
- Novelty router exists: yes, `app/routers/s2p_novelty.py`, mounted at `app/main.py:90`.
- Financial impact router exists: no exact `/api/s2p/financial-impact`; PVG impact exists at `/api/s2p/pvg/impact` in `app/routers/s2p_pvg.py:120`.
- Intent router exists: no class/service named `IntentRouter`; router-level intent classification exists in `app/routers/s2p_control_tower.py`.

## Service Map
| Service file | Lines | Classes/functions | Data sources | G5/G6/G7 relevance |
|---|---:|---|---|---|
| `app/services/__init__.py` | 0 | None | None | None |
| `app/services/novelty_tracker.py` | 156 | `NoveltyEntry` at `app/services/novelty_tracker.py:58`; `NoveltyTracker` at `app/services/novelty_tracker.py:77`; singleton getter/reset at `app/services/novelty_tracker.py:149-153` | In-memory tracker; scorer centroids in `compute_nearest_distance` at `app/services/novelty_tracker.py:26` | G7 equivalent to NoveltyMonitor exists as `NoveltyTracker` |
| `app/services/ols_status.py` | 7 | Minimal status helper | Static/computed | Not target |
| `app/services/receipt_store.py` | 91 | `ReceiptStore` | In-memory receipt persistence | Not target |
| `app/services/s2p_evolver.py` | 93 | Variant/evolver helper functions | Prompt variant state | Not target |
| `app/services/s2p_learning_gate.py` | 126 | `S2PLearningGateResult`; `evaluate_s2p_learning_gate` | Decision metrics | Not target |
| `app/services/supplier_profile_accumulator.py` | 383 | `SupplierProfile` at `app/services/supplier_profile_accumulator.py:19`; `SupplierEvent` at `app/services/supplier_profile_accumulator.py:37`; `SupplierProfileAccumulator` at `app/services/supplier_profile_accumulator.py:52` | Repo fixture path in `_default_fixture_path` at `app/services/supplier_profile_accumulator.py:286-287`; verified decision updates | G6 supplier profile equivalent exists |
| `app/services/synthetic_invoices.py` | 185 | Synthetic invoice generation classes | Generated deterministic invoices | Fixture utility |

Named service existence:
- `IntentRouter`: absent by class/name search. Equivalent intent inference is router-level in `s2p_control_tower.py`.
- `FinancialImpactTracker`: absent by class/name search. Equivalent impact calculations are router-level under `s2p_pvg.py`.
- `ProcessVariantGraph`: absent by class/name search. Equivalent variant/cycle-time routes are in `s2p_pvg.py`.
- `SupplierAnalytics`: absent by class/name search. Equivalent behavior is distributed across supplier routers and `SupplierProfileAccumulator`.
- `NoveltyMonitor`: absent by class/name search. Equivalent tracker is `NoveltyTracker`.

## Fixture State
### Invoices
- Count: 50 in `data/synthetic_invoices.json`.
- Fields: `amount`, `category`, `currency`, `factors`, `ground_truth_action`, `invoice_id`, `metadata`, `po_number`, `supplier_id`, `supplier_name`.
- Loader path: `load_invoices()` loads `data/synthetic_invoices.json` through `_DATA_DIR` in `app/routers/s2p_data_helpers.py:9` and `app/routers/s2p_data_helpers.py:19-21`.
- Missing for G5: `intent`, `amount_at_risk`, `amount_recovered`, `cycle_time_hours`, and `verified` are not present in the first item.

### Suppliers
- Count: 10 in primary `data/s2p_demo_suppliers.json`.
- Fields: `avg_invoice_amount`, `category`, `exception_rate`, `name`, `otif_score`, `payment_terms`, `recent_trend`, `supplier_id`, `total_exceptions`, `total_invoices`.
- Alternate backend-local fixture: `backend/app/data/s2p_demo_suppliers.json`, count 10, fields include `exception_rate`, `financial_health_trend`, `format_compliance_pct`, `lead_time`, `otif`, `region`, `supplier_id`, `supplier_name`, `total_invoices_ytd`.
- Loader path: `load_suppliers()` uses the repo-level `data/s2p_demo_suppliers.json` via `app/routers/s2p_data_helpers.py:24-26`.
- Missing for G6: `quarterly_otif`, `behavioral_scores`, `category_exception_rates`, and `monthly_volume` are not present in the first item of either fixture.

## Test Inventory
- Baseline command from `backend`: `python -m pytest tests/ -q --timeout=120`.
- Baseline result: `701 passed, 1404 warnings in 12.58s`.
- `tests/test_s2p_control_tower.py`: 14 tests. Covers `/control-tower/intents`, intent field shape, category coverage, classify mapping, queue sorting/priority, invoice field preservation, no scorer calls, and no SOC imports; examples at `tests/test_s2p_control_tower.py:15-21`, `tests/test_s2p_control_tower.py:69-88`, and `tests/test_s2p_control_tower.py:116-138`.
- `tests/test_s2p_pvg.py`: 12 tests. Covers impact, variants, median cycle time, leakage, cycle-time endpoint, mounted endpoints, and no SOC imports; examples at `tests/test_s2p_pvg.py:16-20`, `tests/test_s2p_pvg.py:48-55`, `tests/test_s2p_pvg.py:103-127`, and `tests/test_s2p_pvg.py:130-142`.
- `tests/test_s2p_ct_pvg_integration.py`: 6 tests. Covers integration between Control Tower and PVG surfaces.
- `tests/test_novelty.py`: 19 tests. Covers novelty distance, tracker windowing, status/history endpoints, score side-effect recording, and explicitly asserts score response does not include novelty at `tests/test_novelty.py:191-201`.
- `tests/test_s2p_suppliers.py`: 19 tests. Covers supplier list, profile, history, declining suppliers, heatmap, and clustering routes.
- `tests/test_clustering.py`: 16 tests. Covers `/api/s2p/suppliers/clusters` and `/similarity`.
- `tests/test_early_warning.py`: 15 tests. Covers supplier early-warning and trend-signal endpoints.
- `tests/test_payment.py`: 20 tests. Covers supplier payment-strategy and payment-behavior endpoints.
- Target glob caveat: the requested shell pattern `tests/test_control_tower* tests/test_pvg* tests/test_supplier* tests/test_novelty* tests/test_financial* tests/test_intent*` only matched `test_novelty.py` and `test_supplier_accumulator.py` under PowerShell because actual S2P files include the `test_s2p_` prefix.

## Endpoint Smoke Results
| Path | Status | Top-level JSON keys or note |
|---|---:|---|
| `/api/s2p/preview/queue` | 200 | `auto_approve_rate`, `confidence_avg`, `engine_version`, `exceptions`, `invoices`, `scorer`, `showing`, `total` |
| `/api/s2p/preview/suppliers` | 200 | `engine_version`, `showing`, `source`, `suppliers`, `total` |
| `/api/s2p/preview/conservation` | 200 | `accuracy`, `auto_approve_pct`, `auto_approve_rate`, `computed_status`, `conservation_product`, `conservation_threshold`, `copilot`, `curve`, `engine_version`, `fixture_decisions`, `passed`, `penalty_ratio`, `source`, `status`, `verified_decisions` |
| `/api/s2p/control-tower/queue` | 200 | `items`, `queue`, `showing`, `source`, `total` |
| `/api/s2p/control-tower/intents` | 200 | `count`, `intents`, `source` |
| `/api/s2p/financial-impact` | 404 | Missing exact requested endpoint |
| `/api/s2p/pvg/variants` | 200 | `count`, `source`, `variants` |
| `/api/s2p/pvg/cycle-time` | 200 | `activities`, `available`, `bottleneck_activity`, `bottleneck_name`, `bottleneck_pct`, `process_model`, `source`, `total_median_minutes`, `variant` |
| `/api/s2p/suppliers/trends` | 404 | Missing exact requested endpoint |
| `/api/s2p/suppliers/clusters` | 200 | `clusters`, `consolidation_candidates`, `estimated_annual_savings`, `method`, `total_suppliers` |
| `/api/s2p/suppliers/heatmap` | 404 | Aggregate heatmap missing; per-supplier `/{supplier_id}/heatmap` exists |
| `/api/s2p/suppliers/correlations` | 404 | Missing exact requested endpoint |
| `/api/s2p/novelty/rate` | 404 | Missing exact requested endpoint |
| `/api/s2p/novelty/auto-pause` | 404 | Missing exact requested endpoint |
| `/api/s2p/novelty/history` | 200 | `alert_active`, `entries`, `total_in_window` |
- Requested score smoke payload with only `invoice_id` returned 422 because `ScoreRequest` requires `event_id`, `category`, `amount`, and `supplier_id` at `app/routers/s2p.py:587-591`.
- Valid score smoke request returned 200 with keys `action`, `action_index`, `active_variant`, `auto_approve`, `category`, `confidence`, `decision_id`, `event_id`, `factor_names`, `factor_vector`, `probabilities`, and `process_context`.
- `ScoreResponse` fields at `app/routers/s2p.py:608-620` do not include `novelty_score`.
- The score route records novelty as a side effect through `_record_score_novelty(...)` at `app/routers/s2p.py:704`, but the returned response at `app/routers/s2p.py:706-719` omits novelty.

## Gap Tables
### G5 Control Tower + PVG
| Requested Feature | Already Exists? | What's There | Remaining Gap |
|---|---|---|---|
| Control Tower queue | Yes | `/api/s2p/control-tower/queue` at `app/routers/s2p_control_tower.py:147` | No greenfield work; preserve contract |
| Control Tower intents | Yes | `/api/s2p/control-tower/intents` at `app/routers/s2p_control_tower.py:117` | No greenfield work; preserve contract |
| Financial impact | Partial | PVG impact endpoint exists at `/api/s2p/pvg/impact` in `app/routers/s2p_pvg.py:120` | Exact `/api/s2p/financial-impact` returns 404; add alias or confirm frontend uses `/pvg/impact` |
| PVG variants | Yes | `/api/s2p/pvg/variants` at `app/routers/s2p_pvg.py:79` | No greenfield work |
| PVG cycle time | Yes | `/api/s2p/pvg/cycle-time` at `app/routers/s2p_pvg.py:173` | No greenfield work |
| Intent routing | Partial | Router-level `_infer_intent` and classify endpoints exist in `s2p_control_tower.py`; tests cover mapping | No `IntentRouter` service class; create only if a service abstraction is explicitly needed |
| Invoice `intent` field | No | Primary fixture fields do not include `intent` | Add fixture enrichment only if downstream contract needs raw field |
| Invoice risk/recovery/cycle/verified fields | No | Primary fixture lacks `amount_at_risk`, `amount_recovered`, `cycle_time_hours`, `verified` | Add fixture enrichment or compute from existing fields |

### G6 Suppliers
| Requested Feature | Already Exists? | What's There | Remaining Gap |
|---|---|---|---|
| Suppliers preview | Yes | `/api/s2p/preview/suppliers` at `app/routers/s2p_preview.py:399` | No greenfield work |
| Suppliers trends | Partial | `/api/s2p/suppliers/trend-signals` exists at `app/routers/s2p_early_warning.py:147` | Exact `/api/s2p/suppliers/trends` returns 404; add alias or map contract |
| Suppliers clusters | Yes | `/api/s2p/suppliers/clusters` at `app/routers/s2p_clustering.py:117`; `/clustering` also exists in `s2p_suppliers.py:167` | Avoid duplicate cluster implementations |
| Suppliers heatmap | Partial | Per-supplier `/{supplier_id}/heatmap` exists at `app/routers/s2p_suppliers.py:219` | Aggregate `/api/s2p/suppliers/heatmap` returns 404 |
| Suppliers correlations | No | No smoke endpoint and no named correlation endpoint in router inventory | Add targeted aggregate endpoint if required |
| Supplier fixture `quarterly_otif` | No | `SupplierProfile` has `otif_by_quarter` at `app/services/supplier_profile_accumulator.py:19`, but fixture lacks `quarterly_otif` | Add fixture field or response mapping if contract requires it |
| Supplier fixture behavioral/category/monthly fields | No | Fixture lacks `behavioral_scores`, `category_exception_rates`, `monthly_volume` | Add only if frontend/API contract requires raw fields |

### G7 Novelty
| Requested Feature | Already Exists? | What's There | Remaining Gap |
|---|---|---|---|
| Novelty rate | Partial | `NoveltyTracker.novelty_rate` exists at `app/services/novelty_tracker.py:104`; `/status` includes tracker state | Exact `/api/s2p/novelty/rate` returns 404 |
| Novelty auto-pause | No | `NoveltyTracker.alert_active` exists at `app/services/novelty_tracker.py:111` | Exact `/api/s2p/novelty/auto-pause` returns 404 |
| Novelty history | Yes | `/api/s2p/novelty/history` at `app/routers/s2p_novelty.py:19` | No greenfield work |
| Score novelty_score | No | Score side effect records novelty, but `ScoreResponse` omits `novelty_score` at `app/routers/s2p.py:608-620`; tests assert novelty is not returned at `tests/test_novelty.py:191-201` | Add response field only with test contract update |
| Novelty monitor service/test coverage | Partial | `NoveltyTracker` service and 19 novelty tests exist | Name differs from requested `NoveltyMonitor`; avoid duplicate service |

## Recommendations
- G5: ENHANCE. Keep `s2p_control_tower.py` and `s2p_pvg.py`; add only targeted aliases or response/fixture fields for exact API contracts after confirming frontend expectations. Estimated effort: small to medium, depending on fixture enrichment.
- G6: ENHANCE. Reuse existing supplier routers and `SupplierProfileAccumulator`; add exact aggregate endpoints for trends, heatmap, and correlations only if smoke paths are the required contract. Estimated effort: medium.
- G7: ENHANCE. Reuse `NoveltyTracker`; add `/rate`, `/auto-pause`, and possibly `novelty_score` in score response only with explicit compatibility tests because current tests assert the score response excludes novelty. Estimated effort: small to medium.

## Revised Scope
- Net new files to create: likely none for G5/G7; possibly none for G6 if aggregate supplier endpoints fit existing routers. Create a new service only if correlation logic becomes large enough to justify it.
- Existing files to modify: likely `app/routers/s2p_pvg.py` or `app/routers/s2p.py` for financial impact alias; `app/routers/s2p_suppliers.py` and/or `app/routers/s2p_early_warning.py` for supplier aggregate aliases; `app/routers/s2p_novelty.py` and possibly `app/routers/s2p.py` for novelty response additions; fixture files only if raw fields are contractually required.
- Files already correct: `app/routers/s2p_control_tower.py`, `app/routers/s2p_pvg.py` for existing PVG endpoints, `app/routers/s2p_clustering.py`, `app/services/novelty_tracker.py`, and most existing tests.
- Net new tests: add endpoint tests for each missing exact path; update existing novelty score-response tests if `novelty_score` is added; add supplier aggregate tests if new endpoints are added.
- Estimated effort: targeted enhancement, not greenfield. G5 small, G6 medium, G7 small-to-medium because of score response compatibility.

## Blockers
- Exact frontend/API contract cannot be confirmed from this backend-only repo; frontend source is intentionally out of scope.
- Several requested paths return 404 despite equivalent or adjacent existing endpoints, so implementation prompts should not recreate whole features.
- Adding `novelty_score` to `/api/s2p/score` conflicts with current test expectations in `tests/test_novelty.py:191-201`; update tests deliberately if product contract changes.
- Fixture field additions may be unnecessary if endpoints compute equivalent values; decide contract before editing fixture data.
