# S2P AGE Migration v1.2

**Date:** 2026-07-24  
**Status:** implementation design and Phase 4 gate  
**Supersedes:** `s2p_age_migration_v1_1.md`  
**Parent:** AGE shared-graph migration v3.20 and v3.22 addendum

## §1 Scope and current state

S2P currently constructs a direct SQLite scorer unless a store is injected
(`s2p-copilot/backend/app/main.py:87-99`), while production data is rooted at
`CI_DATA_DIR` or the app data directory (`main.py:16-17,119-125`). The source
baseline is 24,032 Decisions and 12 verified Decisions (`V_s2p=12`). The
runtime scorer performs a legacy seven-to-eight-factor tensor migration
(`main.py:103-117`).

The first cutover is hybrid: migrate active/archive Decision and Outcome
history plus canonical checkpoint/receipt topology; dual-write governed
decision, outcome, and retention operations; keep enrichment in SQLite; defer
OD-1 entity edges. This document is self-contained for implementation and
supersedes v1.1's gates where stated below.

## §2 Factory, paths, and governed identity

Replace default direct construction with
`create_graph_store(backend=..., domain="s2p", db_path=effective,
decision_id_prefix="S2P-")`, preserving injected test stores. The factory
accepts these controls (`copilot-sdk/copilot_sdk/graph/factory.py:117-129`),
constructs SQLite primary plus AGE secondary and durable outbox in dual-write
mode (`factory.py:160-221`), and requires exact pair authorization
`s2p:soc_graph` (`factory.py:186-209`). `effective` MUST be the absolute
`CI_DATA_DIR`-resolved path, logged at startup; a factory default or `:memory:`
production path fails the gate.

Add `S2PActiveAGEGraphStore.generate_decision_id()` returning `S2P-` plus 12
hex characters. Set `SCORER_GOVERNED_WRITES=1` only after the active wrapper,
factory path, and test doubles satisfy Protocol V2. Governed construction with
a non-V2 store must raise, not silently fall back
(`copilot-sdk/copilot_sdk/scoring/scorer.py:136-141`).

## §3 Enrichment ownership and durable split-read contract

For the first flip, the enrichment service receives the SQLite primary object
directly; enrichment calls do not pass through `DualWriteStore`. Decision,
Outcome, and retention calls do dual-write. The service already catches
`NotImplementedError` and returns an unsupported receipt
(`s2p-copilot/backend/app/services/s2p_enrichment.py:276-305`). As defense in
depth, if a future wiring error sends enrichment to DualWriteStore, that same
fallback remains the safe behavior; these are two layers, not contradictory
ownership rules.

The split-read contract is durable: decisions are read from AGE after flip and
enrichment is read from SQLite until Option A lands. New enrichment readers
MUST NOT assume co-location with decisions. Existing situation enrichment uses
independent enrichment calls (`backend/app/services/situation_graph_enrichment.py:90-119`);
the implementation gate still greps all readers and records that no same-store
JOIN is assumed. Option A (AGE enrichment with compound identity and equivalent
reads) is a separate approved project; dropping enrichment is rejected because
the situation service requires it (`situation_graph_enrichment.py:104-119`).

## §4 OD-1 edge disposition

`decision_entity_edges` remain SQLite-only because target entities and canonical
identity are unproven (`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:376-409`).
The later OD-1 gate requires a signed report by the S2P data owner and graph
owner, covering distinct `(domain, decision_id, entity_type, entity_id)` tuples,
AGE target-node query results, and live-writer identity evidence. No edge parity
claim is made before that signature.

## §5 Migration and pre-flight

Run `--domain=s2p --all-decisions --include-archived --batch-size=1000` with
phase-aware checkpoint identity (domain, source path, graph, both flags, and
source manifest hashes). Pending active rows remain pending and receive no
Outcome. Archive status derives from inline outcome: NULL action → pending,
`is_correct=true` → confirmed, `false` → overridden
(`copilot-sdk/copilot_sdk/migrate/sqlite_to_age.py:239-312,1004-1015`).

### Step 0: clean slate and overlap

Before any AGE write:

```sql
SELECT decision_id FROM decisions
INTERSECT
SELECT decision_id FROM decisions_archive;
```

The result MUST be empty. Also run the symmetric orphan diagnostic:

```sql
SELECT a.decision_id FROM decisions_archive a
LEFT JOIN decisions d ON d.decision_id = a.decision_id
WHERE d.decision_id IS NULL;
```

This design does not assume every archive row had a currently present active
row: retention physically removes active rows, so archive-only rows are valid.
The symmetric query is diagnostic and must be recorded, not treated as an
error. Finally, run a domain-scoped AGE clean-slate query for
`domain='s2p'` (active and archived Decision nodes tagged by the prior
migration), and abort unless the operator has explicitly reset or documented
the retained baseline. A clean SQLite overlap is not sufficient protection
against a failed prior AGE run.

Archive rows can reconstruct Decision plus denormalized Outcome, but not
checkpoint/receipt topology; archive migration writes Decision+Outcome only.
Malformed inline outcome is rejected: `actual_action IS NOT NULL` with
`is_correct IS NULL` is not a valid pending row and cannot be safely classified.

## §6 Concrete gates and order

1. **Path/factory:** CI_DATA_DIR identity is identical in app, factory,
   backup, and migration; dual-write has an outbox.
2. **V2 identity:** S2P prefix and governed-write validation pass.
3. **Pre-flight:** clean AGE slate, empty active/archive INTERSECT, manifest
   captured, and archive orphan diagnostic reviewed.
4. **Migration snapshot fidelity:** immediately after migration, before
   dual-write opens, assert `V_s2p = 12` exactly, verified/correct counts and
   pending-status histogram equal the SQLite snapshot, and active/archive
   topology and fields pass. This catches pending→confirmed drift.
5. **Tensor:** load migrated records, apply the 5×5×7 runtime migration, and
   compare factor-vector dimensions/values and scorer outputs to SQLite for a
   fixed sample (`main.py:103-117`).
6. **Enrichment:** direct SQLite ownership observed; no AGE/outbox enrichment;
   all readers satisfy the durable split-read contract.
7. **Benchmark:** run against a disposable scratch AGE graph named
   `s2p_benchmark_<timestamp>`, never `soc_graph` or a live demo graph. Drop it
   after measurement. For 24,032 decisions acceptance is ≤30 minutes, no
   timeout/topology error, and ≥100 decisions/second; a different target needs
   operational approval. v3.20 requires a measured benchmark
   (`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:458-466`).
8. **OD-1 signature:** owners sign the §4 report.
9. **Live 40-cycle:** record `V_s2p_at_dual_write_start`. Each cycle performs
   score→learn, exact L1 counts, and a deterministic 1,000-ID sample (or all
   rows if fewer). Every newly verified decision is traced to a real outcome
   event and exists in both stores. The final cycle is a full active/history
   comparison. Require 40 consecutive zero-discrepancy cycles, healthy empty
   outbox, and `V_s2p >= V_s2p_at_dual_write_start`; legitimate new
   verification may increase V.
10. **Flip:** score, learn, retention, trajectory, enrichment, and rollback
    checks pass with active AGE variables.

## §7 Raw repair route and rollback

The internal outcome-repair helper at `s2p-copilot/backend/app/routers/s2p.py:1611-1639`
currently uses raw `write_decision` (`s2p.py:1632-1639`). Convert it to
governed writing. It MUST first call `get_decision(request.decision_id,
domain="s2p")`; a missing prior Decision (including an archive-only/pruned ID)
is rejected with a clear not-found response. It must never create a synthetic
Decision-then-Outcome repair, because that would fabricate history and bypass
migration identity. For an existing Decision, use the request ID and then
`write_outcome(domain="s2p")` with that same ID.

Rollback is configuration-first: set `S2P_ACTIVE_GRAPH_BACKEND=sqlite`, unset
active AGE variables, set `SCORER_GOVERNED_WRITES=0` if needed, disable
dual-write, restart, and run SQLite score/learn and route smoke tests. If code
is implicated, revert the factory/governed-router deployment commit while
retaining SQLite/AGE backups. Never re-enable raw dual-write writes.

## §8 Configuration

```text
GRAPH_BACKEND=dual_write
GRAPH_DSN=<AGE DSN>
GRAPH_NAME=soc_graph
SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
SCORER_GOVERNED_WRITES=1
CI_DATA_DIR=<absolute S2P data root>
S2P_ACTIVE_GRAPH_BACKEND=age
S2P_ACTIVE_AGE_DSN=<AGE DSN>
S2P_ACTIVE_AGE_GRAPH=soc_graph
S2P_ACTIVE_AGE_DOMAIN=s2p
S2P_SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
S2P_ACTIVE_AGE_TEST_MODE=0
S2P_SHADOW_AGE=0
```

## §9 Validation sequence

0. Resolve path, back up SQLite, run both SQLite queries and AGE clean-slate
   check; record snapshot `V_s2p=12`.
1. Benchmark scratch graph and reset target domain.
2. Full-history migrate with checkpoint; verify topology, status histogram,
   tensor, and exact snapshot gate.
3. Reconcile archives and rerun reconciler for idempotency.
4. Enable dual-write; run governed score→learn, retention, and direct-local
   enrichment checks.
5. Run 40 cycles with 1,000-row intermediate samples and a final full diff;
   enforce live `V >= start` plus traced verification events.
6. Flip AGE reads and run endpoint/trajectory/tensor/rollback checks.

## §10 Parent alignment and reading log

The design adopts v3.22 active/D2 exclusion, property-based archival,
normalized archive history, `compare_active`/`compare_history`, reconciliation,
and dual-mode 40-cycle flip gates
(`copilot-sdk/docs/design/age_shared_graph_migration_v3_22_addendum.md:3.5-3.9,7.2,8`).
S2P additions are snapshot/live V gates, clean-slate and orphan diagnostics,
tensor and benchmark acceptance, split-read contract, and repair rejection.

Evidence read: `s2p-copilot/backend/app/main.py:1-140`; `backend/app/routers/s2p.py:1611-1639`;
`backend/app/services/s2p_enrichment.py:276-345`; `backend/app/services/situation_graph_enrichment.py:90-130`;
`backend/app/s2p_graph_status.py:258-285,319-400`; `copilot-sdk/copilot_sdk/graph/factory.py:117-221`;
`copilot-sdk/copilot_sdk/graph/protocol.py:99-111,270-280`;
`copilot-sdk/copilot_sdk/scoring/scorer.py:136-141`;
`copilot-sdk/copilot_sdk/migrate/sqlite_to_age.py:239-312,942-1030`;
`copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:315-329,376-409,458-466,809-822`.

## §11 Review disposition

All eight v1.1 follow-up findings are addressed: snapshot versus live V,
durable split-read constraint, malformed archive shape, symmetric orphan
diagnostic, scratch benchmark target, prior-decision repair check, two-layer
enrichment defense, and AGE clean-slate verification.
