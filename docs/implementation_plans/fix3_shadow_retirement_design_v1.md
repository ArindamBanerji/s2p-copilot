# Fix 3 — S2P Shadow Retirement and Shared `soc_graph`

## §1 Executive Summary

The current S2P shadow path is a second AGE store, not a second scorer. `main.py`
initializes the production scorer from the active graph store, then derives an
`enrichment_store` from `primary`/`_primary` and can replace
`app.state.graph_store` with that SQLite object (`s2p-copilot/backend/app/main.py:160-183`).
It also initializes `s2p_shadow` without a shared store (`main.py:196-199`). When
enabled, `s2p_shadow.py` creates another AGE store from shadow-specific DSN and
graph settings (`s2p_shadow.py:223-250`). This is the remaining S2P split.

The selected design is **lifecycle labels in the one shared graph**:

* `app.state.scorer.graph_store`, `app.state.graph_store`, the S2P reader, and
  enrichment all reference the same object (`main.py:162-170`).
* Shadow hooks remain available for comparison diagnostics, but receive that
  same store. No shadow-specific AGE connection or graph is constructed.
* Shadow Decisions use deterministic shadow IDs and metadata
  `lifecycle="shadow"`, `production_decision_id`, and the existing shadow
  operation fields. Their outcomes use the corresponding shadow ID.
* Shadow configuration remains an opt-in diagnostics switch. Legacy
  `S2P_AGE_DSN`/`S2P_AGE_GRAPH` values are accepted only as inert compatibility
  metadata in isolated tests; they never create a store.
* The active graph guard no longer treats `S2P_SHADOW_AGE=1` as a conflict,
  because the flag now enables lifecycle-labelled work in the already-selected
  graph (`s2p_graph_status.py:181-201`, `335-365`).
* SQLite remains valid for test/development profiles. It is not selected as an
  enrichment substitute for a different production scorer store.

This preserves S2P domain isolation (`domain="s2p"`), scoring profiles,
enrichment schemas, and disposable test graphs while removing the physical
shadow graph.

## §2 Shadow System Inventory

### §2.1 Configuration and graph construction

`S2PShadowConfig` defaults to disabled and has separate `dsn` and `graph`
fields (`s2p-copilot/backend/app/s2p_shadow.py:58-65`). The environment parser
reads `S2P_SHADOW_AGE`, `S2P_SHADOW_STRICT`, `S2P_AGE_TEST_MODE`, and legacy
`S2P_AGE_DSN`/`S2P_AGE_GRAPH` (`s2p_shadow.py:68-75`). With production
environment access and no legacy overrides, it loads `GraphConfig.load("s2p")`
and takes the configured DSN, graph, and domain (`s2p_shadow.py:82-95`).

The current validator requires a DSN and graph when enabled and explicitly
rejects `soc_graph` (`s2p_shadow.py:113-130`). The separate store factory calls
`create_graph_store(backend="age", domain=config.domain, dsn=config.dsn,
graph_name=config.graph, ...)` (`s2p_shadow.py:223-235`), and
`initialize_s2p_shadow_state()` invokes that factory whenever the flag is enabled
(`s2p_shadow.py:238-250`). Thus the current shadow target is a separate AGE
graph, configured independently from the active store.

### §2.2 Shadow scoring and comparison

There is no shadow `CompoundingScorer` construction in `s2p_shadow.py`; the
module owns configuration, diagnostics, and an optional store
(`s2p_shadow.py:143-148`, `164-220`). The actual hooks are in `s2p.py`:

* `_record_score_shadow()` writes a governed Decision to `shadow.store` with
  shadow metadata (`s2p-copilot/backend/app/routers/s2p.py:325-397`).
* `_record_outcome_shadow()` writes the outcome to the shadow store and records
  parity diagnostics (`s2p.py:399-463`).
* The score hook is submitted after the normal score flow (`s2p.py:1989-2005`);
  the outcome hooks run after normal learning/recording (`s2p.py:2164-2173`,
  `2289-2301`).

The current implementation reuses the production decision ID in the separate
shadow graph. Once both records share one graph, that would collide with the
authoritative Decision. The implementation therefore assigns a deterministic
shadow ID and retains the production ID as metadata.

### §2.3 Lifecycle and status

Shadow is opt-in through `S2P_SHADOW_AGE`; strict failure behavior is controlled
by `S2P_SHADOW_STRICT` (`s2p_shadow.py:68-72`). The active graph configuration
currently rejects the flag during AGE validation (`s2p_graph_status.py:181-193`)
and again before active store construction (`s2p_graph_status.py:335-346`).
Graph status exposes shadow diagnostics through `_shadow_summary()` and
`build_s2p_graph_status()` (`s2p_graph_status.py:368-449`). Those diagnostics
remain, but `shadow.store` will be the shared store rather than a second store.

### §2.4 Shadow data and lifecycle labels

Current shadow writes use metadata such as `shadow=True`, `shadow_run_id`, and
`shadow_operation` (`s2p.py:364-374`, `428-440`). They do not have a canonical
`lifecycle` field. The shared-graph implementation adds `lifecycle="shadow"`
and `production_decision_id` to every shadow Decision/outcome metadata payload.
Shadow IDs remain distinct from production IDs, preventing accidental
replacement while allowing same-graph census and scoped diagnostics.

## §3 Enrichment Split

### §3.1 Startup ownership

Startup builds the scorer with the active store (`main.py:160-167`) and creates
the S2P reader over the scorer store (`main.py:168-170`). It then selects
`scorer.graph_store.primary` or `_primary` as `enrichment_store`
(`main.py:172-178`). In dual-write mode it replaces `app.state.graph_store`
with that primary (`main.py:179-183`). This means scorer access can remain on a
DualWriteStore while routes resolving `app.state.graph_store` read the SQLite
primary.

### §3.2 Enrichment consumers

The enrichment routers resolve `request.app.state.graph_store` and construct
`S2PSupplierEnrichmentService` with that store (`s2p-copilot/backend/app/routers/s2p_enrichment.py:90-105`).
The service reads and writes GraphStore entity-enrichment records
(`app/services/s2p_enrichment.py:211-230`, `285-335`). Situation enrichment
also reads/writes entity enrichment through its injected GraphStore
(`app/services/situation_graph_enrichment.py:104-124`). Context, traversal,
centroid, and supplier-intelligence services consume the same enrichment API
(`s2p_context_builder.py:235-243`, `situation_traversals.py:527-546`,
`centroid_explorer.py:202-206`, `supplier_intelligence.py:363-371`).

Because these consumers use `app.state.graph_store`, the reassignment at
`main.py:179-183` can make them unable to see AGE-backed scorer writes. The fix
sets `app.state.enrichment_store = app.state.graph_store` and removes the
reassignment branch. Response models and service APIs do not change.

## §4 Callers and Consumers

### §4.1 Production references

The shadow construction/import references are:

| File:line | Reference | Classification |
|---|---|---|
| `backend/app/main.py:66` | imports `initialize_s2p_shadow_state` | startup |
| `backend/app/main.py:198` | initializes `app.state.s2p_shadow` | startup |
| `backend/app/routers/s2p.py:59` | imports `S2PShadowState` | route helper type |
| `backend/app/routers/s2p.py:293-296` | reads `app.state.s2p_shadow` | route helper |
| `backend/app/routers/s2p.py:325-397` | score shadow write | side effect |
| `backend/app/routers/s2p.py:399-463` | outcome shadow write | side effect |
| `backend/app/routers/s2p.py:1997-2005` | submits score shadow hook | caller |
| `backend/app/routers/s2p.py:2164-2172` | submits learn shadow hook | caller |
| `backend/app/routers/s2p.py:2289-2301` | submits outcome shadow hook | caller |
| `backend/app/s2p_graph_status.py:190-193` | rejects shadow with active AGE | guard |
| `backend/app/s2p_graph_status.py:342-345` | rejects shadow before store construction | guard |
| `backend/app/s2p_graph_status.py:368-449` | reports shadow status | diagnostics |

The evolution `shadow_runner`/`S2PEvolutionService` is a separate in-memory
variant evaluator, not the AGE shadow store (`app/domains/s2p/evolution/service.py:17-109`).
It is outside this physical-graph retirement and remains unchanged.

### §4.2 Test references

The shared AGE fixture creates both active and shadow graphs
(`backend/tests/conftest.py:68-89`) and drops both (`conftest.py:91-98`).
The main shadow test files are:

* `tests/test_s2p_shadow_phase1.py:32-238` — configuration, diagnostics, and
  disabled-startup checks; it currently asserts DSN/graph requirements and the
  `soc_graph` rejection.
* `tests/test_s2p_shadow_live_age.py:31-55` — constructs a shadow store and
  separately rebuilds the scorer; its score/learn/outcome assertions read
  `shadow.store` (`:107-129`, `:144-228`).
* `tests/test_s2p_preview.py:504-526` — injects a `S2PShadowState` for preview
  behavior.
* Active AGE tests initialize disabled shadow state for app setup
  (`tests/test_s2p_active_age_live.py:17-58`,
  `test_s2p_active_age_parallel.py:18-49`,
  `test_s2p_active_age_phase_b.py:21-49`, `:330-334`).

Non-Decision shadow-related routes and evolution tests use their own stateful
services (`routers/s2p_auto_approve.py:1-114`,
`tests/test_s2p_auto_approve_gate.py:95-490`) and are not physical AGE shadow
graph users. They must remain behaviorally unchanged.

## §5 Option A Design

### §5.1 Shared store and lifecycle labels

`initialize_s2p_shadow_state()` gains a `store` argument. It parses the opt-in
diagnostic configuration but never calls a graph factory. When enabled, its
state points to the store supplied by startup; when disabled, `store` remains
`None` for compatibility with existing disabled-state tests.

`main.py` passes `app.state.scorer.graph_store` into shadow initialization. The
same object remains in `app.state.graph_store` and `app.state.enrichment_store`.

`s2p.py` derives a shadow ID from the production decision ID, writes lifecycle
metadata on shadow records, and uses that shadow ID for the shadow outcome.
The authoritative production record is untouched. Shadow diagnostics retain
the original operation names and parity fields.

### §5.2 Configuration

`S2P_SHADOW_AGE` remains an opt-in lifecycle-comparison flag. `S2P_SHADOW_STRICT`
continues to control whether shadow failures surface as HTTP 502. The legacy
DSN/graph variables no longer control a connection and are retained only for
test/config compatibility summaries; production GraphConfig remains the sole
graph resolver (`main.py:115-126`). No new graph-name or DSN path is added.

### §5.3 Enrichment

Remove the `primary`/`_primary` lookup and the dual-write reassignment block.
Set `app.state.enrichment_store = app.state.graph_store` as an explicit identity
alias. All enrichment routes continue resolving `app.state.graph_store`.

### §5.4 Preserved behavior

The following must not change: `domain="s2p"`; scorer profile and reward
behavior; active/test SQLite selection; S2P reader API and response schemas;
shadow diagnostics/status schemas; non-Decision shadow/evolution services; and
AGE-safe query rules. The only intentionally changed behavior is elimination of
the second physical shadow graph and SQLite enrichment substitution.

## §6 Blast Radius

| Change | Files affected | Tests affected | Risk |
|---|---|---|---|
| Pass shared store to shadow state | `app/main.py`, `app/s2p_shadow.py` | startup, shadow, active AGE tests | stale test setup or missing store |
| Remove separate shadow factory/rejection | `app/s2p_shadow.py`, `app/s2p_graph_status.py` | `test_s2p_shadow_phase1.py`, graph status tests | old config assertions |
| Add deterministic lifecycle-labelled shadow IDs | `app/routers/s2p.py` | `test_s2p_shadow_live_age.py`, triage/score tests | same-graph ID collision or outcome lookup |
| Align enrichment identity | `app/main.py` | enrichment, startup, situation tests | consumers relying on SQLite `.primary` |
| Remove shadow graph fixture | `tests/conftest.py` | live AGE shadow fixture tests | cleanup and graph isolation |
| Add new regression coverage | `tests/test_shadow_retirement.py` | new file only | fixture/app global state isolation |

## §7 Design Decisions

**DD1 — Lifecycle labels, not total removal.** Keep the shadow diagnostics and
comparison hooks because they are exercised by S2P live AGE tests and provide a
useful non-authoritative comparison. Retire only the separate graph/store.

**DD2 — Enrichment store is the scorer store object.** This removes the observed
split and makes identity testable. `app.state.enrichment_store` is an alias, not
a second wrapper or primary.

**DD3 — Shadow remains disposable behavior, but in the selected graph.** The
flag remains opt-in and strict mode remains available. Shadow records are
non-authoritative via lifecycle metadata and deterministic distinct IDs.

**DD4 — Legacy shadow DSN/graph variables become inert compatibility inputs.**
They are not used to construct stores. GraphConfig/active startup remains the
only production graph authority.

**DD5 — Keep DualWriteStore for explicitly configured local/test profiles.**
This fix does not remove the factory’s local/test capability. It removes only
the S2P startup code that exposes its primary as a different enrichment store.

## §8 Implementation Plan

1. **Update `app/s2p_shadow.py`.** Read `store` as an optional initializer
   argument, remove DSN/graph-required validation and the `soc_graph` rejection,
   remove `_default_shadow_store_factory` use, and return the supplied shared
   store only when enabled. Keep config redaction/diagnostics APIs.
2. **Align `app/main.py`.** Keep the scorer-created store in
   `app.state.graph_store`; assign the same object to `app.state.enrichment_store`;
   remove `.primary` lookup and reassignment; pass the scorer store into shadow
   initialization.
3. **Update `app/s2p_graph_status.py`.** Remove the active-AGE conflict checks;
   report `shadow_allowed` according to the opt-in lifecycle mode rather than a
   second-graph prohibition. Preserve active backend and graph reporting.
4. **Update `app/routers/s2p.py`.** Add a deterministic shadow ID helper and
   lifecycle metadata. Use the shadow ID in governed-decision and outcome writes;
   retain production ID in metadata and diagnostics.
5. **Update AGE test fixture and affected tests.** Create only one disposable
   AGE graph, pass the active store into shadow state, and replace assertions
   about a second graph with same-store/lifecycle assertions. Preserve all
   non-shadow and test-profile coverage.
6. **Add `tests/test_shadow_retirement.py`.** Cover shared identity, no second
   store, `soc_graph` acceptance, enrichment visibility, SQLite regression, and
   lifecycle-labelled shadow records. Use real InMemory/SQLite stores and no
   mocks/monkeypatches.

Verification after each changed file: run mypy on that file, then its targeted
tests. Final commands:

```powershell
cd s2p-copilot/backend
python -m pytest tests/test_shadow_retirement.py -v --timeout=60
python -m pytest tests/ -q --timeout=300
cd ../../copilot-sdk
python -m pytest tests/ -q --timeout=120 -k "s2p"
python -m pytest tests/graph/test_domain_required_conformance.py -v --timeout=30
```

## §9 Risk Analysis

| Risk | What could go wrong | Mitigation |
|---|---|---|
| Shadow ID collision | Same-graph shadow write overwrites/conflicts with production Decision | deterministic distinct ID plus `production_decision_id` metadata |
| Shadow records pollute authoritative counts | Generic store readers may see lifecycle-labelled records | keep shadow records distinguishable, verify production IDs/read paths, and add lifecycle assertions; follow-up may add protocol lifecycle filters if census shows pollution |
| Enrichment remains split | A hidden `.primary` consumer bypasses the alias | scan all `primary`, `enrichment_store`, and `app.state.graph_store` references; identity test |
| Legacy env still creates AGE | A stale factory call remains | remove factory from shadow initializer and assert no shadow-specific graph construction |
| Existing shadow tests expect two graphs | Tests fail or leak graph resources | update fixture to one graph and preserve shared-store setup |
| Active AGE guard remains | `S2P_SHADOW_AGE=1` fails before shared-store initialization | remove both guards in `s2p_graph_status.py` and test coexistence |
| SQLite regression | Test/development scorer no longer receives expected store | leave `build_s2p_scorer()` profile selection and factory paths unchanged; add SQLite regression test |
| Strict diagnostics semantics change | Shadow error handling becomes silent | preserve `S2P_SHADOW_STRICT`, diagnostic status, and HTTP 502 behavior |

## §10 Reading Log

Fully read:

* `copilot-sdk/docs/implementation_plans/jm_gap_closure_plan_v1.md`
* `copilot-sdk/docs/design/jm_implementation_review_part1a_v1.md`
* `copilot-sdk/docs/design/jm_implementation_review_part2b_v1.md`
* `s2p-copilot/CLAUDE.md`
* `copilot-sdk/CLAUDE.md`
* `s2p-copilot/backend/app/s2p_shadow.py`
* `s2p-copilot/backend/app/main.py`
* `s2p-copilot/backend/app/s2p_graph_status.py`
* `s2p-copilot/backend/app/routers/s2p.py`
* `s2p-copilot/backend/app/routers/s2p_enrichment.py`
* `s2p-copilot/backend/app/routers/s2p_enrichment_context.py`
* `s2p-copilot/backend/app/services/s2p_enrichment.py`
* `s2p-copilot/backend/app/services/situation_graph_enrichment.py`
* `s2p-copilot/backend/app/services/situation_traversals.py`
* `s2p-copilot/backend/app/services/centroid_explorer.py`
* `s2p-copilot/backend/app/services/supplier_intelligence.py`
* `s2p-copilot/backend/app/domains/s2p/evolution/service.py`
* `s2p-copilot/backend/tests/conftest.py`
* `s2p-copilot/backend/tests/test_s2p_shadow_phase1.py`
* `s2p-copilot/backend/tests/test_s2p_shadow_live_age.py`
* `s2p-copilot/backend/tests/test_s2p_preview.py`
* `s2p-copilot/backend/tests/test_s2p_active_age_live.py`
* `s2p-copilot/backend/tests/test_s2p_active_age_parallel.py`
* `s2p-copilot/backend/tests/test_s2p_active_age_phase_b.py`
* `s2p-copilot/backend/tests/test_s2p_graph_status_phase_a.py`
* `s2p-copilot/backend/tests/test_s2p_product_like_phase_c2.py`
* `s2p-copilot/backend/tests/test_s2p_auto_approve_gate.py`

DESIGN_READY: YES
