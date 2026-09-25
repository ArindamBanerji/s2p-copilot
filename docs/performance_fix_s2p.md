# S2P Performance Fix

## Accumulation Bug

Root cause: `_SUMMARY_CACHE` was keyed by `id(graph_store)`. The active graph lifecycle can recreate store objects, so each request could add another summary entry and miss the two-second cache.

Fix: use one domain-scoped cache key and a five-second TTL. The cache is now bounded regardless of graph-store recreation.

## Broken Endpoints

`evidence/compliance`: optional extinction-history failures no longer turn the invoice-derived compliance response into HTTP 503; the endpoint returns the compliance payload with an empty extinction list.

`insight/fingerprint`: factor computation now falls back to numeric invoice factors when the factor engine is unavailable, so a valid invoice still returns fingerprint data.

## TTL Cache

Status: verified and strengthened. S2P tab state uses `TabStateCache("s2p", ttl_seconds=5.0)`. Preview queue and supplier reads now share a bounded five-second decision scan cache, with verified outcomes refreshed and mutation paths invalidating the cache.

## Validation

- Targeted endpoint/performance/preview tests: 69 passed; preview and Rule #72 regression tests: 48 passed.
- Full backend suite: 2093 passed, 1 pre-existing order-dependent auto-approve failure; the failed test passes in isolation.
- `python -m mypy backend/app/routers/s2p_performance.py --config-file pyproject.toml`: clean.
