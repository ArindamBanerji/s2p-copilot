# S2P Playwright Brittleness Diagnostic

Date: 2026-08-11

Scope: diagnostic only. No S2P source or test files were changed. The only
filesystem write made for this investigation is this document. The supplied
live timing procedure invoked score and learn POST endpoints to obtain valid
decision IDs and measure the learn path, so it created diagnostic records and
an outcome; no schema/DDL/reset operation was executed.

## Executive diagnosis

The primary failure is the S2P situation graph query, not Playwright
concurrency:

```cypher
MATCH p = (e {entity_id: 'S2P-INV-0003'})-[*1..3]-(n)
WHERE n.domain = 's2p'
RETURN p
LIMIT 100
```

The query has three problematic properties:

1. The starting vertex is unlabelled, so AGE cannot narrow the scan to an
   `Entity` (or `Invoice`) relation.
2. The variable-length traversal is undirected and can expand through the
   shared graph.
3. The domain predicate applies only to the destination node `n`; it does not
   constrain the starting vertex `e` or the traversed path.

Live evidence supports this diagnosis. The active graph is `soc_graph`, with
48,025 vertices and 17,526 edges. Its `Entity` relation contains only two
rows, neither of which is an S2P invoice, and it has only its primary-key
index—no `entity_id` property index. The S2P status endpoint also reports that
historical SQLite records are not visible in AGE-active mode and that migration
is incomplete.

The first cold situation request took 25.97–28.40 seconds in three trials. The
same decision’s subsequent depth-2 and depth-3 requests took 0.43–0.60 seconds,
which indicates cache/plan/warm-state sensitivity rather than predictable
depth-linear cost. A 120-second AGE session statement timeout and a frontend
`fetch` with no AbortController timeout allow the cold request to outlive the
Playwright 20-second situation assertion and the 30-second learn-response
waits.

The `OID 0` relation error is a secondary database/catalog failure signal. It
was not reproduced deterministically in this read-only probe, and the source
contains no OID-specific recovery. It should be investigated after removing
the pathological query shape, because a timeout/cancelled AGE query can leave
the next request exposed to the same backend session or catalog state.

## 1. Full situation request chain

```text
Browser scores invoice
  └─ TriageScreen.handleScore()
       └─ scoreInvoice() -> POST /api/s2p/score
            └─ S2P score router computes/persists Decision
                 └─ response.decision_id

SituationPanel effect observes decision_id
  └─ fetchSituation(decision_id, maxDepth=3)
       └─ GET /api/s2p/situation/{decision_id}?max_depth=3
            └─ app/routers/s2p_situation.py:get_situation()
                 ├─ S2PGraphReader.get_decision()
                 └─ SituationAnalyzer.analyze_intent() in a worker thread
                      └─ S2PTraversalPatternBase.traverse()
                           └─ _query_graph_context()
                                └─ S2PGraphReader.query_context()
                                     └─ GraphStore.query_context()
                                          └─ AGEGraphStore.query_context()
                                               └─ AGEGraphStore._run_query()
                                                    └─ AGEClient.run_query()
                                                         └─ asyncio.to_thread()
                                                              └─ _sync_execute()
                                                                   └─ pool.connection()
                                                                        └─ PostgreSQL/AGE
```

### Router and context construction

`s2p_situation.py:27-122` defines `GET /api/s2p/situation/{decision_id}`.
The handler bounds `max_depth` to 0–3 at lines 168–173, obtains the shared
graph store at lines 35–40, reads the Decision through `_decision()` at
lines 146–150, and calls `SituationAnalyzer.analyze_intent()` in
`asyncio.to_thread()` at lines 74–77.

The intent scope includes the invoice identifier from Decision metadata or
`entity_id` at `s2p_situation.py:59-72`. The handler does not itself build the
Cypher; it passes the intent to the category traversal.

`situation_traversals.py:69-119` constructs the category-specific situation
context. At lines 81–83 it calls `_query_graph_context()` before rendering the
category nodes. `_query_graph_context()` at lines 425–437 delegates to
`S2PGraphReader.query_context()` and rejects foreign-domain rows.

### Graph reader and generated Cypher

`s2p_graph_reader.py:118-130` delegates `query_context(entity_id, max_depth,
domain="s2p")` to the GraphStore.

`age_graph_store.py:3557-3578` clamps the hop count, validates the domain, and
generates:

```cypher
MATCH p = (e {entity_id: '<escaped entity_id>'})-[*1..<hop_count>]-(n)
WHERE n.domain = 's2p'
RETURN p
LIMIT 100
```

The request depth is transformed by `situation_traversals.py:82`:

| HTTP `max_depth` | Graph `hops` |
|---:|---:|
| 1 | 2 |
| 2 | 3 |
| 3 | 3 |

The source uses string-literal escaping through the AGE store’s `_S()` helper;
there are no bound Cypher parameters for this query. The returned paths are
converted to node dictionaries at `age_graph_store.py:3578` and then used as
context/enrichment by the S2P traversal.

### AGE client and pool

`s2p-copilot/backend/app/s2p_graph_status.py:331-367` creates one active
`S2PActiveAGEGraphStore` for the S2P process. `s2p-copilot/backend/app/main.py:171-194`
stores it on the FastAPI app and binds the same store to the scorer and graph
reader.

The adapter constructs `AGEGraphStore` at
`ci-platform/ci_platform/graph/age_sdk_adapter.py:11-25`. The AGE client uses
the shared pool implementation in `age_client.py:184-214`; the current
defaults are `AGE_POOL_MIN_SIZE=3` and `AGE_POOL_MAX_SIZE=15`, with
`AGE_USE_POOL` selecting pooled mode. `demo.py:120-140` supplies
`AGE_USE_POOL=true`, `AGE_POOL_MAX_SIZE=15`, the DSN, and `soc_graph` to each
copilot process.

Each backend process owns a separate pool, but all pools share PostgreSQL’s
connection budget. This is not the immediate cause of the single-worker
situation stall, although a pool wait can amplify the same symptoms under a
full demo run.

## 2. Learn/outcome chain

The S2P-specific SDK-shaped endpoint is
`s2p-copilot/backend/app/routers/s2p.py:2218-2331`:

```text
POST /api/learn
  └─ validate action/reason code
  └─ get scorer + S2PGraphReader
  └─ mutation lock: get Decision
  └─ read centroid and conservation snapshot
  └─ append evidence receipt
  └─ _learn_with_scorer()
       └─ reader.get_decision()
       └─ scorer.learn()
            ├─ graph get_decision()
            ├─ conflict/conservation checks
            ├─ graph write_outcome()
            ├─ evidence/fingerprint/centroid persistence
            ├─ DK refresh
            └─ optional invoice link
  └─ persist L5 centroid/conservation/DK state
  └─ receipts, supplier profile, evolver, shadow bookkeeping
```

The shared SDK router has the same core sequence at
`copilot-sdk/copilot_sdk/backend/scoring_router.py:203-291`.

The learn path does not call `get_situation()`, `query_context()`, or
`SituationAnalyzer`. Therefore the situation request is not a direct learn
dependency. Learn does perform multiple AGE reads/writes and takes a mutation
lock, so it can still be delayed by the same database/session failures or by
another long-running AGE query.

The `POST /api/s2p/outcome` route follows a parallel governed outcome path in
`s2p.py:2334` onward; it also uses scorer and graph persistence rather than
refreshing situation context.

## 3. Timing measurements

Measurements were made against the live S2P backend on port 8002. The initial
probe using the task’s invoice IDs directly returned 404 because the route
parameter is a Decision ID, not an invoice ID. This is consistent with
`s2p_situation.py:27-44`, which looks up a Decision before traversing.

### Score and situation

Valid score requests used the S2P `ScoreRequest` fields from
`s2p.py:1946-1967` (`event_id`, `amount`, `supplier_id`, and the S2P factor
fields). Scores were 0.35–0.65 seconds in the measured trials.

For three fresh Decisions for `S2P-INV-0003`:

| Graph hop mode | First decision | Second decision | Third decision |
|---:|---:|---:|---:|
| Situation `max_depth=1` → 2 hops | 25.97s | 28.36s | 28.40s |
| Situation `max_depth=2` → 3 hops | 0.60s | 0.46s | 0.45s |
| Situation `max_depth=3` → 3 hops | 0.48s | 0.44s | 0.43s |

All these situation calls returned HTTP 200. The depth-1 cold latency is the
important result: it is already beyond the test’s 20-second “Analyzing
situation” assertion even though it eventually succeeds. The difference is
not evidence that depth 3 is safe; the same query family can still hit the
database timeout or OID error under a different cache/session state.

### Learn

One fresh score followed by a valid learn request returned:

| Operation | Result | Elapsed |
|---|---:|---:|
| `POST /api/s2p/score` | 200 | 0.35s |
| `POST /api/learn` | 200 | 3.80s |

The successful learn returned `outcome=applied` semantics, with
`centroid_delta=0.01118`. This is below the 30-second Playwright wait in the
healthy case but leaves limited margin when AGE reads, mutation-lock wait,
catalog invalidation, or persistence retries occur.

## 4. PostgreSQL and AGE measurements

Read-only SQL measurements used the live DSN (`soc_copilot`, PostgreSQL on
port 5433) and `soc_graph`.

| Setting/metric | Observed value | Evidence/interpretation |
|---|---:|---|
| PostgreSQL `max_connections` | 100 | Shared by all copilot processes |
| `superuser_reserved_connections` | 3 | Not available to ordinary pool traffic |
| PostgreSQL global `statement_timeout` | 0 | No global timeout |
| AGE client session timeout | 120s | `age_client.py:176-178` sets it on every configured connection |
| `shared_buffers` | 128MB | Small relative to the shared graph workload |
| Active `soc_copilot` sessions during probe | 42 | Pool/launcher/database activity already consumes a substantial portion of the 100-session budget |
| `soc_graph` vertex count | 48,025 | `MATCH (n) RETURN count(n)` |
| `soc_graph` edge count | 17,526 | `MATCH ()-[r]->() RETURN count(r)` |
| `soc_graph` graph OID | 16,994 | `ag_catalog.ag_graph` |
| AGE graph records in catalog | 105 | Many disposable/test graphs remain registered |

### Index evidence

The live `soc_graph."Entity"` relation contains two rows:

```text
TEST-AGE-ENT-001
TEST-AGE-ENT-TEV
```

There are no `S2P-INV-*` Entity rows in that relation. Its only reported index
is `Entity_pkey` on the internal vertex `id`. There is no live `entity_id`
property index on `soc_graph."Entity"`.

The repository’s invoice-index helper at
`s2p-copilot/backend/app/migration/s2p_entity_migration.py:484-502` explicitly
creates an index only on a disposable graph matching
`protocol_v2_test_s2p_*`. It rejects `soc_graph` at lines 620–623. Therefore
the existence of that helper is not evidence that the live product graph is
indexed.

The status endpoint reported:

- `active_backend=age`
- `active_graph_name=soc_graph`
- `age_active=true`
- `migration_complete=false`
- `historical_visibility=new_writes_only_history_not_migrated`
- historical SQLite records are not visible in AGE-active mode

This explains why an invoice identifier can be absent from the AGE graph even
though it exists in the S2P fixture/queue, and why the unlabelled graph lookup
is especially expensive.

## 5. Frontend behavior

`TriageScreen.tsx:165-172` passes the scored `decision_id` to `SituationPanel`.
`SituationPanel.tsx:49-81` starts one request whenever that ID changes:

1. Set `loading=true` and notify the parent.
2. Call `fetchSituation(decisionId)`.
3. On a resolved response, set data or mark an error.
4. Clear loading only in `.finally()` after the Promise settles.

The API wrapper at `apps/s2p/frontend/src/api.ts:65-82` uses raw `fetch` with
no AbortController, deadline, or retry. `fetchSituation()` at lines 217–221
converts both network errors and non-2xx responses, including 503, to `null`.
That means a fast 503 should eventually render “Situation analysis
unavailable,” but a slow backend response keeps the Promise pending and leaves
“Analyzing situation...” on screen indefinitely from the browser’s point of
view.

The panel renders this exact loading text at `SituationPanel.tsx:99-102`.
The Rule-vs-Reasoning panel separately renders “Loading reasoning...” while
the parent’s `situationLoading` flag is true at
`RuleVsReasoningPanel.tsx:80-93`.

There is no client retry or polling for situation. There is also no separate
request deadline for learn: `learnDecision()` calls the same unbounded
`apiPost()` and maps failure to `null` at `api.ts:190-192`.

The tests reflect these assumptions:

- `rule-vs-reasoning.spec.ts:28-33` waits up to 20 seconds for situation
  loading to clear.
- `rule-vs-reasoning.spec.ts:57-71` repeats the same loading assertion and
  accepts an unavailable state only after the request settles.
- `triage.spec.ts:29-35` and `shadow-smoke.spec.ts:33-39` wait up to 30 seconds
  for a successful learn/outcome response.
- The shadow test’s situation 503 is not retried by the client; it is reduced
  to `null` by `fetchSituation()`.

## 6. OID 0 error analysis

The observed error is:

```text
could not open relation with OID 0
```

The AGE client has no handling specific to OID, relation, or catalog errors.
`age_client.py:461-499` retries only the literal “Entity failed to be
updated” case. Other query exceptions are logged with the query and re-raised.
The client configures a 120-second session statement timeout at
`age_client.py:176-178`.

The evidence does not prove that `soc_graph` was recently dropped/recreated:

- The live graph exists with graph OID 16,994.
- The process status says product `soc_graph` is active and migration is not
  complete.
- The source forbids domain reset on `soc_graph` in
  `age_graph_store.py:3416-3450`.
- The catalog contains many disposable protocol/test graphs, but no live
  catalog timestamp was available from the read-only checks.

Most likely interpretation: the OID 0 error is a secondary AGE/PostgreSQL
catalog/session symptom exposed by a cancelled or otherwise invalidated query,
possibly aggravated by shared-graph activity. It should not be treated as the
primary cause until the unbounded situation query is removed from the hot path
and the error is reproduced with a minimal direct query. A concurrent DDL
operation remains a possible cause, but there is no evidence in this run that
the S2P product process performs DDL at request time.

## 7. Root-cause classification

| Class | Finding | Confidence |
|---|---|---|
| A. Query design | Confirmed primary. Unlabelled start, undirected variable-length traversal, destination-only domain filter, no live `entity_id` index, and missing S2P Entity rows. | High |
| B. PostgreSQL config | Contributing timeout boundary, but not “too low.” Global timeout is 0; AGE client sets 120s. That protects the DB poorly for a UI path and is far above PW waits. | High |
| C. AGE catalog/OID | Secondary symptom/hypothesis. OID 0 was observed in logs, but not reproduced deterministically and no request-time DDL was found. | Medium/low |
| D. Frontend handling | Confirmed brittleness. No fetch deadline, abort, retry, or explicit request-level timeout. A 503 settles; a slow backend does not. | High |
| E. Learn path | The learn path does not refresh situation context. It has its own multi-step AGE read/write/persistence chain; healthy measurement was 3.80s. | High |

## 8. Ranked recommendations

### P0 — Replace the situation hot query with a bounded, domain-scoped read

Effort: medium. Blast radius: S2P situation API and any consumers of
`GraphStore.query_context`.

Use a known label and direction where the S2P graph model guarantees one, and
query the canonical invoice/entity relation directly. Prefer a bounded list of
known context edges over an undirected variable-length path. The starting
entity must be domain-scoped, and the query must return a bounded number of
nodes/edges. If an invoice is absent from AGE, return a fast empty/degraded
context rather than traversing the entire shared graph.

This is the most important fix because it removes the 25–28 second cold path,
prevents statement-timeout cascades, and reduces exposure to catalog errors.

### P0 — Make missing AGE context an explicit fast degradation

Effort: low/medium. Blast radius: situation explanation semantics.

Treat “invoice/entity not found in AGE” as a valid unavailable-context result
with a warning and fixture/scorer context. Do not turn absence into an
unbounded exploratory traversal. The active status already documents that
historical SQLite records are not visible in AGE mode, so this behavior is
required for the current cutover state.

### P1 — Add an entity index only after the live data model is settled

Effort: low for DDL, medium for migration governance. Blast radius: live
`soc_graph` schema and shared copilot query performance.

If `Entity` remains the canonical start label, add the AGE property index on
the live graph through an approved migration. Do not copy the disposable-graph
helper blindly: it intentionally rejects `soc_graph`. First establish whether
S2P invoices should be `Entity`, `Invoice`, or another canonical label, then
index that property and verify with a live query plan/latency measurement.

### P1 — Add frontend request deadlines and cancellation

Effort: low. Blast radius: shared S2P API wrapper and situation/learn UX.

Give situation requests a deadline shorter than the Playwright assertion,
abort them on decision change/unmount, and settle to a visible unavailable
state. Apply the same pattern to learn/outcome requests with a user-visible
retry action. This does not fix the backend query, but it converts an infinite
spinner into deterministic degradation.

### P1 — Set a deliberate UI-facing AGE timeout

Effort: low/medium. Blast radius: all AGE queries using the shared client.

The current 120-second session timeout is not suitable for a request that the
UI waits on for 20–30 seconds. Prefer a route/query-specific deadline or a
bounded situation read; only then consider lowering the session timeout. A
global reduction without query redesign could break legitimate writes and
long-running maintenance operations.

### P2 — Investigate OID 0 with database telemetry and catalog checks

Effort: medium. Blast radius: AGE/PostgreSQL operations.

Capture backend PID, transaction state, query start, cancellation source, and
`pg_stat_activity` around the error. Audit for DDL against `soc_graph`, prepared
statement reuse, and pool connection reuse after cancellation. Reproduce a
minimal query in a fresh connection before changing catalog objects. Do not
drop/recreate the shared graph as a first response.

### P2 — Profile the learn chain separately

Effort: medium. Blast radius: `/api/learn` and `/api/s2p/outcome`.

Add timing instrumentation around decision lookup, conservation/conflict
reads, `scorer.learn()`, outcome write, centroid/DK persistence, receipts, and
post-lock bookkeeping. The current code proves learn does not call situation,
but it can still inherit AGE pool/catalog failures. Keep the mutation lock
visible in the timings.

## 9. Failure-to-fix mapping

| PW failure | Likely mechanism | P0 query/degrade | Frontend deadline | Learn profiling/OID work |
|---|---|---:|---:|---:|
| `rule-vs-reasoning.spec.ts:42` hard failure | Situation request remains pending beyond 20s | Resolves root latency | Makes failure deterministic/unavailable | Not primary |
| `rule-vs-reasoning.spec.ts:57` hard failure | Same pending situation request | Resolves root latency | Prevents infinite loading | Not primary |
| `rule-vs-reasoning.spec.ts:50` flaky | Cold/cache-sensitive situation latency | Removes variability | Handles residual 503/slow response | OID investigation if residual |
| `shadow-smoke.spec.ts:79` flaky 503 | Situation graph failure or timeout | Avoids pathological query | Settles 503 to explicit unavailable | OID work if 503 persists |
| `triage.spec.ts:115` learn timeout | AGE read/write/persistence delay, not situation refresh | Reduces shared AGE pressure | Adds learn deadline/retry UX | Directly profiles remaining cost |
| `triage.spec.ts:125` learn timeout | Same learn chain | Reduces upstream graph failures | Same | Same |
| `triage.spec.ts:139` learn timeout | Same learn chain followed by conservation UI | Reduces upstream graph failures | Same | Same |

## Bottom line

The evidence favors a query-design and data-model mismatch: S2P asks a shared
48k-node AGE graph to discover an invoice/entity that is not present in the
live graph, using an unlabelled, undirected variable-length traversal. The
first fix should make that lookup bounded and domain-specific; pool sizing or
raising Playwright timeouts would only hide the defect. Frontend deadlines are
the essential defensive fix, while OID 0 should be treated as a secondary
catalog/session investigation until the query is corrected.
