# S2P AGE Migration v1.1

**Date:** 2026-07-24  
**Status:** implementation design and Phase 4 gate  
**Supersedes:** `s2p_age_migration_v1.md`  
**Parent:** AGE shared-graph migration v3.20 and v3.22 addendum

## §1 Executive summary

S2P has 24,032 source Decisions and exactly 12 verified Decisions (`V_s2p = 12`).
Its data root is selected by `CI_DATA_DIR` (`s2p-copilot/backend/app/main.py:16-17`).
The scorer is currently built around a directly constructed SQLite store
(`backend/app/main.py:87-99`), with an explicit active-AGE injection path
(`main.py:118-125`). The blockers are factory bypass, enrichment ownership,
and OD-1 entity-edge identity.

The first cutover is deliberately hybrid: migrate active and archived
Decision/Outcome/checkpoint/receipt history; dual-write governed decisions,
outcomes, and retention; keep enrichment in SQLite through a direct-primary
path; and defer OD-1 edges until entity identity is signed off. This document
adds mandatory pre-flight, conservation, tensor, benchmark, sampling, and
rollback gates.

`SCORER_GOVERNED_WRITES=1` is set only after V2 compliance; a non-V2 store must
fail at construction, not emit ungoverned IDs
(`copilot-sdk/copilot_sdk/scoring/scorer.py:136-141`).

## §2 Current architecture

`build_s2p_scorer()` preserves an injected store, otherwise constructs
`SQLiteGraphStore(effective, domain="s2p", decision_id_prefix="S2P-")`
(`backend/app/main.py:87-99`). Production passes `str(DATA_DIR / "s2p.db")`
(`main.py:119-125`); `effective` can otherwise be `:memory:` (`main.py:87-90`).
`DATA_DIR` is `CI_DATA_DIR` or the app data directory (`main.py:16-17`). The
factory and all migration tooling MUST receive this same absolute resolved
path, with a startup log/assertion to prevent a second SQLite file.

The scorer is `CompoundingScorer.from_preset("s2p", ...)` (`main.py:94-99`)
and pads legacy seven-factor runtime tensors to the S2P shape after
construction (`main.py:103-117`). The explicit active helper uses the SDK
factory for AGE (`backend/app/s2p_graph_status.py:258-285`), but normal default
construction bypasses it.

Enrichment writes domain, entity type/id, namespace, metrics, provenance,
dry-run, and idempotency key through `write_entity_enrichment`
(`backend/app/services/s2p_enrichment.py:276-305`). Situation enrichment has
independent read/write calls (`backend/app/services/situation_graph_enrichment.py:90-119`).
The GraphStore enrichment and edge contracts are
(`copilot-sdk/copilot_sdk/graph/protocol.py:99-111,270-280`). The v3.20
baseline records S2P's source tables and counts
(`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:315-329`).

The raw write at `backend/app/routers/s2p.py:1611-1639` is an internal outcome
repair helper, not the primary user scoring route; it calls
`write_decision` (`s2p.py:1632-1639`). It is selected for governed conversion.

## §3 Blocker 1 — factory compliance

Replace the default direct SQLite construction with
`create_graph_store(backend=..., domain="s2p", db_path=effective,
decision_id_prefix="S2P-")`, retaining explicit test injection. The factory
accepts these controls (`copilot-sdk/copilot_sdk/graph/factory.py:117-129`),
and its dual-write path constructs SQLite primary, AGE secondary, and a domain
outbox (`factory.py:160-221`). Generic AGE remains a safety-gated active path.

Add `S2PActiveAGEGraphStore.generate_decision_id()` returning `S2P-` plus 12
hex characters. The active wrapper, factory path, and all test doubles must be
V2-complete before enabling governed writes. The exact pair
`s2p:soc_graph` is required by the factory authorization check
(`factory.py:186-209`).

Tests must prove SQLite default behavior, CI_DATA_DIR path identity, dual-write
construction/outbox, S2P prefix, and score→learn compound identity.

## §4 Blocker 2 — enrichment isolation

### Decision: Option B, direct SQLite ownership

For the first flip, enrichment calls go directly to the SQLite primary object,
never through `DualWriteStore`. Decisions, outcomes, and retention remain
dual-write. This prevents expected AGE `NotImplementedError` from becoming
replayable outbox failures and blocking the flip. The service already catches
unsupported enrichment and returns a warning receipt
(`s2p_enrichment.py:284-305`).

Before implementation, grep all enrichment readers (`read_entity_enrichment`,
`list_entity_enrichments`, situation, supplier, invoice, and context routes).
The known situation reader independently calls enrichment APIs
(`situation_graph_enrichment.py:90-95`) and does not JOIN decisions and
enrichment in one store. Record the grep result as a gate proving no reader
assumes same-store co-location. After flip, AGE supplies decisions while
SQLite supplies enrichment.

Option A (AGE enrichment) is deferred until compound identity
`(domain, namespace, entity_type, entity_id)`, normalized provenance, reads,
idempotency, and migration are separately specified. Option C (drop enrichment)
is rejected because S2P situation features require it
(`situation_graph_enrichment.py:104-119`).

## §5 Blocker 3 — OD-1 entity edges

S2P `decision_entity_edges` remain deferred because target entity nodes and
canonical identity are unproven (`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:376-409`).
No edge is migrated or dual-written in the first cutover; SQLite remains the
edge source of truth.

The OD-1 verdict must be signed by the S2P data owner and graph owner against a
reproducible report: distinct `(domain, decision_id, entity_type, entity_id)`
source tuples, target-node existence query results in AGE, and live-writer
identity evidence. A count-only or verbal approval does not pass. Later work
must define canonical labels, target lifecycle, domain-scoped matching, live
entity writes, and edge idempotency.

## §6 Migration approach

Run `--domain=s2p --all-decisions --include-archived --batch-size=1000`.
Active pending rows remain pending and receive no Outcome. Archive rows derive
status from inline outcome (`actual_action IS NULL → pending`, true → confirmed,
false → overridden), and are written directly with `archived=true`, preserving
source archive metadata (`copilot-sdk/copilot_sdk/migrate/sqlite_to_age.py:239-312,1004-1015`).

### Step 0 pre-flight overlap query

Before any write transaction, run:

```sql
SELECT decision_id FROM decisions
INTERSECT
SELECT decision_id FROM decisions_archive;
```

The result MUST be empty. Abort before AGE writes and list conflicting IDs.
This is a pre-flight query, not a late batch exception. Capture row counts,
status histogram, `V_s2p=12`, and active/archive manifest hashes.

Archive rows can reconstruct Decision and denormalized Outcome, but cannot
reconstruct checkpoint/receipt topology. As in Trading, archive migration
writes Decision+Outcome only; checkpoints and receipts come from canonical
tables and are not inferred from archive rows.

Checkpoint identity binds domain, source path, graph, both flags, and source
manifest hashes. Resume rejects either flag changing and records active/archive
phase progress.

## §7 Gates and order

1. **Factory/path:** SQLite default unchanged; CI_DATA_DIR path is identical in
   app, factory, backup, and migration; dual-write creates primary, secondary,
   and durable outbox.
2. **V2 identity:** S2P active wrapper implements `generate_decision_id`; the
   governed scorer rejects non-V2 stores loudly. Only now set
   `SCORER_GOVERNED_WRITES=1`.
3. **Pre-flight/migration:** overlap empty; pending histogram unchanged;
   malformed archive rows rejected; topology and field parity pass.
4. **OD-1 signature:** the two named owners sign the tuple-level target-node
   report in §5. Until then edges remain SQLite-only.
5. **Tensor:** load migrated S2P records, apply the 5×5×7 runtime migration,
   and compare factor-vector dimensions, values, and scorer outputs to SQLite
   for a fixed sample (`main.py:103-117`). Shape-only smoke tests do not pass.
6. **Enrichment:** direct SQLite enrichment is observed; no call reaches AGE
   or the outbox; reader co-location discovery passes.
7. **Measured benchmark:** run a disposable S2P-sized migration. Acceptance is
   completion within 30 minutes for 24,032 decisions, no timeout/topology
   error, and at least 100 decisions/second. A different window requires
   explicit operational approval. v3.20 requires a measured benchmark
   (`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:458-466`).
8. **40-cycle:** each cycle score→learn and parity. Intermediate checks sample
   exactly 1,000 IDs (or all rows when fewer); final pre-flip checks compare
   every active and archived ID. Require 40 consecutive zero-discrepancy
   cycles and a healthy, empty outbox.
9. **V predicate:** after migration and final cycle, `V_s2p = 12` exactly;
   correct count and pending-status histogram match SQLite. Any pending→
   confirmed drift fails and resets the counter.
10. **Flip:** score, learn, retention, trajectory, enrichment, and rollback
    routes pass with active AGE variables.

### §7.5 Rollback

First use configuration-only rollback: set `S2P_ACTIVE_GRAPH_BACKEND=sqlite`
(or unset active AGE variables), set `SCORER_GOVERNED_WRITES=0` if required,
restart, and disable dual-write. This restores SQLite reads/writes without
deleting AGE or reconciling AGE-only rows.

If factory, governed router, or enrichment code is implicated, revert that S2P
deployment commit, restore the prior environment, retain DB/AGE backups, and
run SQLite score→learn plus route smoke tests. No rollback may re-enable raw
dual-write writes or authorize another domain on `soc_graph`.

## §8 Environment

```text
GRAPH_BACKEND=dual_write
GRAPH_DSN=<AGE DSN>
GRAPH_NAME=soc_graph
SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
SCORER_GOVERNED_WRITES=1
CI_DATA_DIR=<absolute S2P data root, if used>
```

```text
S2P_ACTIVE_GRAPH_BACKEND=age
S2P_ACTIVE_AGE_DSN=<AGE DSN>
S2P_ACTIVE_AGE_GRAPH=soc_graph
S2P_ACTIVE_AGE_DOMAIN=s2p
S2P_SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
S2P_ACTIVE_AGE_TEST_MODE=0
S2P_SHADOW_AGE=0
```

Authorization is the exact `(domain, graph)` pair; graph-only authorization is
forbidden. S2P active status must enforce the equivalent product check.

## §9 Validation sequence

0. Resolve CI_DATA_DIR, back up SQLite, run INTERSECT overlap query, and
   capture manifests, counts, status histogram, and `V_s2p=12`.
1. Domain-scoped AGE reset; prove active and archived tagged nodes are gone.
2. Run benchmark, then full-history migration with checkpoint.
3. Verify active/archive topology, Outcome edges, field parity, tensor gate,
   and pending histogram.
4. Run reconciliation; rerun it and require `not_found=0` and correct
   already-archived classification.
5. Start dual-write; verify S2P prefix, compound score→learn IDs, retention,
   and direct SQLite enrichment.
6. Run 40 cycles. Each intermediate cycle uses a deterministic 1,000-ID
   sample plus exact L1 counts; final cycle is a full active/history compare.
   Require `V_s2p=12`, zero status drift, zero unresolved outbox entries, and
   zero mismatches.
7. Flip active reads to AGE and run S2P route/trajectory/tensor checks while
   monitoring both parity modes and local enrichment health.

Parameterized phase scripts may use `--domain s2p`, but S2P route shapes,
factor/tensor migration, enrichment locality, and scale require S2P-specific
checks.

## §10 Decisions, risks, and parent alignment

### Raw router disposition

The route at `s2p.py:1611-1639` is internal outcome repair. Convert it to
`write_governed_decision`; use `request.decision_id` when repairing an existing
decision, otherwise call the V2 store's `generate_decision_id("s2p")`. Pass
all governed fields, then call `write_outcome(domain="s2p")` with that same
ID. There is no raw-write fallback in dual-write mode; errors are loud.

Risks are S2P scale, tensor drift, intentionally split enrichment ownership,
and deferred OD-1 parity. CI_DATA_DIR mistakes can create a second database,
so path logs and manifest binding are mandatory.

This design adopts v3.22's active/D2 definition, property-based AGE archival,
normalized archive fields, `compare_active`/`compare_history`, reconciliation
predicate, and dual-mode 40-cycle gate (`copilot-sdk/docs/design/age_shared_graph_migration_v3_22_addendum.md:3.5-3.9,7.2,8`).
It adds S2P-specific `V_s2p=12`, pre-flight overlap, 1,000-row sampling,
benchmark threshold, tensor, enrichment, and OD-1 decisions without changing
the parent contract.

## §11 Reading log

- `s2p-copilot/backend/app/main.py:1-140` — path, scorer, injection, tensor.
- `s2p-copilot/backend/app/routers/s2p.py:1611-1639` — raw repair path.
- `s2p-copilot/backend/app/services/s2p_enrichment.py:276-345` — enrichment.
- `s2p-copilot/backend/app/services/situation_graph_enrichment.py:90-130` — readers.
- `s2p-copilot/backend/app/s2p_graph_status.py:258-285,319-400` — active path.
- `copilot-sdk/copilot_sdk/graph/factory.py:117-221` — factory/auth/outbox.
- `copilot-sdk/copilot_sdk/graph/protocol.py:99-111,270-280` — contracts.
- `copilot-sdk/copilot_sdk/scoring/scorer.py:136-141` — V2 gate.
- `copilot-sdk/copilot_sdk/migrate/sqlite_to_age.py:239-312,942-1030` — migration.
- `copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:315-329,376-409,458-466,809-822` — baseline/OD-1/benchmark/cycles.
- `copilot-sdk/docs/design/age_shared_graph_migration_v3_22_addendum.md:3.5-3.9,7.2,8` — parent archive/diff/reconcile/flip contract.

## §12 Review disposition

All 12 consolidated findings are addressed: explicit V predicate and overlap
pre-flight; raw-router decision; direct enrichment isolation and reader gate;
CI_DATA_DIR; tensor and benchmark gates; rollback; governed ordering;
signed OD-1 gate; concrete sampling; archive topology note; and parent
alignment. No unresolved design decision blocks implementation. Human approval
is still required only for a different benchmark window and the future AGE
enrichment/OD-1 project, both outside the hybrid first cutover.
