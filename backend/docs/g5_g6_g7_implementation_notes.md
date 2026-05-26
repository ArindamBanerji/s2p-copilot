# G5+G6+G7 S2P Targeted Enhancement — Implementation Notes
**Date:** 2026-05-25
**Repo:** s2p-copilot backend

## Recovery State
- The recovery pass found all six target endpoints already mounted.
- GET /api/s2p/financial-impact returned 200 before completion edits.
- GET /api/s2p/suppliers/trends returned 200 before completion edits.
- GET /api/s2p/suppliers/heatmap returned 200 before completion edits.
- GET /api/s2p/suppliers/correlations returned 200 before completion edits.
- GET /api/s2p/novelty/rate returned 200 before completion edits.
- GET /api/s2p/novelty/auto-pause returned 200 before completion edits.
- POST /api/s2p/score already included novelty_score.
- data/synthetic_invoices.json already had all G5 enrichment fields.
- data/s2p_demo_suppliers.json already had all G6 enrichment fields.
- tests/test_fixture_enrichment.py already covered fixture field preservation.
- tests/test_novelty.py already covered G7 endpoint and score behavior.
- Missing completion items were G5/G6 target-named tests and this implementation note.

## G5 Financial Impact Design
- The financial impact endpoint lives in the existing PVG router.
- No new router include was added to app/main.py.
- The endpoint path is GET /api/s2p/financial-impact.
- It computes totals from enriched synthetic invoice fixture data.
- total_at_risk comes from amount_at_risk with amount fallback.
- total_recovered comes from amount_recovered, treating null as zero.
- total_leakage_prevented mirrors recovered fixture dollars.
- by_category groups recovered, at_risk, and count by S2P category.
- source is fixture because the endpoint derives from fixture enrichment.
- auto_approve_savings_hours now follows the explicit count rule.
- The count rule is verified auto_approve decisions times 0.25 hours.
- Empty or unverified invoice input returns zero recovered and zero savings.
- Existing PVG routes remain under /api/s2p/pvg/*.

## Invoice Fixture Enrichment
- The invoice fixture remains at ../data/synthetic_invoices.json from backend cwd.
- The fixture contains 50 invoices.
- Existing fields such as invoice_id, amount, category, factors, and supplier_id are preserved.
- Added fields are intent, amount_at_risk, amount_recovered, cycle_time_hours, and verified.
- intent follows the control-tower category mapping.
- amount_at_risk mirrors amount.
- amount_recovered is amount * 0.93 for verified invoices and null otherwise.
- cycle_time_hours is deterministic and bounded between 0.5 and 48.0.
- verified is boolean on every invoice.

## G6 Supplier Trends Design
- Supplier trends are served by the existing early-warning supplier router.
- No new supplier trend service was created.
- GET /api/s2p/suppliers/trends now includes a target-contract suppliers array.
- The suppliers array includes supplier_id, name, quarterly_otif, trend, and trend_delta.
- quarterly_otif in the suppliers array is a list of quarter/otif objects.
- trend_delta is last quarter OTIF minus first quarter OTIF.
- declining uses delta < -0.10.
- improving uses delta > 0.05.
- stable covers the remaining range.
- The existing trends array is retained for backward-compatible current tests and UI.

## G6 Supplier Heatmap Design
- GET /api/s2p/suppliers/heatmap is an aggregate endpoint on the existing supplier router.
- The aggregate path is declared before the dynamic /{supplier_id}/heatmap path.
- The response now includes suppliers as supplier IDs.
- supplier_details preserves the prior richer per-supplier objects.
- categories are the five S2P categories.
- matrix is N suppliers by 5 categories.
- hot_spots lists cells with rate > 0.15.
- Hot spot severity is critical as a deterministic rule.
- Existing category_totals and category_averages remain available.
- Existing /api/s2p/suppliers/{supplier_id}/heatmap still returns 200.

## G6 Supplier Correlations Design
- GET /api/s2p/suppliers/correlations is on the existing supplier router.
- correlations now contains per-supplier risk correlation rows.
- Each row includes supplier_id, name, exception_rate, otif, otif_exception_score, and risk_score.
- otif_exception_score uses -(1.0 - otif) * exception_rate * 10.0 clamped to [-1, 1].
- risk_score uses exception_rate, inverse OTIF, and a declining trend penalty clamped to [0, 1].
- metric_correlations retains the previous aggregate Pearson-style metric pairs.
- No pandas, scipy, sklearn, or heavy dependency was introduced.

## Supplier Fixture Enrichment
- The supplier fixture remains at ../data/s2p_demo_suppliers.json from backend cwd.
- The fixture contains 10 suppliers.
- Existing fields such as supplier_id, name, exception_rate, and otif_score are preserved.
- Added fields are quarterly_otif, behavioral_scores, category_exception_rates, and monthly_volume.
- quarterly_otif has four deterministic quarter entries.
- At least two suppliers decline by more than 0.10.
- At least one supplier improves by more than 0.05.
- behavioral_scores values are bounded in [0, 1].
- category_exception_rates covers exactly the five S2P categories.
- monthly_volume has six positive deterministic integers per supplier.

## G7 Novelty Rate Design
- GET /api/s2p/novelty/rate is on the existing novelty router.
- It uses the existing NoveltyTracker singleton state.
- It does not create a parallel novelty tracker.
- categories include category index, category name, novelty_rate, and status.
- overall_rate comes from the tracker novelty_rate property.
- GREEN is rate < 0.20.
- AMBER is 0.20 <= rate < 0.30.
- RED is rate >= 0.30.
- No decisions return overall_rate 0.0 and overall_status GREEN.

## G7 Novelty Auto-Pause Design
- GET /api/s2p/novelty/auto-pause is advisory only.
- The endpoint does not mutate conservation state.
- paused_categories includes categories whose novelty rate is >= 0.30.
- Each paused category includes category, name, novelty_rate, and reason.
- advisory_only is always true.

## novelty_score Integration
- POST /api/s2p/score includes novelty_score.
- The score route still records the novelty observation side effect.
- NoveltyTracker exposes get_last_distance for safe public access.
- Non-finite nearest distances are converted to None for JSON safety.
- Non-finite distances are recorded as 0.0 to avoid leaking inf or NaN into history.
- The prior test that expected novelty to be excluded now expects novelty_score.

## Files Changed
- app/routers/s2p_pvg.py: financial impact auto-approve savings count rule.
- app/routers/s2p_early_warning.py: target suppliers trend contract.
- app/routers/s2p_suppliers.py: heatmap matrix/hot spots and per-supplier correlations.
- app/routers/s2p_novelty.py: novelty rate and auto-pause endpoints.
- app/routers/s2p.py: novelty_score response integration.
- app/services/novelty_tracker.py: get_last_distance accessor.
- tests/test_financial_impact.py: G5 financial impact contract tests.
- tests/test_supplier_aggregate.py: G6 aggregate endpoint contract tests.
- tests/test_g5_g6_aggregate_endpoints.py: compatibility tests updated for enriched contracts.
- tests/test_novelty.py: G7 endpoint and score response tests.
- docs/g5_g6_g7_implementation_notes.md: this implementation record.

## Files Intentionally Unchanged
- app/main.py was not changed.
- No include_router calls were added.
- S2P tensor and domain config files were not changed.
- Existing clustering, payment, discovery, preview, governance, simulation, and control-tower routers were not recreated.
- No frontend files were changed.
- No SOC files were changed.
- No copilot-sdk files were changed.
- No dependency or package files were changed.

## API Contract Summary
- GET /api/s2p/financial-impact returns recovered, at-risk, leakage-prevented, by-category, savings, and source fields.
- GET /api/s2p/suppliers/trends returns suppliers, trends, counts, total, and source.
- GET /api/s2p/suppliers/heatmap returns suppliers, supplier_details, categories, matrix, hot_spots, totals, averages, total, and source.
- GET /api/s2p/suppliers/correlations returns per-supplier correlations and metric_correlations.
- GET /api/s2p/novelty/rate returns category and overall novelty rates.
- GET /api/s2p/novelty/auto-pause returns advisory paused categories.
- POST /api/s2p/score returns novelty_score in addition to the existing score response.

## Test Summary
- Targeted tests run: tests/test_financial_impact.py, tests/test_supplier_aggregate.py, tests/test_novelty.py.
- Targeted result: 33 passed.
- Full backend suite result: 728 passed.
- Target endpoint post-checks passed for financial-impact, supplier trends, supplier heatmap, supplier correlations, novelty rate, and novelty auto-pause.
- Existing endpoint preservation checks passed for control tower, PVG, suppliers, novelty status/history, and preview paths.
- novelty_score response check passed for POST /api/s2p/score.
- Fixture compatibility validation passed.
- Tensor and penalty validation passed for (5,5,7) and penalty_ratio 5.0.
- Heavy dependency scan passed with no sklearn, scipy, or pandas imports under app/.

## Residual Risks
- Existing endpoint consumers may still read the legacy trends, supplier_details, or metric_correlations fields.
- Both legacy and target-shaped fields are retained to reduce internal churn.
- Pytest cache writes may warn under the current filesystem permissions.
- The implementation remains fixture-driven, not live-data backed.
