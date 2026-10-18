## Stub Fix Design — s2p-copilot (3 endpoints)

### Investigation scope

This investigation used read-only source inspection and GET probes against the running S2P backend on port 8002. No application or test code was changed.

The latest recorded stable baseline at the start of the investigation was 2,255 passed and 7 known Track-2 failures, with no setup errors. The session state also records that the S2P preseed path can stop before writing when the root `/api/trajectory` endpoint returns 404.

The live counts have drifted slightly from the probe quoted in the task because the shared AGE graph contains additional stress and Playwright decisions:

| Endpoint | Task observation | Current read-only probe |
|---|---:|---:|
| `/api/s2p/preview/queue` | 60 nulls, 188 values equal to `0.50`, about 18 KB | 60 nulls, 204 values equal to `0.50`, 18,566 bytes |
| `/api/s2p/preview/suppliers` | 30 nulls, about 8.6 KB | 30 nulls, 8,631 bytes |
| `/api/s2p/suppliers` | 79 nulls, 33 empty objects, about 9.2 KB | 87 nulls, 36 empty objects, 9,929 bytes |

The standalone supplier count grew from eleven to twelve because two runtime-only suppliers are now present alongside the ten fixture suppliers. The defect pattern is unchanged.

### Pattern: How Working S2P Endpoints Access Live Data

The application owns one scorer and one GraphStore:

- `app.state.scorer` is the active S2P scorer.
- `app.state.graph_store` is the scorer's graph store.
- `app.state.s2p_graph_reader` is an `S2PGraphReader` over that same store and the `s2p` domain.
- `app.state.materializer` reads `get_all_decisions("s2p")` and `get_verified_decisions("s2p")` once per refresh and supplies a read-only `SnapshotStore` to response builders. Its TTL is 30 seconds.

The strongest working patterns are `/api/s2p/performance/summary` and `/api/conservation/status`.

`GET /api/s2p/performance/summary` in `backend/app/routers/s2p_performance.py` resolves the app's graph reader, uses GraphStore counts for total, verified, correct, and recommended actions, and can build the same response from the materializer snapshot. The current live response reports 28,341 scored decisions and 1,159 verified decisions.

`GET /api/conservation/status` is assembled in `backend/app/main.py` with the shared conservation router and an S2P state provider. The S2P override uses the materialized live conservation payload. It derives coverage, rolling accuracy, volume, and gate status from verified graph data. The current response reports 1,159 verified, 1,090 correct, all five categories covered, and GREEN status.

`GET /api/s2p/evidence/compliance` uses a hybrid pattern: fixture invoices provide invoice facts, factor computation reports availability, and GraphStore provides persisted extinction history. It does not return nulls, but the current response correctly marks `compliance_available: false` and lists degraded factors when production graph context is missing. That explicit availability contract is preferable to neutral defaults.

`GET /api/s2p/preview/conservation` is not a model for live headline values. It reads recent queue scores for its computed fields, but its primary status, accuracy, verified-decision count, rate, and curve are hardcoded illustration values. Its absence of nulls does not make those fields live.

The stub fixes should therefore follow this pattern:

1. Resolve the application-owned GraphStore through `app.state.s2p_graph_reader` or the materializer's `SnapshotStore`.
2. Merge decisions and verified outcomes by `decision_id`, using `merge_decisions`.
3. Preserve factor and field provenance. Missing observations must be marked unavailable rather than converted to `0.50` or `{}`.
4. Use fixture rows only as explicitly labeled supplier master/demo context, never as learned outcomes.
5. Build queue and both supplier projections from one normalized snapshot so a refresh cannot mix generations.

No new low-level GraphStore method is required. Existing methods cover the needed decision data: `get_all_decisions`, `get_verified_decisions`, `get_decision`, aggregate counts, and the S2P graph reader's direct-context queries. Some business facts are absent from the stored records and require upstream persistence or fixture/master-data enrichment.

### Endpoint 1: `/api/s2p/preview/queue`

- **File:** `backend/app/routers/s2p_preview.py:322`
- **Handler:** `preview_queue`, registered by `@router.get("/queue", response_model=GenericResponse)` on the `/api/s2p/preview` router
- **Function size:** the handler is 10 lines (`323-332`). Its response path also includes `_preview_queue_payload` (5 lines), `queue_read_payload` (9 lines), `score_pending` (20 lines), `_score_invoice` (41 lines), and `queue_payload` (24 lines).
- **Current:** The handler reads the materialized `preview_queue` response or calls `read_decisions` against the active graph. `read_decisions` merges all decisions with verified outcomes. `score_pending` keeps the newest pending decision per invoice and re-scores up to 50 of them through the live scorer's read-only path. The route then returns a requested slice, five records by default, under both `exceptions` and `invoices`.
- **Null source:** Each of the five displayed records currently has six missing values: `po_reference`, `ground_truth_action`, `ground_truth_action_index`, and metadata `due_date`, `invoice_date`, and `po_number`. Because the identical records are serialized under both `exceptions` and `invoices`, six missing fields become 60 nulls. The current pending decisions are stress/Playwright records whose stored metadata does not contain those facts.
- **Default source:** `_decision_invoice` reads `factor_names`, `factor_vector`, and any factor mapping. `_score_invoice` then uses `factors.get(name, 0.5)` for every canonical factor. Four of the five current decisions already contain all-neutral eight-factor vectors, while the fifth has two values that are legitimately exactly `0.50`. Each value is repeated in the factor map, the response factor vector, and the metadata factor vector, then repeated again under the two list aliases. This produces the observed 204 occurrences. The earlier 188 count reflects a different five-row snapshot or factor shape, not a different code path.
- **Available data:** `synthetic_invoices.json` has 50 complete demo invoices with PO number, ground-truth action, invoice/due dates in metadata, and seven named factors. It does not contain the newer `environmental_risk` factor. Current GraphStore decisions contain IDs, category, recommended action, confidence, stored factors/vector, probabilities, supplier/invoice IDs, amount, status, and metadata when supplied at score time. Verified rows add actual action/correctness, but a pending queue has no legitimate ground truth. Initial centroid values exist for all five categories, five actions, and seven factors in `s2p_initial_centroids.json`; centroids are model parameters, not observations and must not be returned as invoice factor evidence.
- **Fix:** Project the queue from canonical pending graph decisions and retain the stored recommendation, confidence, probabilities, factor names, and vector. Normalize factor representations once. Accept a factor vector only when its schema/version and length match the current configured factors. Do not fill absent positions with `0.50`.
- **Fix, enrichment:** For sample IDs that match `synthetic_invoices.json`, merge the labeled fixture facts by invoice ID and retain `provenance: sample`. For live invoices, read direct invoice/PO/GR context through `S2PGraphReader` and persisted decision metadata. If PO or dates were not persisted, return explicit availability/provenance fields or omit the optional values according to a revised response model.
- **Fix, outcome fields:** Remove `ground_truth_action` and `ground_truth_action_index` from ordinary pending production items, or make their availability explicit. Substituting `actual_action` is valid only after verification, at which point the record no longer belongs in the pending queue. Exposing fixture ground truth is acceptable only for a clearly labeled sample record.
- **Fix, factors:** When stored factors are incomplete, attempt computation only with a complete invoice plus graph context and use `compute_all_factors(..., return_status=True)`. Persist and return the resulting provenance/degraded-factor list. If evidence is still insufficient, mark the row or factors unavailable and abstain from re-scoring. Do not score an all-neutral synthetic vector. Upstream score writers should persist `factor_names`, the vector, schema version, and factor provenance so later queue reads do not need to reconstruct an old scoring context.
- **Fix, response duplication:** Make one list canonical. If backward compatibility requires both `exceptions` and `invoices`, derive both from one projection but document them as aliases; scanner counts will otherwise double every legitimate missing/default value.
- **Complexity:** moderate
- **Risk:** Removing neutral substitution exposes sparse legacy decisions and may require frontend handling for unavailable factors. Recomputing old decisions with today's scorer can change recommendations, so the contract must distinguish original stored score from an optional current re-score. Ground-truth removal is an API-contract change. Reading all 28,000 decisions per request is already mitigated by the materializer but should remain snapshot-based.

### Endpoint 2: `/api/s2p/preview/suppliers`

- **File:** `backend/app/routers/s2p_preview.py:391`
- **Handler:** `preview_suppliers`, registered by `@router.get("/suppliers", response_model=GenericResponse)` on the `/api/s2p/preview` router
- **Function size:** the handler is 8 lines (`392-399`); `suppliers_payload` is 14 lines (`402-415`).
- **Current:** The materializer and fallback path both call `demo_supplier_profiles()`, which reloads `data/s2p_demo_suppliers.json` and overlays `_preview_supplier`. The endpoint intentionally returns exactly ten fixture suppliers with `source: fixture` and `provenance: sample`; it does not inspect GraphStore decisions.
- **Null source:** `_preview_supplier` emits `region`, `otif`, and `financial_health_trend` for every supplier. The fixture has none of those keys, yielding 30 nulls. It does contain `otif_score`, `quarterly_otif`, `lead_time`, `recent_trend`, category, exception rate, invoice totals, payment terms, behavioral scores, category exception rates, and monthly volume. The `otif` null is primarily a projection mismatch: code asks for `otif`, while the fixture stores `otif_score` and `quarterly_otif`. Region and financial-health trend are genuinely absent.
- **Relationship to queue:** The two routes share the response materializer but not their input. Queue uses normalized graph decisions; preview suppliers ignores the shared decision snapshot and reloads the fixture. Fixing queue cannot change supplier preview output.
- **Fix:** Replace the independent fixture-only computation with a supplier projection from the same normalized graph snapshot used by queue. `s2p_preview_data.supplier_profiles(decisions)` already groups decisions by supplier and invoice and derives verified exception rate, trend, average invoice amount, OTIF when fulfillment evidence exists, and lead-time summaries. Extend that projection to accept supplier master rows as context and merge by `supplier_id`.
- **Fix, preview scope:** Keep the preview's stable ten-supplier demo scope by filtering the live aggregation to the ten fixture supplier IDs and using fixture values only when live observations are unavailable. This prevents stress/Playwright suppliers from contaminating the curated preview while still allowing verified live decisions to update its metrics. Add field-level provenance such as `graph`, `sample_fallback`, or `unavailable`.
- **Fix, field mapping:** Map `quarterly_otif` to the response's OTIF history shape and `lead_time` to its lead-time shape rather than emitting null aliases. Keep `otif_score` as the scalar. Region and financial-health trend need a supplier-master/enterprise source; until one exists, mark them unavailable or remove them from the required preview contract. Do not invent them from decision accuracy.
- **Contract conflict:** Existing tests explicitly assert that preview suppliers preserve the fixture exactly, never read the live graph, and never include stress/colliding supplier IDs. Wiring this route to live metrics requires a deliberate test and product-contract migration. The filtered live-plus-fixture design above preserves stable membership and sample fallback, but it changes the current fixture-isolation rule.
- **Complexity:** moderate
- **Risk:** Mixing sample master data with learned metrics without field-level provenance can present fixture values as observed performance. Live rows often lack OTIF and lead-time observations, so a graph-only implementation would replace useful fixture context with unavailable fields. The preview must remain insulated from test supplier IDs.

### Endpoint 3: `/api/s2p/suppliers`

- **File:** `backend/app/routers/s2p_suppliers.py:212`
- **Handler:** `suppliers`, registered by `@router.get("")` and `@router.get("/")` on the `/api/s2p/suppliers` router
- **Function size:** the handler is 8 lines (`214-221`); `_profile_summary` is 23 lines (`90-112`).
- **Current:** The endpoint has no `Request` argument and cannot reach `app.state.graph_store`. It reads the module-global `SupplierProfileAccumulator`, which loads the ten supplier fixtures at import time and receives only outcomes processed during the current Python process. It returns fixture profiles plus runtime-only suppliers. The current live response has ten fixture suppliers and two computed test suppliers.
- **Null source:** `SupplierProfileAccumulator._load_fixtures` populates only a subset of its dataclass. It converts textual `recent_trend` values such as `declining` through `_numeric_or_none`, producing `exception_rate_trend: null`. It ignores fixture `quarterly_otif` and `lead_time`, and the fixture lacks last invoice date, pricing trend, last-updated timestamp, and payment-day metrics. Consequently every current supplier has null `exception_rate_trend`, `avg_lead_time_days`, `last_invoice_date`, `pricing_trend`, both nullable payment-response metrics, and `last_updated`. The two runtime-only suppliers also lack OTIF. `CollectionResponse` injects `count: null` because the handler returns `total` but not `count`.
- **Empty-object source:** `otif_by_quarter`, `lead_time_by_quarter`, and `lead_time_by_volume` default to empty mappings. Three empty mappings per current twelve suppliers produce 36 empty objects; the earlier eleven-supplier response produced 33.
- **Available data:** The supplier fixture can populate scalar OTIF, quarterly OTIF, current/contractual lead time, recent trend, category, exception rate, payment terms, and behavioral/category distributions. Verified GraphStore decisions can provide actual actions, correctness, amount, category, supplier, timestamps, and any payment/quality/lead-time facts persisted in decision or outcome context. The accumulator knows how to calculate rolling exception rate, trend, pricing trend, payment behavior, quality, and lead-time groupings from `SupplierEvent` rows, but it is not hydrated from existing GraphStore history after restart.
- **Fix:** Introduce one graph-backed supplier-profile provider used by both supplier routes. On each materializer refresh, merge all decisions with verified outcomes, normalize decision/outcome metadata, group by supplier and invoice, and compute profiles from verified events. Merge supplier fixture/master fields by supplier ID only as labeled fallback. The existing stateless `supplier_profiles(decisions)` function can seed this implementation, or the accumulator can gain an idempotent `replace_from_verified_history` method. A stateless snapshot projection is safer because it cannot double-count replayed events.
- **Fix, route access:** Add `request: Request` to `suppliers` and read the materialized supplier-profile snapshot or `app.state.s2p_graph_reader`. Do not rely on the process-global accumulator as the list endpoint's authoritative history. Keep the accumulator for event-driven detail/history behavior only if it is hydrated from the same snapshot and given clear lifecycle ownership.
- **Fix, fixture mapping:** Load `quarterly_otif` into `otif_by_quarter`; map the fixture's lead-time values into an explicitly labeled fixture lead-time representation; translate `recent_trend` into the categorical `trend_direction` rather than trying to parse it as a float. Populate `count` with `len(rows)`. Leave last invoice date, payment-day correlation, pricing trend, and last-updated unavailable unless graph events actually contain the inputs.
- **Relationship to preview suppliers:** The endpoints have different presentation goals: preview is a curated ten-supplier demo view; standalone suppliers is the complete detailed portfolio, including live-only suppliers. They should share the normalized supplier-profile provider and fixture/master merge logic, then apply separate membership and response projections. Fixing one handler alone will not fix the other today.
- **Complexity:** needs-new-method at the application service layer; no new GraphStore protocol method
- **Risk:** Replaying the graph into the current mutable accumulator can duplicate events unless replacement is atomic and idempotent. Combining fixture invoice counts with live event counts can double count without a documented baseline-plus-delta rule. Historical decisions missing supplier ID cannot be assigned. Sparse payment, fulfillment, and lead-time context will remain unavailable until upstream score/outcome calls persist those fields.

### Preview Queue and Supplier Dependency

Queue and supplier computations should share one materializer snapshot, factor/metadata normalization, and supplier identity rules. They should not call one another. Supplier metrics must aggregate verified outcomes, while queue contains pending decisions; deriving supplier performance from queue rows would be semantically wrong. The correct dependency is a shared normalization/provider service:

```text
GraphStore decisions + verified outcomes
                 │
          normalized snapshot
          ┌──────┴────────┐
   pending queue      supplier profiles
                       ┌────┴─────┐
                curated preview  full portfolio
```

### Preseed Impact

The S2P branch of `copilot-sdk/scripts/preseed_all_copilots.py` calls root `GET /api/trajectory` in `check_already_seeded` before entering its score/learn loop. Root `/api/trajectory` currently returns 404 on port 8002, so that preseed invocation returns immediately and writes zero S2P decisions. It does not partially seed before the failure.

The current GraphStore is nevertheless populated: live performance reports 28,341 total and 1,159 verified decisions. Those rows came from other tests, probes, or application flows, not from the failed preseed attempt. They include many sparse stress/Playwright records, which explains why the newest preview queue is dominated by incomplete metadata and neutral vectors.

The preseed repair is outside these three handlers, but a clean demo-data verification requires either mounting a compatible root `/api/trajectory` endpoint or changing the script's S2P check to an existing S2P count endpoint before re-running preseed. The seed events provide all eight current factor inputs plus supplier and amount, but do not provide PO number, invoice/due dates, OTIF, lead time, payment, or financial-health context. Preseed alone therefore cannot fill every supplier field.

### Implementation Order

1. **Define and test the normalized decision/factor contract.** Parse mappings and vectors, enforce schema/length, merge outcomes, and carry availability/provenance. This is shared by queue and supplier aggregation.
2. **Fix `/api/s2p/preview/queue`.** Stop neutral substitution and distinguish stored original scores from optional current re-scores. Add explicit sparse-record behavior before changing supplier metrics.
3. **Build the graph-backed supplier-profile snapshot provider.** Aggregate verified decisions, merge labeled supplier master/fixture context, and make refresh atomic.
4. **Fix `/api/s2p/suppliers`.** Move the full portfolio list to that provider, populate fixture-backed fields correctly, and keep genuine missing fields unavailable.
5. **Migrate `/api/s2p/preview/suppliers`.** Reuse the provider but filter to curated fixture IDs. Update the existing fixture-isolation contract and tests as an explicit product decision.
6. **Repair and rerun S2P preseed separately.** Verify the endpoints with controlled demo decisions rather than the shared graph's accumulated stress data.

Queue can be fixed independently. The two supplier endpoints should share one provider and land together to avoid another split-brain representation.

### Blockers

- Existing preview-supplier tests require fixture-only behavior and forbid live GraphStore reads, while this design goal requires live metrics. Implementation needs an approved contract change and corresponding test updates.
- The failed S2P preseed wrote no decisions because it probes the missing root `/api/trajectory` before its write loop. The current graph is large but not a controlled demo baseline.
- Pending decisions do not have ground truth by definition. The queue contract must stop requiring it for live records or clearly limit it to labeled samples.
- Historical stress and Playwright decisions often lack PO number, dates, fulfillment, lead-time, payment, and financial-health evidence. Those values cannot be reconstructed from correctness or centroids.
- `synthetic_invoices.json` contains seven factors, while the current S2P configuration has eight; it lacks `environmental_risk`. That factor needs graph/master context or an explicit unavailable state.
- The module-global accumulator is not restored from GraphStore after restart. Reusing it as the authority requires an idempotent graph replay/replace operation; otherwise a stateless materialized projection should replace it for list responses.
- Fixture invoice totals are historical baselines, while live graph counts are observed events. Their merge rule must be defined to prevent double counting.
