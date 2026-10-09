# P2 Diagnostic Report — S2P (s2p-copilot)
Date: 2026-09-28
Scanner: P2-DIAG-S2P (terra/high)
Baseline: 2,215 passed, 0 skipped

## Scan Scope

Files scanned: 109
Known findings excluded: 8 (F01 through F08)

The scan covered every Python file under `backend/app/routers/`, `backend/app/services/`, `backend/app/domains/`, and `backend/app/framework/` (27,581 lines). Candidate catches, defaults, sentinels, and availability flags were traced through their callers and the current S2P frontend. Findings were graded down where the degraded state does not currently mislead a user. No sticky-false flag survived caller review.

## New Findings

### P2 — User-Visible Data Quality

| ID | File | Line | Function | Pattern | Description |
|----|------|------|----------|---------|-------------|
| S2P-P2-N01 | `backend/app/routers/s2p_demo_beats.py` | 44 | `_rows` | 1 — Collapsed error states | A missing reader or `GraphUnavailableError` returns `[]`, so frozen-twin, what-if, day-zero, and confidence views cannot distinguish an unavailable decision history from a genuinely empty history. |
| S2P-P2-N02 | `backend/app/routers/s2p_demo_beats.py` | 188 | `frozen_twin` | 5 — Wrong reason on fail-closed | The not-frozen or missing-manager branch always says "Graph unavailable" even when the graph read succeeded, giving the user the wrong reason that comparison data is absent. |
| S2P-P2-N03 | `backend/app/services/s2p_preview_data.py` | 28 | `_verified_rows` | 1 — Collapsed error states | Any verified-decision read failure becomes `[]`; `read_decisions` can then classify already verified decisions as pending preview work. |
| S2P-P2-N04 | `backend/app/services/cross_copilot_signals.py` | 24 | `fetch_supplier_signals` | 1/2 — Collapsed error states / missing availability flag | An unconfigured client, offline cache, non-200 response, malformed payload, and transport exception all become `[]`; scoring treats the outage as “no supplier signal” without provenance or availability. |
| S2P-P2-N05 | `backend/app/services/optimizer_export.py` | 147 | `_raw_centroids` | 1 — Collapsed error states | A centroid read exception returns `None`, after which `_centroids` silently exports canonical profile centroids as current model state. |
| S2P-P2-N06 | `backend/app/services/optimizer_export.py` | 155 | `_dk_weights` / `_compute_metadata` | 5 — Wrong reason on fail-closed | DK read failure is collapsed to no weights and exported as `dk_status="pre-transition (omitted)"`, falsely describing an outage as an ordinary cold/pre-transition model. |
| S2P-P2-N07 | `backend/app/routers/s2p.py` | 2570 | `score_procurement_event` | 2 — Missing availability flag | Frozen-twin enrichment exceptions omit the `frozen_twin` field with no separate availability/degraded flag, so score consumers cannot distinguish an enrichment outage from no twin result. |
| S2P-P2-N08 | `backend/app/domains/s2p/factors.py` | 437 | `compute_all_factors` | 1 — Collapsed error states | Individual factor exceptions are replaced with supplied values or `0.5` and no degradation metadata; failures after the required-context guard can therefore appear as real factor values in scoring. |
| S2P-P2-N09 | `backend/app/routers/s2p_evidence.py` | 515 | `compliance_payload` | 1 — Collapsed error states | Factor-computation failure falls back to raw or empty factors, then defaults the tax score to `1.0`; invoices can be shown as noncompliant without any factor-availability signal. |
| S2P-P2-N10 | `backend/app/routers/s2p_insight.py` | 150 | `fingerprint_payload` | 1 — Collapsed error states | Factor-computation failure is presented as a normal raw/empty fingerprint, with no flag distinguishing computation failure from an invoice that has no numeric factors. |
| S2P-P2-N11 | `backend/app/routers/s2p_data_helpers.py` | 12 | `load_json` | 1/2 — Collapsed error states / missing availability flag | Missing or malformed invoice/supplier fixtures are replaced with caller defaults; active compliance, fingerprint, supplier, and insight endpoints report typed empty data as though the source were successfully read. |
| S2P-P2-N12 | `backend/app/domains/s2p/evolution/service.py` | 112 | `_load_fixture` | 1 — Collapsed error states | Fixture I/O or JSON failure becomes `{}`, after which evolution state is synthesized as empty/default without exposing fixture availability. |
| S2P-P2-N13 | `backend/app/services/supplier_profile_accumulator.py` | 76 | `_load_fixtures` | 1/5 — Collapsed error states / wrong reason | Fixture failure becomes `raw=[]`, while the suppliers endpoint still labels the result `accumulator_with_fixture_fallback`; zero or partial profiles appear to come from a functioning fallback source. |
| S2P-P2-N14 | `backend/app/routers/s2p_suppliers.py` | 74 | `_trend_direction` | 4 — Sentinel erasure | `exception_rate_trend=None` is rendered as `"stable"`, so missing or insufficient trend evidence is presented as an observed stable trend. |
| S2P-P2-N15 | `backend/app/services/cohort_status.py` | 82 | `_load_instrument` | 1/5 — Collapsed error states / wrong reason | A missing, unreadable, or malformed oracle artifact returns `validated=False`, which is indistinguishable from an artifact that was read and failed validation. |
| S2P-P2-N16 | `backend/app/services/cohort_status.py` | 154 | `_read_decisions` | 2 — Missing availability flag | When no graph reader is configured, the service returns `[]`; cohort status then reports zero real cohorts/collecting state without identifying the unavailable decision source. |
| S2P-P2-N17 | `backend/app/routers/s2p_insight.py` | 242 | `cross_graph` | 1/5 — Collapsed error states / wrong reason | Missing or malformed Celonis data is reduced to `{}`, but the endpoint still emits a fixed Chicago AP bottleneck and root-cause narrative, turning unavailable process evidence into a concrete diagnosis. |

### P3 — Internal Observability / Type Contract

| ID | File | Line | Function | Pattern | Description |
|----|------|------|----------|---------|-------------|
| S2P-P3-N01 | `backend/app/routers/s2p_evidence.py` | 221 | `_conservation_snapshot` | 1 — Collapsed error states | Non-graph exceptions become `{}` in the audit pack. The empty object avoids a false healthy value, but it carries no explicit reason or availability state. |
| S2P-P3-N02 | `backend/app/services/centroid_explorer.py` | 215 | `_p39_evidence` | 6 — Dead flag | P39 read failure is correctly represented by `p39_available=False`, but the current frontend and production backend have no consumer for that flag; only a test asserts it. |
| S2P-P3-N03 | `backend/app/services/s2p_context_builder.py` | 202 | `build_supplier_context` | 6 — Dead flag | Supplier enrichment failure sets `metadata.enrichment_available=False`, but no current production consumer reads the metadata flag. |
| S2P-P3-N04 | `backend/app/framework/checkpoint.py` | 74 | `list_checkpoints` | 6 — Dead flag | The framework endpoint exposes `data_available`, but the current S2P frontend has no consumer, so checkpoint outages are only observable to direct API clients. |
| S2P-P3-N05 | `backend/app/framework/intervention_controls.py` | 411 | `get_current_state` / `get_intervention_history` | 6 — Dead flag | `graph_available` and history `data_available` correctly describe failures, but neither is read by the current S2P frontend. |

### P4 — Cosmetic / Style

| ID | File | Line | Function | Pattern | Description |
|----|------|------|----------|---------|-------------|
| S2P-P4-N01 | `backend/app/framework/composite_gate.py` | 61 | `evaluate` | 5 — Wrong reason on fail-closed | A history-query exception produces a maturity-gate reason rather than an infrastructure reason, but no production call site invokes this evaluator; current imports use class constants only. |
| S2P-P4-N02 | `backend/app/routers/s2p_preview.py` | 309 | `_preview_queue_limit` | 1 — Collapsed error states | Any query-parameter access/conversion exception silently uses the default limit. This changes pagination only and does not misstate business data. |
| S2P-P4-N03 | `backend/app/routers/s2p_audit_export.py` | 95 | `_get_conservation_status` | 1 — Collapsed error states | Broad exceptions become an explicit `UNKNOWN` conservation phase; the user-facing state remains truthful, leaving only an overly broad catch and reduced diagnostic precision. |

## Totals

| Severity | Count |
|----------|-------|
| P2       | 17 |
| P3       | 5 |
| P4       | 3 |
| **Total**| 25 |
