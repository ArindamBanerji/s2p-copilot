# S2P Domain-Bound Graph Facade Design

Status: design only; no implementation is included in this document.

## 1. Executive summary

The original inventory recorded 44 Decision-access points across the requested routers and services. A re-scan of the current `backend/app/` tree found additional callers and counts that the original table did not model consistently: 52 concrete GraphStore Decision-method access sites, including `cohort_status.py`, `s2p_auto_approve_gate.py`, the performance count helpers, and both count methods used by audit export. The migration table below retains the original numbered inventory and adds a reconciliation note for those later-discovered sites. Twenty-five of the original points use optional-method discovery (getattr/hasattr) or signature probing with TypeError. This makes a missing or legacy method look like an empty graph, and several branches retry without a domain. On the shared soc_graph, that is both a correctness risk and a cross-copilot isolation risk.

The proposed solution is S2PGraphReader: a small domain-bound facade constructed around the application GraphStore. It binds domain="s2p" once, exposes only the Decision reads S2P actually needs, delegates through one canonical signature, and converts graph failures into a named `GraphUnavailableError` with the original exception chained. Callers no longer discover methods dynamically or retry without a domain.

The facade is not a fallback store and must not hide a missing GraphStore. It is a typed boundary around the already-selected active AGE-backed store. Test-mode stores implement the same contract and remain useful for unit tests.

## 2. Facade interface

### 2.1 Class and invariants

~~~
class GraphUnavailableError(RuntimeError):
    """A canonical graph-read failure for S2P router boundaries."""


class S2PGraphReader:
    def __init__(self, store: GraphStore, domain: str = "s2p") -> None:
        if domain != "s2p":
            raise ValueError("S2PGraphReader only supports domain='s2p'")
        self.store = store
        self.domain = domain
~~~

The constructor should validate the domain rather than silently permitting a reader configured for another copilot. The underlying store remains responsible for validating its own domain literals and graph configuration.

### 2.2 Methods

The eleven methods below are derived from the calls found in the requested files plus the re-scan of the whole S2P application tree. Each method injects the bound domain; callers do not supply it.

| Method | Typed signature | Delegation target | Domain injection | Failure behavior |
|---|---|---|---|---|
| get_decision | `get_decision(decision_id: str) -> dict[str, Any] | None` | `store.get_decision(decision_id, domain=self.domain)` | Keyword s2p | Catch `Exception` and raise `GraphUnavailableError(... ) from exc`; `None` means valid not-found |
| get_decisions | `get_decisions(category: str | None = None, limit: int = 400) -> list[dict[str, Any]]` | `store.get_decisions(self.domain, category=category, limit=limit)` | Positional/keyword canonical domain | Wrap graph exceptions; empty list means successful empty query |
| get_all_decisions | `get_all_decisions() -> list[dict[str, Any]]` | `store.get_all_decisions(self.domain)` | Canonical domain | Wrap graph exceptions; empty list means successful empty query |
| get_verified_decisions | `get_verified_decisions() -> list[dict[str, Any]]` | `store.get_verified_decisions(self.domain)` | Canonical domain | Wrap graph exceptions; empty list means successful empty query |
| count_verified | `count_verified() -> int` | `store.count_verified(self.domain)` | Canonical domain | Wrap graph exceptions; never turn failure into zero |
| count_verified_decisions | `count_verified_decisions() -> int` | `store.count_verified_decisions(self.domain)` | Canonical domain | Wrap graph exceptions; never turn failure into zero |
| count_correct | `count_correct() -> int` | `store.count_correct(self.domain)` | Canonical domain | Wrap graph exceptions; never turn failure into zero |
| count_decisions | `count_decisions() -> int` | `store.count_decisions(self.domain)` | Canonical domain | Wrap graph exceptions; never turn failure into zero |
| count_recommended_action | `count_recommended_action(action: str) -> int` | `store.count_recommended_action(self.domain, action)` | Canonical domain | Wrap graph exceptions; no SQLite/empty fallback in the facade |
| get_decision_links | `get_decision_links(decision_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]` | `store.get_decision_links(decision_id=..., domain=self.domain, limit=limit)` | Keyword s2p | Wrap graph exceptions; empty list means successful empty query |
| query_context | `query_context(entity_id: str, max_depth: int = 2) -> list[dict[str, Any]]` | `store.query_context(entity_id, max_depth, domain=self.domain)` | Keyword s2p | Wrap graph exceptions; router maps to HTTP 503 |

The facade's exception wrapper is intentionally narrow and centralized:

~~~
def _read(self, operation: str, call: Callable[[], T]) -> T:
    try:
        return call()
    except GraphUnavailableError:
        raise
    except Exception as exc:
        raise GraphUnavailableError(f"S2P graph read failed: {operation}") from exc
~~~

The implementation should use this helper for all eleven reads. A successful query returning no rows is not an error and remains an empty list/`None`; only an exception is wrapped.

`get_decision_links` currently exposes an API mismatch: the AGE store accepts decision_id and domain, while S2P code also attempts limit. The final decision is to add `limit: int | None = None` consistently to the protocol, AGE store, adapter, SQLite store, and in-memory store. The facade must use that canonical signature and must not emulate signature probing.

The facade should return ordinary dictionaries and lists, preserving the existing GraphStore result shape. It should not add metadata, silently filter rows in Python, or infer a domain from a store attribute.

## 3. Facade wiring

main.py currently creates the active graph store through create_s2p_active_graph_store, assigns the scorer, and exposes app.state.graph_store at main.py:159-178. In dual-write mode, that state is later redirected to the SQLite enrichment primary at :174-178, while scorer writes retain the DualWriteStore. A Decision reader must not accidentally bind to that enrichment-only redirect.

### Proposed wiring

1. Construct the facade immediately after the canonical active Decision store is created. Bind it to `app.state.scorer.graph_store`, not to `app.state.graph_store`:

   ~~~
   app.state.s2p_graph_reader = S2PGraphReader(
       store=app.state.scorer.graph_store,
       domain="s2p",
   )
   ~~~

2. Keep app.state.scorer.graph_store as the canonical Decision store. Preserve app.state.enrichment_store for the explicitly non-Decision enrichment path.

3. During migration, routers may obtain the reader through a small accessor:

   ~~~
   def get_s2p_graph_reader(request: Request) -> S2PGraphReader:
       reader = getattr(request.app.state, "s2p_graph_reader", None)
       if reader is None:
           raise HTTPException(503, "S2P graph reader unavailable")
       return reader
   ~~~

   This accessor is only a state lookup; it must not use getattr to discover graph methods. The state lookup may use `getattr` because it is not a Decision-method compatibility shim.

4. Services should receive the reader explicitly in constructors or function parameters. For transitional call sites that only receive a scorer, add a reader property to the app-owned service object rather than reaching through scorer.graph_store.

5. Backward compatibility is limited to construction and test fixtures: existing stores remain valid because the facade calls their canonical domain-aware methods. There is no backward-compatible no-domain retry in production.

## 4. Caller migration table

The following table covers the 44 Decision access points identified in the requested files. Current pattern describes the existing call or method-discovery branch; new pattern is the facade call.

### routers/s2p.py

| # | File:line | Current pattern | New pattern | Risk |
|---:|---|---|---|---|
| 1 | routers/s2p.py:113 | Direct query_context(..., domain="s2p") | reader.query_context(invoice_id, 2) | Low; same domain |
| 2 | routers/s2p.py:773 | getattr graph link lookup | reader.get_decision_links(...) | Medium; preserve link semantics |
| 3 | routers/s2p.py:791 | Second optional link lookup | reader.get_decision_links(...) | Medium; remove duplicate compatibility path |
| 4 | routers/s2p.py:824 | Optional count_verified(selected_domain) | reader.count_verified() | Low |
| 5 | routers/s2p.py:829 | Optional count_verified_decisions(selected_domain) | reader.count_verified_decisions() | Low |
| 6 | routers/s2p.py:1563 | Direct scoped get_decision | reader.get_decision(decision_id) | Low |
| 7 | routers/s2p.py:1615 | Direct scoped lookup | reader.get_decision(request.decision_id) | Low |
| 8 | routers/s2p.py:2064 | Direct scoped lookup | reader.get_decision(request.decision_id) | Low |
| 9 | routers/s2p.py:2179 | Scoped lookup but exception becomes None | reader.get_decision(request.decision_id); map failure to 503 | Medium; outcome behavior changes |

### Other routers

| # | File:line | Current pattern | New pattern | Risk |
|---:|---|---|---|---|
| 10 | routers/s2p_evidence.py:166 | getattr then get_all_decisions(domain) | reader.get_all_decisions() | Low |
| 11 | routers/s2p_evidence.py:224 | Optional get_decision_links() with no domain | reader.get_decision_links() | Medium; requires adapter fix |
| 12 | routers/s2p_evidence.py:243 | Optional scoped get_decision | reader.get_decision(decision_id) | Low |
| 13 | routers/s2p_evidence.py:261 | Direct get_all_decisions(domain="s2p") | reader.get_all_decisions() | Low |
| 14 | routers/s2p_explorer.py:177 | Optional count_verified(domain) | reader.count_verified() | Low |
| 15 | routers/s2p_explorer.py:180 | Optional count_verified_decisions(domain) | reader.count_verified_decisions() | Low |
| 16 | routers/s2p_explorer.py:335 | get_decision(invoice_id) with no domain | reader.get_decision(invoice_id) | High; closes cross-domain read |
| 17 | routers/s2p_explorer.py:342 | get_all_decisions(_graph_domain(...)) | reader.get_all_decisions() | Low |
| 18 | routers/s2p_audit_export.py:61 | Optional scoped get_all_decisions | reader.get_all_decisions() | Low |
| 19 | routers/s2p_audit_export.py:103 | _call_count with no-arg retry | reader.count_verified() | Medium; removes legacy-double compatibility |
| 20 | routers/s2p_audit_export.py:281 | Same _call_count fallback | reader.count_verified() | Medium |
| 21 | routers/s2p_governance.py:34 | Optional get_all_decisions(_graph_domain) | reader.get_all_decisions() | Low |
| 22 | routers/s2p_performance.py:73 | _safe_call count_verified(domain) | reader.count_verified() | Medium; graph errors become explicit |
| 23 | routers/s2p_performance.py:87 | _safe_call get_all_decisions(domain) | reader.get_all_decisions() | Medium |
| 24 | routers/s2p_performance.py:115 | _safe_call get_all_decisions(domain) | reader.get_all_decisions() | Medium |
| 25 | routers/s2p_performance.py:126 | Indirect _count_verified | reader.count_verified() | Low |
| 26 | routers/s2p_performance.py:133 | _safe_call get_verified_decisions(domain) | reader.get_verified_decisions() | Medium |
| 27 | routers/s2p_performance.py:146 | Indirect _count_verified | reader.count_verified() | Low |
| 28 | routers/s2p_performance.py:208 | Indirect _count_verified | reader.count_verified() | Low |
| 29 | routers/s2p_performance.py:220 | Indirect _count_verified | reader.count_verified() | Low |
| 30 | routers/s2p_situation.py:131 | Optional scoped get_decision | reader.get_decision(decision_id) | Low |
| 31 | routers/financial_router.py:100 | Scoped call, then no-domain TypeError retry | reader.get_all_decisions() | High; removes cross-domain fallback |
| 32 | routers/factor_proposer_router.py:89 | Optional get_all_decisions(_graph_domain) | reader.get_all_decisions() | Low |

### Services

| # | File:line | Current pattern | New pattern | Risk |
|---:|---|---|---|---|
| 33 | services/situation_traversals.py:423 | Optional query_context(..., domain="s2p") | reader.query_context(entity_id, max_depth) | Low |
| 34 | services/situation_traversals.py:642 | Optional scoped get_decision | reader.get_decision(decision_id) | Low |
| 35 | services/s2p_situation_pattern.py:130 | Direct scoped get_decision | reader.get_decision(decision_id) | Low |
| 36 | services/s2p_situation_pattern.py:138 | Direct scoped get_all_decisions | reader.get_all_decisions() | Low |
| 37 | services/s2p_situation_pattern.py:155 | Optional link and Decision readers | reader.get_decision_links() | Medium |
| 38 | services/s2p_situation_pattern.py:165 | Optional scoped linked Decision read | reader.get_decision(linked_id) | Low |
| 39 | services/s2p_context_builder.py:528 | Optional scoped get_decision | reader.get_decision(decision_id) | Low |
| 40 | services/s2p_context_builder.py:598 | get_decisions signature probing | reader.get_decisions(category, limit) | High; removes legacy signatures |
| 41 | services/s2p_context_builder.py:609 | get_all_decisions scoped then unscoped retry | reader.get_all_decisions() | High |
| 42 | services/s2p_enrichment.py:353,362,370 | Optional verified/all/legacy get_decisions with domain | Reader methods | Medium; exceptions no longer become empty enrichment |
| 43 | services/supplier_intelligence.py:377 | hasattr plus get_verified_decisions("s2p") | reader.get_verified_decisions() | Medium |
| 44 | services/centroid_explorer.py:189 and situation_graph_enrichment.py:194 | Unscoped get_decision through getattr | reader.get_decision(decision_id) | High; closes two direct leaks |

The final row contains two access points because both have the same unscoped pattern and should migrate together. The corresponding link-reader call at situation_graph_enrichment.py:183 is also migrated in that unit.

### 4.1 Reconciliation against the current tree

The original 44-point count was not a stable source-level count. The current scan found these omitted concrete GraphStore accesses:

| File:line | Omitted access | Migration consequence |
|---|---|---|
| routers/s2p_audit_export.py:104 | `count_correct` through `_call_count` | Replace with `reader.count_correct()`; no zero/default on failure |
| routers/s2p_audit_export.py:282 | second `count_correct` through `_call_count` | Same replacement |
| routers/s2p_performance.py:77 | `_count_correct` helper | Delegate helper to `reader.count_correct()` |
| routers/s2p_performance.py:84 | `count_decisions(domain)` | Add `reader.count_decisions()` |
| routers/s2p_performance.py:95 | `count_recommended_action(domain, action)` | Add `reader.count_recommended_action(action)` |
| services/situation_graph_enrichment.py:183 | `get_decision_links(limit=1000)` | Add `reader.get_decision_links(limit=1000)` |
| services/s2p_auto_approve_gate.py:400 | `get_verified_decisions(domain)` | Add `reader.get_verified_decisions()` |
| services/cohort_status.py:153-160 | dynamic verified/all/category read | Migrate to `reader.get_verified_decisions()`, `reader.get_all_decisions()`, or `reader.get_decisions(limit=10000)` |

The current source therefore has 52 concrete Decision-method access sites under the counting convention used here. The numbered table is a migration-unit inventory; the reconciliation table prevents the omitted sites from being treated as out of scope. `framework/audit.py` and `s2p_audit_export.py`'s `audit.get_decisions()` calls remain outside the facade because they read the separate audit ledger.

## 5. Adapter changes

### AGE adapter

ci_platform/graph/age_sdk_adapter.py:463-464 currently exposes a link method with only decision_id. It must accept and forward domain, and the final canonical API must also forward limit:

~~~
def get_decision_links(
    self,
    decision_id: str | None = None,
    domain: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return self._store.get_decision_links(
        decision_id=decision_id,
        domain=domain,
        limit=limit,
    )
~~~

The AGE GraphStore, SQLite store, and in-memory store must accept the same signature. SQLite/in-memory currently bind an instance domain internally; their new optional domain argument must validate/filter against that instance domain rather than broaden the result set. The S2P facade passes domain="s2p" and does not expose a caller-controlled domain.

### Protocol additions

copilot_sdk/graph/protocol.py already defines the main Decision reads and counts at :40-69, and GraphTraversalStore.query_context at :140-145. It does not define `get_decision_links`. Add it to `GraphTraversalStore` with `get_decision_links(decision_id: str | None = None, domain: str | None = None, limit: int | None = None) -> list[dict[str, Any]]`. Add the same limit-bearing method to AGE, SQLite, and in-memory implementations and to the adapter. `count_recommended_action(domain, action)` is used by the performance route but is not currently in the shared protocol; add it to the GraphStore contract and each implementation, or explicitly make it a facade-owned aggregate over a canonical domain-scoped read before migration. The protocol must not leave either operation to runtime TypeError probing.

The facade is the stronger S2P contract: its public methods do not expose a domain argument. Shared protocol methods remain domain-aware because Trading, Purchasing, DataOps, SOC, and S2P share infrastructure.

## 6. Test double changes

### Existing doubles requiring the link signature update

The current test scan identifies these implementations:

| File:line | Existing method | Required update |
|---|---|---|
| backend/tests/test_graph_links.py:201 | get_decision_links(self, decision_id=None) | Add domain and limit parameters |
| backend/tests/test_s2p_active_age_phase_b.py:279 | get_decision_links(self, decision_id=None) | Add domain and limit parameters |
| backend/tests/test_s2p_situation_pattern.py:43 | get_decision_links(self, decision_id=None) | Add domain and limit parameters |

The 22 previously updated doubles already accept domain for get_decision, get_all_decisions, get_verified_decisions, and query_context. The tests also contain domain-aware count doubles, for example test_l5_conservation_s2p_hook.py:24-32 and test_s2p_active_age_phase_b.py:217-220. The three link doubles above must additionally accept `limit`; any new facade double must implement stateful filtering rather than merely accepting and ignoring domain.

### New facade tests

Add unit tests for:

1. Every facade method delegates with exactly domain="s2p".
2. A store exception propagates unchanged or is wrapped in a named facade exception.
3. None/empty results remain distinguishable from exceptions.
4. A constructor domain other than "s2p" is rejected.
5. query_context and get_decision_links forward depth/limit correctly.

Add integration tests using one complete stateful test double:

1. Seed both SOC and S2P Decisions with identical IDs/categories.
2. Verify every facade read returns only S2P data.
3. Verify link traversal returns only S2P links.
4. Verify an AGE adapter facade and an in-memory facade produce the same result shape.
5. Verify graph failures reach router-level 503 responses rather than empty authoritative payloads.

## 7. Migration strategy

### Phase 1 — Build and wire

- Add the facade module and unit tests.
- Add the app-state reader in main.py after the canonical scorer graph store is created.
- Keep existing routers unchanged.
- Run the S2P suite and shared SDK/CI graph tests.

Rollback: remove the app-state reader construction and facade module; no caller behavior has changed.

### Phase 2 — Migrate file by file

Recommended order:

1. Wire the facade and migrate `s2p_explorer.py`, `centroid_explorer.py`, and `situation_graph_enrichment.py` first. They contain direct unscoped reads and are the highest isolation risk.
2. Migrate `s2p_evidence.py`, `s2p_situation.py`, `situation_traversals.py`, and `s2p.py`, preserving their explicit graph error boundaries.
3. Migrate `s2p_context_builder.py` and `financial_router.py`, removing signature probing and no-domain retries.
4. Migrate performance, governance, audit export, factor proposer, enrichment, supplier intelligence, auto-approve, and cohort-status services. Audit export's ledger calls remain separate.

Run the owning test file after each file, then the complete S2P suite. Rollback is a per-file revert of imports/call substitutions; the facade remains unused and harmless.

### Phase 3 — Remove compatibility code

- Delete Decision-method getattr/hasattr branches.
- Delete all no-domain TypeError retries.
- Delete fallback-to-empty behavior caused only by missing graph methods.
- Preserve explicit cold-start behavior where a successful query returns no rows.

Rollback: temporarily restore the previous caller implementation only in a branch, never by reintroducing an unscoped production retry.

### Phase 4 — Enforce

- Add an AST test over backend/app rejecting getattr/hasattr for the listed Decision methods.
- Reject zero-argument calls to get_all_decisions, get_decisions, and get_verified_decisions outside the facade/tests.
- Extend E3 scanner rules to recognize facade calls as domain-bound and to flag direct get_decision calls without an explicit domain/facade.
- Add the Rule #72 test requirement to the S2P engineering rules.

Rollback: enforcement begins in report-only mode for one suite, then becomes a gate after all findings are resolved.

## 8. Failure mode analysis

| Current path | Current fallback | Post-change behavior | Endpoint impact | Frontend handling |
|---|---|---|---|---|
| s2p.py:2179-2182 outcome lookup | decision=None; request category reused | Graph exception becomes 503 | /api/s2p/outcome may return 503 | Show graph unavailable/retry; do not display completed outcome |
| s2p_situation_pattern.py:133-140 | None and later metadata/fixture paths | Raise service graph error; empty only for valid no rows | Situation enrichment returns 503/unavailable | Distinguish no evidence from unavailable graph |
| s2p_situation_pattern.py:154-167 | Link/Decision failure becomes empty list | Raise for graph failure | Evidence enrichment may return 503 | Distinguish no links from unavailable graph |
| s2p_context_builder.py:596-615 | Signature probing, then empty rows | One canonical facade call; propagate failure | Context-dependent score/preview returns 503 | Existing score error handling displays retry/unavailable |
| financial_router.py:98-105 | Retries get_all_decisions() without domain | One scoped facade call | Financial analysis returns explicit graph error | Show unavailable analysis |
| s2p_performance.py:45-51 | Any graph exception becomes 0/[] | Facade exception reaches router boundary | Performance metrics may become 503 | Avoid presenting zero as measured performance |
| s2p_enrichment.py:352-375 | AGE error becomes empty enrichment | Explicit unavailable/error receipt | Supplier enrichment reports unavailable | Preserve provenance and warning |
| supplier_intelligence.py:374-385 | AGE error becomes no verified decisions | Explicit unavailable supplier evidence | Supplier intelligence is blocked | Show blocked readiness |
| centroid_explorer.py:187-192 | Unscoped result or None | S2P-scoped lookup; graph failure propagates | Explanation returns 503, not cross-domain data | Do not display a cross-domain explanation |
| situation_graph_enrichment.py:182-196 | Link signature retry and unscoped Decision read | Canonical scoped link/Decision calls | Enrichment returns explicit failure | Do not claim graph-backed enrichment |
| s2p_audit_export.py:103-104,281-282 | Count helper returns `None`/zero after graph failure | Facade count methods propagate to 503 | Audit export conservation summary is unavailable | Render unavailable, not zero |
| s2p_performance.py:72-115,132-134 | `_safe_call`/backend fallback turns failed counts and reads into 0/[] | Facade metrics propagate `GraphUnavailableError` to 503 | Performance endpoints fail closed | Show retry/unavailable state |
| cohort_status.py:148-163 | Missing method or exception falls through to another method/empty list | Reader supplies one canonical read and propagates failure | Cohort status is unavailable on graph failure | Show unavailable status |
| s2p_auto_approve_gate.py:392-403 | Missing/error verified read becomes empty rows with warnings | Reader failure blocks readiness and reaches explicit route handling | Auto-approve readiness is blocked, not zero-data ready | Preserve blocked/unavailable warning |

Frontend behavior should be checked for each route before enabling the fail-loud phase. A 503 is preferable to a successful response that presents zero or cross-domain data as authoritative. The migration must explicitly audit the performance, evidence, financial, enrichment, auto-approve, and cohort-status consumers because their current helpers still convert graph failure to empty/default values.

## 9. Enforcement

### AST enforcement

Add an S2P production AST test that rejects:

- getattr/hasattr where the string argument is any Decision method.
- Calls to get_decision, get_all_decisions, get_decisions, get_verified_decisions, count_verified, or count_verified_decisions outside S2PGraphReader unless the call is through the facade.
- get_decision_links calls without the facade.
- Zero-argument calls to domain-required shared methods.
- except TypeError blocks whose body retries one of the Decision methods.

### E3 scanner

Extend E3 with a semantic allowlist for the facade itself:

- Facade delegation methods are classified as DOMAIN_BOUND only when their body supplies self.domain.
- Callers using reader.<decision_method> are classified as scoped.
- Direct store method calls remain violations unless they include the required domain.
- Test and migration code remains separately reported.

### Rule #72

Rule #72 should require complete stateful test doubles for the facade contract. A double that accepts a domain but returns cross-domain rows is incomplete; it must store domain-stamped Decisions and filter/link them from its own state. Monkeypatching a missing facade method is prohibited.

### Additional implementation risks

- Startup ordering: construct the reader only after `app.state.scorer.graph_store` is final; do not bind before profile-aware store creation.
- Test isolation: each test app must receive a fresh reader around its injected store, and disposable AGE fixtures must not leak the production `soc_graph`.
- Concurrent access: the reader is stateless apart from its store reference; store thread/connection safety remains the store's responsibility.
- Runtime replacement: if the scorer store is replaced during a test or reload, rebuild the reader with it; do not mutate the reader's domain or store behind its back.
- Import cycles: keep `S2PGraphReader` in a small module depending only on the graph protocol and error type; routers/services should depend on the facade module, not on `main.py`.
- Failure conversion: catch only at the facade boundary, chain the original exception, and let routers map `GraphUnavailableError` to 503. Do not wrap successful empty results.

## 10. Decisions — all resolved

1. **DECIDED — Dual-write bind target.** Bind to `app.state.scorer.graph_store`, not `app.state.graph_store`. `main.py:159-178` can redirect the latter to the enrichment primary in dual-write mode; the scorer store is the canonical Decision store. Keep the enrichment store separately named.

2. **DECIDED — `get_decision_links` limit.** Add `limit: int | None = None` to every implementation: `GraphTraversalStore`, AGE GraphStore, AGE adapter, SQLite store, and in-memory store. The canonical signature also carries optional `domain` everywhere; instance-bound stores validate that the supplied domain is their own domain. A single canonical signature eliminates signature probing and TypeError retries.

3. **DECIDED — Facade exception type.** Define `GraphUnavailableError` and raise it with the original graph exception chained (`raise ... from exc`). Routers catch this one type and map it to 503; services propagate it without catching. The original exception remains available as `__cause__` for debugging.

4. **DECIDED — Cold-start semantics.** Enumerate before changing callers. An empty list from a successful zero-row query is valid cold-start data; an empty list produced after catching a graph exception is failure. The facade propagates/wraps exceptions and returns empty lists only when the underlying query successfully returns no rows.

5. **DECIDED — Performance endpoints.** Return 503 on graph failure. Never return numeric zero as a measured value when measurement failed. This applies to the performance helpers and any route that consumes them.

6. **DECIDED — Audit ledger.** Audit ledger reads remain separate from `S2PGraphReader`. The facade covers GraphStore Decision queries only; audit/evidence ledger queries use their own contract and data source. Graph-backed evidence queries do migrate to the facade.

7. **DECIDED — Other S2P routers.** Scan all remaining `backend/app/` files before enforcement. The 17 requested files are the known inventory, not a permanent allowlist; the current scan already found `cohort_status.py` and `s2p_auto_approve_gate.py` callers that must be included.

8. **DECIDED — Protocol ownership.** Add `get_decision_links(decision_id=None, domain=None, limit=None) -> list[dict[str, Any]]` to `GraphTraversalStore`. Link traversal belongs with `query_context`; it is separate from core GraphStore CRUD and is shared infrastructure with a domain-aware contract.
