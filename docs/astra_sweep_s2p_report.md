# ASTRA-SWEEP-S2P — SS-16–SS-18 and FIX-S2P

Review date: 2026-09-27 21:58:30 UTC

Verdict: **FINDINGS — 2 P1, 5 P2, 1 P3.**

This was a review-only run. No application code, tests, configuration, scoring logic, or learning gates were edited. The review used working-tree diffs, caller/response-model tracing, an AST scan of broad exception handlers, existing tests, and process-local fault probes. The probes used isolated in-memory stores or patched dependencies; they did not contact or mutate a live graph.

## Scope and provenance

The requested prompt descriptions do not match the repository's campaign record. The authoritative campaign entries are in `backend/docs/session_state.md`:

| Record | Actual recorded scope |
| --- | --- |
| SS-16 | P1-143/144/145 and P2-052/053: `routers/s2p.py`, `models/responses.py`, `models/outcome_receipt.py`, score/link tests |
| SS-17 | P1-135/136/137/139/140 and P2-048: `framework/checkpoint.py`, `framework/intervention_controls.py`, `routers/framework_router.py`, `routers/s2p_audit_export.py`, intervention/export tests |
| SS-18 | P1-141/142/146/147/148/149/150: evidence/situation/enrichment routers, centroid explorer, context builder, enrichment service, situation pattern, and their tests |
| FIX-S2P | Scaffold readiness double and the 29 individually listed DIAG-S2P type errors |

The L5 P2-049/050/051 persistence fixes are recorded earlier under C5-SILENT-SUB in the root session history. They were reviewed because the request explicitly named them. `framework/composite_gate.py`, `framework/audit.py`, and `framework/decision_history.py` have no current SS diff. The factor implementation is `app/domains/s2p/factors.py`; the requested `services/factors.py`, `services/learning.py`, `services/display.py`, and `services/investigation.py` do not exist in this checkout.

The existing working tree also contains materializer and supplier-preview work. Those changes were not automatically attributed to SS. Each finding below states whether it is introduced/incompletely addressed by SS or an inherited remnant in a reviewed file. The FIX-S2P session entry records one Category A replacement in the scaffold test, zero Category B removals, and ten Category C files; the prompt's “zero A/B” description differs from that record.

## P1 — Correctness

### F01 — Advisory link reads can abort an already committed learn

- FILE: `backend/app/routers/s2p.py:1984`, `:2755`, `:2936`
- FUNCTION: `_learn_with_audit`, `learn_decision`, `record_outcome`
- SEVERITY: P1
- ISSUE: New invoice-link verification reads can raise after learning commits, interrupting audit completion and subsequent persistence.
- DETAIL: `_learn_with_audit` calls the raw store's `get_decision_links` after `_learn_with_scorer`, catching only `GraphUnavailableError`. The AGE implementation calls its query runner directly; backend exceptions need not be wrapped in that router-specific error. Both HTTP handlers repeat the read without any exception handling. A failure therefore escapes after the authoritative learn, potentially before `record_outcome`/`finish_outcome`, proposal resolution, L5 hooks, and response construction. A read-only process-local fault probe produced `RuntimeError` with `learning_ran=True`, `audit_recorded=False`, and `audit_finished=False`. Preserve the authoritative mutation/audit boundary and report this advisory failure through `invoice_link_failed=true`; do not perform unguarded duplicate reads afterward.
- SS-PROMPT: SS-16.
- COVERAGE: The existing link-failure test fails writes, not post-commit reads, and checks only that `invoice_link_failed` is a bool. Cover both outcome routes, raw backend errors, and the case where the second verification read fails after the first succeeds.

### F02 — Degraded audit export removes existing nested fields

- FILE: `backend/app/routers/s2p_audit_export.py:180`, `:299`
- FUNCTION: `_factor_analysis`, `_export_payload`
- SEVERITY: P1
- ISSUE: Fingerprint failure now replaces the whole factor-analysis structure with `{}`, removing its existing array and independently available evidence.
- DETAIL: Before SS-17, a failed `fingerprint()` left `fingerprint={}` but still returned `factor_names` as a list and computed `factor_averages` from the already-read decisions. The new early `None` return and handler conversion to `{}` remove all three keys. Thus `factor_analysis.factor_names` becomes absent instead of retaining an array; valid factor averages disappear too. The separate `factor_analysis_available=false` flag does not preserve the existing nested response contract. Keep the established nested fields with their types, preserve independently computable values, and flag the unavailable fingerprint separately. No current frontend crash was reproduced; this is a demonstrated response-contract regression.
- SS-PROMPT: SS-17.
- COVERAGE: `test_audit_export_marks_failed_iks_and_factor_analysis` mocks the whole helper to return `None` and explicitly expects `{}`. It does not inject the fingerprint failure or verify the pre-existing nested fields.

## P2 — Observability

### F03 — Enrichment alerts still turn graph outage into “zero alerts”

- FILE: `backend/app/services/s2p_enrichment.py:224`, `:238`, `:259`; `backend/app/routers/s2p_enrichment.py:67`
- FUNCTION: `list_suppliers`, `summary`, `alerts`, `enrichment_alerts`
- SEVERITY: P2
- ISSUE: The alerts caller does not opt into the new availability result.
- DETAIL: `alerts()` invokes `summary()` with default `return_status=False`. A failed `list_entity_enrichments` still becomes `[]`; `EnrichmentAlertsResponse` has no availability field. A fault probe using an InMemoryGraphStore subclass returned exactly `{"alerts": [], "total_alerts": 0, "threshold": 0.1}` after the underlying read raised. Propagate unavailable state through the entire alerts path, retaining list/numeric defaults at the HTTP boundary. Prefer an unconditional failure-aware internal contract so callers cannot accidentally discard status.
- SS-PROMPT: SS-18; incomplete propagation through a changed service.
- COVERAGE: The new enrichment test covers `read_supplier(return_status=True)` and `summary(return_status=True)` only, not the alerts HTTP response.

### F04 — Conservation snapshot can claim availability after a failed state read

- FILE: `backend/app/routers/s2p.py:1126`, `:1448`
- FUNCTION: `_current_conservation_status`, `_receipt_conservation_snapshot`
- SEVERITY: P2
- ISSUE: The new availability flag misses errors already swallowed by the status helper.
- DETAIL: `_current_conservation_status` catches a failed conservation-count read and returns `"UNKNOWN"`. If the subsequent verified-count read succeeds, `_receipt_conservation_snapshot` returns `conservation_snapshot_available=true`. A probe failing `_cached_conservation_counts` while allowing counts returned `{"state":"UNKNOWN","verified_count":5,"conservation_snapshot_available":true}`. That value is propagated into outcome receipts. Preserve the failure status from the inner helper and mark the snapshot unavailable when either required read fails; keep successful numeric data typed.
- SS-PROMPT: SS-16.
- COVERAGE: The added test raises directly from the status helper, bypassing the actual catch-and-return behavior. Test a failure underneath `_current_conservation_status` with the second read successful.

### F05 — Compliance still hides extinction-history outages

- FILE: `backend/app/routers/s2p_evidence.py:563`
- FUNCTION: `compliance_payload` (formerly the inline `compliance` body)
- SEVERITY: P2
- ISSUE: Failed extinction-history reads yield normal empty arrays with a successful provenance label.
- DETAIL: The broad exception handler sets `extinction_classes=[]`, reused for `extinct_classes` and `queue_extinctions`, while retaining `extinction_source="persisted_verified_outcome_transitions"` and exposing no failure flag. A probe raising from `extinction_history` confirmed those values. Preserve the valid invoice-derived compliance metrics and the arrays, but separately mark extinction evidence unavailable.
- SS-PROMPT: SS-18/FIX-S2P reviewed file; this catch is a pre-existing remnant, not introduced by SS. The materializer extraction also predates this review.
- COVERAGE: No outage contract assertion for these three fields was found in the campaign additions.

### F06 — Failed DK reads are presented as normal learning state

- FILE: `backend/app/services/centroid_explorer.py:145`, `:276`, `:546`; `backend/app/routers/s2p_situation.py:233`
- FUNCTION: `S2PCentroidExplorerService.explain_decision`, `_call_or_none`, `explain_decision`; situation `_dk_weights`
- SEVERITY: P2
- ISSUE: DK-weight read failures remain indistinguishable from weights not yet learned.
- DETAIL: The explorer helper catches any exception and returns `None`; the explanation uses uniform display weights and `dk_status="learning"`. The new P39 flag only describes supplier enrichment, so it does not reveal the DK outage. A probe with working centroids and a failing `get_dk_weights` returned `dk_status="learning"` with no graph/degraded flag. The situation router's `_dk_weights` similarly maps an exception to the same `None` used for genuine missing weights. Distinguish missing from unavailable and propagate a separate availability flag while preserving display field types.
- SS-PROMPT: SS-18 reviewed files; pre-existing remnants outside the narrower P39/entity-context edits.
- COVERAGE: The new P39 test fails supplier-enrichment reads, not DK reads.

### F07 — Score degraded-fields list omits other caught enrichment failures

- FILE: `backend/app/routers/s2p.py:2504`, `:2524`
- FUNCTION: `score_procurement_event`
- SEVERITY: P2
- ISSUE: Auto-approve and threshold enrichment exceptions can still produce an empty `degraded_fields` list.
- DETAIL: The new list records active-variant, prior-count, and process-context failures. The nearby catches for `_should_auto_approve` and `compute_threshold_decision` still substitute `None` without appending their field names. When these alone fail, the response gives no degraded indication for those sections. Record the affected fields without changing the selected action, gate behavior, or the existing optional response types.
- SS-PROMPT: SS-16; pre-existing catch branches left uncovered by the new response-level metadata.
- COVERAGE: `test_score_enrichment_failures_are_reported_without_changing_action` exercises only the three currently named fields.

## P3 — Maintenance

### F08 — Proposal-store type mismatch was suppressed with Any

- FILE: `backend/app/main.py:412`; `backend/app/services/compounding_ledger.py:45`
- FUNCTION: Application `CompoundingLedger` construction; `CompoundingLedger.__init__`
- SEVERITY: P3
- ISSUE: FIX-S2P removes a diagnostic by casting the argument to `Any` rather than expressing the accepted store interface.
- DETAIL: The ledger still declares `proposal_store: ProposalStore`, while startup supplies a `GraphProposalStore`. `cast(Any, app.state.proposal_store)` disables the compatibility check. Introduce a shared protocol or accurately type the supported implementations so future incompatible stores are rejected statically. No runtime incompatibility was demonstrated.
- SS-PROMPT: FIX-S2P, DIAG-S2P error 24.

## Critical checks and test coverage

- **L5 conservation:** Failed metric computation, missing category coverage, failed prior-state read, and failed write return `False`. Both learn handlers include `persistence.conservation_l5` and report `status="partial"` when persistence is incomplete. A failed prior-state read does not write a falsely current snapshot. Learning has already occurred at this stage; a partial response reports that fact rather than rolling it back.
- **L5 centroid/DK:** Failed reads/writes report false persistence flags. Centroid persistence remains restricted to mean convergence. The shared DK persistence function raises on write failure, which the router converts to `False`. Existing mutation locking and authoritative checkpoint/write paths were not weakened by the reviewed SS edits. F01 can nevertheless interrupt the subsequent hooks.
- **Required factors:** `compute_all_factors` still raises when production context is absent and required inputs are missing. The context-read failure still reaches that abstention check. No SS diff changes this function or fabricates scores on that path.
- **S2P categories / I-9:** Composite thresholds use `price_variance`, `quantity_mismatch`, `duplicate_risk`, `contract_gap`, and `format_compliance`. Decision history is domain-scoped to `s2p`; its graph-query exception is re-raised. The legacy composite wrapper still substitutes conservative maturity defaults if that exception reaches it; this is unchanged, and no application call site to that legacy evaluator was found in the reviewed tree.
- **Context bridge / locking:** `_ContextualRewardFunction` is already removed. Main constructs the scorer with `S2PGradedRewardFunction`; `tests/test_context_bridge_removed.py` explicitly checks removal. The SS link wrapper retains its existing RLock and restores the original method in `finally`. No bridge/threading.Lock interaction remains to backport.
- **Scaffold health:** `HealthAGEGraphStore` inherits the AGE store type and implements the actual `count_decisions` probe. With the explicit AGE/`soc_graph` health configuration, connected=True returns zero decisions successfully and connected=False raises ConnectionError. The parametrized test explicitly requires 200/ready and 503/unavailable for both `/health` and `/api/health`. It is a narrow connection-free test double; the production readiness check is unchanged.
- **Type-fix quality:** Reviewed the 29-error catalog against the changed declarations and call sites. Import redefinitions use TYPE_CHECKING branches; repeated optional dictionary access is narrowed through locals; reward conversion returns float; collections have concrete annotations; missing decisions are rejected before dereferencing. Materializer/JSON boundaries use concrete casts. No new `# type: ignore` was found, and new availability fields are bool rather than Optional[bool]. Error 24 is suppressed with Any as detailed in F08.

| Fixed area | Existing degraded-path coverage and limitations |
| --- | --- |
| Graph context and score enrichment | Score endpoint tests cover context failures and named enrichment fields; F07's other catches remain unasserted. |
| Receipt conservation | Asserts typed empty state, zero count, false flag when the outer dependency raises; misses the inner swallowed failure in F04. |
| Score/learn invoice links | Score test checks false persistence; outcome test verifies learning continues but asserts only the flag's boolean type. No post-learn read-failure coverage (F01). |
| Checkpoint list; intervention state/history/log write | Helper tests cover typed empty list/None plus availability and successful in-memory control state plus failed audit persistence. Added tests do not exercise both list HTTP serialization boundaries. |
| Audit IKS/factor analysis | One HTTP test mocks both helpers to None. It bypasses actual trajectory/fingerprint exceptions and encodes the nested-field regression in F02. |
| Trust explanation | New test verifies graph_available=false at helper level; it does not assert all existing response field types through HTTP. |
| Situation entity lookup / receipt context | Helper distinguishes absent versus failed query; traversal verifies receipt degraded-field names. No added end-to-end failure response/type assertion. |
| P39 evidence / supplier context enrichment | Tests cover empty P39 dict plus false availability, and context metadata availability. They do not cover DK outage (F06). Context metadata is preserved by the invoice traversal's metadata merge. |
| Supplier enrichment reads/list | Service test verifies absent versus failed supplier read and empty summary plus false availability. It does not verify degraded supplier/summary HTTP serialization or alerts propagation (F03). |
| Scaffold | Both connected and disconnected cases are asserted for both health routes. |

Consequently, the checklist requirement that every fixed path be covered through its response boundary, including both typed defaults and flags, is **not fully met**. These are coverage gaps, not a claim that every untested path is broken.

## Additional exception-scan observations

Availability is still opt-in in several internal helpers: checkpoint listing, intervention history, supplier reads/lists/summary, entity-context lookup, and receipt-context lookup retain their legacy default-return behavior unless a caller requests status. The reviewed checkpoint/history HTTP callers correctly opt in; F03 demonstrates a caller that does not.

The evidence audit-pack helper `_conservation_snapshot` also retains generic exception-to-`{}` branches, although its normal `GraphUnavailableError` path is explicitly re-raised and converted to HTTP 503. Malformed intervention-details JSON is still normalized to an empty dict without a field-specific flag. These are inherited defensive branches; no new graph-outage regression is attributed to them.

The broad exception scan also found safe failure signaling: centroid history uses `supported=false` and a reason; context-builder centroid failures append warnings; audit verification returns `verified=false/reason=verification_unavailable`; intervention writes return `audit_persisted=false`. These should not be treated as silent substitutions merely because they catch Exception.


### DIAG-S2P type-error disposition

| Catalog entries | Review result |
| --- | --- |
| 1–2 | TYPE_CHECKING import selection removes static duplicate definitions while preserving runtime import alternatives. |
| 3–9 | Evidence-provider dictionary narrowing and concrete casts match the checked branches and copied data. |
| 10 | Reward is explicitly converted to float. |
| 11–13 | Test gate dictionaries and sets now have concrete element types. |
| 14–22 | Materialized/JSON payloads are cast to declared dictionaries/lists; snapshot collections are annotated and returned as lists. These casts assert trusted internal schemas rather than validate arbitrary data. |
| 23 | An explicit missing-decision check narrows the optional value before use. |
| 24 | Any suppresses the proposal-store interface error; see F08. |
| 25 | The materialized conservation result is cast to the declared response dictionary type. |
| 26–27 | Test-store decision IDs are returned as strings, consistent with the GraphStore contract. |
| 28 | A concrete Request cast adapts an app-state-only test double; it adds no runtime validation, but the tested helper only accesses that supplied state. |
| 29 | The known fixture JSON section is cast to the declared dictionary result. |


## Validation

Executed from `s2p-copilot/backend` against the existing working tree, without source/test changes:

| Command | Result |
| --- | --- |
| `python -m pytest tests/ -q --timeout=120` | **2,118 passed, 0 failed, 0 skipped**, 4,242 warnings, 439.45 seconds; exit 0; no collection errors |
| `python -m mypy app/ --config-file pyproject.toml` | **FAIL**, exit 1: one external dependency diagnostic; checked 138 source files |
| `python -m mypy app/ --config-file pyproject.toml --follow-imports=skip` | **PASS**, exit 0: no issues in 138 source files; supplemental application-only check |

The exact mypy failure was:

```text
.../python_expts_venv/Lib/site-packages/psycopg/_cmodule.py:1:
error: disable_error_code: Invalid error code(s): import-not-found [misc]
Found 1 error in 1 file (checked 138 source files)
```

This is the same external inline-configuration diagnostic already recorded by FIX-S2P. The requested command did not pass; the supplemental result must not be substituted for it. No application diagnostics were reported by the requested run.

Backend counts exactly match the supplied 2,118/0/0 baseline. **No test-count regression or new validation failure was observed.** Passing tests do not resolve the review findings, which concern untested failure paths and a response contract encoded incorrectly by an existing test.

Logs:

- [Full backend pytest output](../.codex_tmp/astra_sweep_pytest.txt)
- [Requested mypy output](../.codex_tmp/astra_sweep_mypy.txt)
- [Supplemental application-only mypy output](../.codex_tmp/astra_sweep_mypy_app_only.txt)

## Final verdict

**FINDINGS: 2 P1, 5 P2, 1 P3.** Correct the post-commit advisory-read behavior and restore the nested audit-export contract first. Follow with complete outage propagation and the proposal-store interface type. This review made no fixes.

