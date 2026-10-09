# S2P Response Materializer

Date: 2026-09-25

## Result

Implemented a self-contained `ResponseMaterializer` in `backend/app/services/response_materializer.py`, with S2P builders in `backend/app/services/s2p_materialization.py`. It does not import the new materializer being developed in the separate SDK repository.

The requested live timing gate passed after restarting only S2P on port 8002, with user approval and its existing command/environment preserved. All four requested endpoints were below 34 ms in rounds 2 and 3. Full backend validation: **2,100 passed**. Mypy: **clean**.

## Implementation

- One app-owned materializer, 30-second TTL measured after successful completion, and one all-decisions plus one verified-decisions fetch per build.
- Build-local normalized decision rows and a read-only history view are reused by computations. Small auxiliary graph reads, such as count queries, are memoized within the build; this does not claim only two total database statements.
- Startup prewarms the materializer. A background task waits 30 seconds between refreshes; blocking refresh work runs through `asyncio.to_thread`, not on the event loop. Existing unrelated startup warm-up is retained.
- Concurrent expired/missing reads join one refresh. Refresh publication is atomic and generation-checked, so invalidating an in-flight build cannot republish its old results.
- Invalidation after score, learn, and the legacy outcome path is instant and does not invoke refresh. The next read refreshes lazily. Partial persistence failures still invalidate the projection.
- Request callers receive owned response copies, including nested values. Empty dictionaries are valid cache hits. Individual computation errors preserve other successful results; handlers retain their original live/error fallbacks.
- Shutdown cancels the periodic task, invalidates responses, and joins any ongoing refresh thread before completing.
- The startup snapshot is bound to its tenant and store/scorer identities. A different tenant or replacement store/scorer uses the live fallback rather than receiving a response from the wrong context.

## Endpoint contracts

| Endpoint | Materialized key | Behavior retained |
|---|---|---|
| `/api/s2p/preview/queue` | `preview_queue` | Score latest 50 pending decisions once per build; apply requested limit at read time. Preview observations are written only for displayed invoices during requests, never by materializer refresh. |
| `/api/s2p/preview/suppliers` | `preview_suppliers` | Full graph supplier/outcome/fulfilment projection; requested limits preserve total/showing semantics. |
| `/api/s2p/performance/summary` | `performance_summary` | Existing count/action/savings calculation, using shared history for action counts. |
| `/api/conservation/status` | `conservation_status` | Unified fields and composite-gate layers preserved; authoritative score/learn gate paths remain separate. Shared what-if route retained. |
| `/api/s2p/evidence/compliance` | `evidence_compliance` | Existing invoice-derived compliance and persisted extinction evidence, including optional-evidence fallbacks. |
| `/api/s2p/insight/fingerprint?invoice_id=...` | `insight_fingerprint` | Results indexed by invoice/event ID; copy only the requested entry. Missing-invoice and numeric-factor fallbacks preserved. |

Compliance and fingerprints historically consume the invoice file, not decision history. They now share one invoice-file load per build; changing their provenance to graph decisions would have changed their semantics and was not done. Graph-backed queue/supplier builders do not depend on that file load succeeding.

Removed the scored-invoice and supplier-profile cache dictionaries/TTLs, performance summary cache, preview decision-history TTL, and compliance `cached_static` decorator. Legacy cache-clear entry points remain no-op compatibility hooks where callers/tests still import them. Unrelated evidence-context caches, score/gate caches, and unrelated tab-state entries are not response caches replaced by this change and remain intact.

## Files changed

| File | Change |
|---|---|
| `backend/app/services/response_materializer.py` | New independent synchronized materializer, response lookup, and invalidation helpers. |
| `backend/app/services/s2p_materialization.py` | Six builders, shared read view, conservation display projection, and context guard. |
| `backend/app/main.py` | Startup prewarm, background refresh/shutdown lifecycle, local conservation display wiring. |
| `backend/app/routers/s2p.py` | Score/learn/outcome invalidation. |
| `backend/app/routers/s2p_preview.py` | Extract pure response builders, remove scored/supplier caches, retain request-time observation writes. |
| `backend/app/routers/s2p_performance.py` | Replace summary cache with materialized-first lookup and live fallback. |
| `backend/app/routers/s2p_evidence.py` | Extract compliance builder; replace its decorator cache. |
| `backend/app/routers/s2p_insight.py` | Extract fingerprint builder and add invoice-keyed lookup. |
| `backend/app/services/s2p_preview_data.py` | Replace local TTL with an uncached reader plus reusable snapshot merge. |
| `backend/app/state/s2p_registry.py` | Reuse materialized conservation and read-only queue projection during tab warm-up. |
| `backend/tests/test_response_materializer.py` | Six new concurrency, TTL, invalidation, failure, endpoint-parity/isolation, and lifecycle tests. |
| `backend/tests/test_s2p_performance.py` | Migrate existing coalescing/expiry checks to the replacement materializer; retain their assertions. |
| `backend/tests/test_extinction_contract.py` | Assert the replacement cache is warmed, then verify a real outcome updates extinction evidence. |

## Validation

`python -m pytest backend/tests/ -q --timeout=120 -x`

Result: **2,100 passed**, 208.59 seconds. No tests deleted or skipped to pass this change. Deprecation warnings remain. Earlier runs exposed assertions against the removed cache internals; these were migrated to the replacement cache, retaining coalescing, expiry, and post-learn freshness checks.

`python -m mypy backend/app/services/response_materializer.py --follow-imports=skip --no-error-summary`

Result: **clean**. The extended check of both new services, main, all modified routers, and the registry also passed.

New tests prove ten concurrent misses perform one all-history read and one verified-history read; a build longer than TTL is fresh when it finishes; obsolete generations are discarded; response mutation cannot corrupt the cache; source failures can retry; six materialized payloads preserve live shapes; refresh does not write preview observations; local invalidation removes confirmed invoices; tenant/store isolation and startup/shutdown behavior are preserved.

## Live timings

Measured against the restarted backend at `127.0.0.1:8002`, including response-body reading and JSON decoding. These are three sequential rounds, not p95 statistics.

| Endpoint | Round 1 | Round 2 | Round 3 |
|---|---:|---:|---:|
| `/api/s2p/preview/queue` | 3.9988 s | 0.0247 s | 0.0333 s |
| `/api/s2p/preview/suppliers` | 0.0276 s | 0.0259 s | 0.0212 s |
| `/api/s2p/performance/summary` | 0.0162 s | 0.0272 s | 0.0119 s |
| `/api/s2p/evidence/compliance` | 0.0223 s | 0.0286 s | 0.0142 s |
| Sum of four endpoint times | 4.0649 s | 0.1064 s | 0.0805 s |

**Gate: passed.** Every requested endpoint was below two seconds in rounds 2 and 3. The permitted slower first queue request was 4.00 seconds; its breakdown between request-time observation writes and any refresh wait was not separately instrumented.

Additional checks: conservation status returned its unified/composite-gate payload in **0.0171 s**; fingerprint for `S2P-INV-0001` returned its invoice-specific payload in **0.0139 s**. Server logs showed successful startup and HTTP 200 responses with no materializer errors in the inspected log.

Initial timings against the old, non-reloading process included connection resets and slow suppliers. They are not measurements of this implementation. Only S2P was restarted; other copilots were left running. Operational scripts/log are under `.codex_tmp/`.

## Boundaries

This is strict TTL with coordinated lazy refresh, not stale-while-revalidate. A request at expiry or after invalidation can wait for the history fetch/build; the warm timing gate does not establish a below-two-second SLA at every refresh boundary. The periodic loop waits 30 seconds after each refresh finishes, so its full cycle also includes build duration.

External database writes bypassing the application invalidation hooks may remain invisible for the TTL. Scoring and learning authorization do not use this cached display projection. Replacing the app's store/scorer requires recreating its materializer to regain cached service; until then the identity guard deliberately selects the live fallback.
