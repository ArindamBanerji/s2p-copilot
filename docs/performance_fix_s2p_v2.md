# S2P Performance Fix v2

| Endpoint | Before | After R1 | After R2 | Method |
|---|---:|---:|---:|---|
| `/api/s2p/preview/queue` | 4.75s | 6.61s | 0.18s | Added 30s scored-invoice cache keyed by scorer type/state plus verified-count token; cache avoids graph-store identity churn and invalidates on outcomes/scorer swaps. |
| `/api/s2p/preview/suppliers` | 5.61s | 6.40s | 0.40s | Added 30s graph supplier-profile cache preserving the full graph/outcome contract; warm calls reuse derived profiles. |
| `/api/s2p/performance/summary` | 3.98s | 0.66s | 0.01s | Kept stable summary cache and replaced full-row/action fallback with AGE aggregate for `auto_approve`; category coverage uses aggregate counts. |
| `/api/s2p/evidence/compliance` | HTTPError | 0.02s | 0.02s | Hardened factor/extinction reads so optional graph evidence cannot 500 the invoice-derived compliance response. |
| `/api/s2p/insight/fingerprint?invoice_id=S2P-INV-0001` | HTTPError | 0.03s | 0.02s | Hardened fallback factor parsing for invoice fingerprints. |

Final round 3: queue 0.17s, suppliers 0.44s, performance summary 0.01s, compliance 0.01s, fingerprint 0.02s.

Validation:
- `python -m pytest backend/tests/ -q --timeout=120 -x`: 2094 passed.
- `python -m mypy backend/app/routers/s2p_preview.py backend/app/routers/s2p_performance.py --config-file pyproject.toml`: success.
- Focused post-query-cleanup tests: 78 passed.
