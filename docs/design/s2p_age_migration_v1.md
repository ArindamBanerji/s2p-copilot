# S2P AGE Migration v1

**Status:** implementation design and Phase 4 gate

## §1 Executive summary

S2P currently builds its default scorer with a directly constructed `SQLiteGraphStore`, rather than the SDK factory. It uses the SDK `CompoundingScorer` with an S2P reward function and the `S2P-` decision-ID prefix. `main.py:87-100`. The production app installs that scorer at startup using `DATA_DIR / "s2p.db"`; `CI_DATA_DIR` can relocate that data directory. `main.py:16,118-123`.

Three blockers prevent copying the Trading migration mechanically:

1. **P1 — factory bypass.** Default S2P writes cannot enter SDK `dual_write` because `build_s2p_scorer()` constructs SQLite directly. `main.py:87-99`.
2. **P1 — entity enrichment.** S2P uses `GraphStore.write_entity_enrichment`; AGE support must be explicitly implemented or intentionally kept local before enabling dual-write. `services/s2p_enrichment.py:284-303`, `copilot_sdk/graph/protocol.py:99-111`.
3. **P1 — OD-1 decision/entity edges.** The migration intentionally defers `decision_entity_edges` where target entity nodes do not exist in AGE. `age_shared_graph_migration_v3_20.md:376-409`.

**Recommended approach: hybrid migration.** Migrate active and archived Decision/Outcome/checkpoint/receipt history to AGE; dual-write governed decision/outcome/retention operations; retain enrichment and entity-edge source-of-truth in SQLite for the first S2P flip. Do not enable `link_entity` or enrichment writes on the AGE secondary until their topology and reader contracts are implemented and validated.

Implementation order: factory compliance → governed S2P ID path → full-history baseline migration → archive reconciliation → decision dual-write and parity → enrichment/edge isolation gate → active-read flip. Entity enrichment and OD-1 graph topology are separate follow-up work, not hidden prerequisites inside the decision migration.

## §2 Current architecture

### Store and scorer

`build_s2p_scorer()` selects a caller-injected store when supplied; otherwise it constructs `SQLiteGraphStore(effective, domain="s2p", decision_id_prefix="S2P-")`. `backend/app/main.py:87-93`. It then calls `CompoundingScorer.from_preset("s2p", graph_store=..., reward_function=S2PRewardFunction())`. `main.py:94-99`. S2P applies a runtime tensor migration for legacy factor dimensions after scorer construction. `main.py:103-117`.

At application startup, `create_s2p_active_graph_store()` is passed as the injected store, but the normal default remains direct SQLite if that helper returns `None`. `main.py:118-123`. The active-store helper itself has a factory path for AGE, showing that S2P has partial active-graph infrastructure but not factory compliance in the normal path. `backend/app/s2p_graph_status.py:258-288`.

S2P also has a direct non-scorer write path: its router calls `scorer.graph_store.write_decision(...)`. `backend/app/routers/s2p.py:1632`. This must be moved to governed caller-provided IDs or kept primary-only; DualWriteStore deliberately does not send raw writes to AGE because primary/secondary IDs can diverge.

### Enrichment

`S2PEnrichmentService` obtains `write_entity_enrichment` dynamically, writes an enrichment receipt when available, and treats absence as unsupported. `services/s2p_enrichment.py:281-307`. It also has a dry-run path using the same write API. `services/s2p_enrichment.py:309-336`. Situation graph enrichment requires that writer and raises if it is absent. `services/situation_graph_enrichment.py:101-116`.

Enrichment writes domain, entity type, entity ID, namespace, metrics, and a provenance-bearing `EnrichmentSourceSet`, as specified by the GraphStore API. `copilot_sdk/graph/protocol.py:99-111`. These values support S2P operational/context routes; they are not inputs to `CompoundingScorer.score()` in the default app construction path. `main.py:87-123`.

### Entity edges and models

The GraphStore V2 API exposes `link_entity(domain, decision_id, entity_id, entity_type, ...)`. `copilot_sdk/graph/protocol.py:270-280`. S2P's domain graph creates deterministic `S2P-<event>-<timestamp>` decision identifiers for graph events. `backend/app/domains/s2p/graph.py:69`. S2P response models expose score decision IDs, factors, factor vectors, and process context. `backend/app/models/responses.py:17-29`; its evidence receipt includes decision, invoice, supplier, PO, factors, outcome, and receipt-chain fields. `backend/app/models/outcome_receipt.py:13-48`.

S2P's source SQLite contains `decisions`, `outcomes`, `centroid_checkpoints`, `evidence_receipts`, and `decision_entity_edges`; v3.20 records 24,032 Decisions, 12 Outcomes, 12 checkpoints, 4 receipts, and 353 S2P entity edges in the discovery baseline. `age_shared_graph_migration_v3_20.md:315-329`. Unlike the initial v3.20 assumption that archives were empty, v3.22 requires checking each source's `decisions_archive` and migrating it when present. `age_shared_graph_migration_v3_22_addendum.md:1-10,88-108`.

## §3 Blocker 1 — factory compliance

### Required behavior

`create_graph_store()` already accepts backend, domain, db path, ID prefix, DSN, graph name, shared authorization, and an optional test mode. `copilot_sdk/graph/factory.py:117-130`. Its `dual_write` branch builds SQLite primary and AGE secondary, derives a durable outbox adjacent to the SQLite database, and requires authorization for `soc_graph`. `factory.py:160-221`.

### Implementation plan

1. In `s2p-copilot/backend/app/main.py`, replace the direct SQLite default in `build_s2p_scorer()` with `create_graph_store(backend=..., domain="s2p", db_path=effective, decision_id_prefix="S2P-")`. Preserve explicit `graph_store` injection for tests.
2. Read `GRAPH_BACKEND` exactly as Trading/Purchasing/DataOps do. Generic `age` must remain downgraded to SQLite; `dual_write` must pass through to the factory. Active read selection remains owned by `S2P_ACTIVE_*`, not generic `GRAPH_BACKEND`.
3. Add `S2PActiveAGEGraphStore.generate_decision_id()` returning `S2P-<uuid12>` before any AGE active flip. The generic adapter intentionally uses bare UUIDs; domain wrapper owns prefix policy.
4. Change the direct router raw write at `routers/s2p.py:1632` to `write_governed_decision()` with a generated primary-owned ID, or declare that endpoint unavailable during dual-write. Do not call raw `write_decision()` and expect AGE parity.
5. Enable `SCORER_GOVERNED_WRITES=1` only after the active S2P wrapper and factory path are Protocol V2 compliant. Governed mode validates V2 at scorer construction and creates a caller-owned ID before writing. `copilot_sdk/scoring/scorer.py:136-141,280-314`.

### Tests and gate

- Unit test `GRAPH_BACKEND=sqlite` preserves `S2P-` IDs and existing DB path.
- Unit test `GRAPH_BACKEND=dual_write` produces `DualWriteStore(SQLiteGraphStore, AGEGraphStoreAdapter)` and an S2P outbox path.
- Unit test S2P active AGE ID begins `S2P-` and matches `[0-9a-f]{12}` suffix.
- Integration test score → learn writes the same compound `(s2p, decision_id)` to SQLite and AGE.
- Gate: no default code path constructs SQLite directly except test-only or explicitly injected stores.

## §4 Blocker 2 — entity enrichment

### Options

**A. Implement AGE enrichment now.** Add AGE adapter/store support for `write_entity_enrichment`, define labels and compound identity `(domain, namespace, entity_type, entity_id)`, persist serializable metrics/provenance, and implement equivalent reads. This is complete but broad: readers and migrations must be defined before a production claim of enrichment parity.

**B. Retain enrichment in SQLite for first S2P flip — recommended.** Decision history moves to AGE; enrichment stays on the S2P SQLite primary. Explicitly configure DualWriteStore to classify AGE `NotImplementedError` as unsupported and do not treat it as a replayable decision failure. The frontend/context readers continue using their local enrichment source. This minimizes Phase 4 blast radius and preserves current situation/enrichment behavior.

**C. Drop enrichment.** Rejected: the situation enricher explicitly requires the writer and S2P exposes enrichment/context features. `services/situation_graph_enrichment.py:101-116`.

### Future AGE enrichment specification

If option A is approved, add `EntityEnrichment` nodes with immutable identity properties: `domain`, `namespace`, `entity_type`, `entity_id`, `receipt_id`/idempotency key, `computed_at`, `metrics_json`, `computed_from_json`, and `migration_source` where relevant. Use an `ENRICHES` edge to a domain-scoped entity node only after target node lifecycle is defined. Implement idempotency by `MATCH` exact identity then `CREATE`; do not use AGE `MERGE` or `$params`. Return the protocol receipt shape. Add reader methods before changing UI sources. The direct implementation must live in ci-platform AGE store/adapter; the SDK protocol already defines the contract.

## §5 Blocker 3 — OD-1 entity edges

OD-1 is deferred because S2P `decision_entity_edges` point at entity nodes that were not found in AGE, which would create dangling or invented topology. The required discovery is: PRAGMA schema, sample rows, distinct entity IDs, AGE target lookup, and live-writer search. `age_shared_graph_migration_v3_20.md:376-409`.

**Recommendation: defer for the first S2P decision flip.** Migrate no `decision_entity_edges`; retain them in SQLite. This does not prevent Decision/Outcome, retention, conservation, or standard scorer parity. It does prevent claiming entity relationship parity.

To implement later: define canonical entity labels/identity, backfill entity nodes from an authoritative source, implement live entity writes, then migrate edges only when each target is present. Required edge identity is `(domain, decision_id, entity_type, entity_id)` and all Cypher must scope source and target by domain.

## §6 Migration approach

Use the SDK full-history migration command with `--domain=s2p --all-decisions --include-archived --batch-size=1000`. It already handles schema discovery, active pending records, denormalized archive outcomes, checkpoint/resume, compound identity, and topology verification. `copilot_sdk/migrate/sqlite_to_age.py:174-312,942-1030`.

| Source | AGE action | First-flip disposition |
|---|---|---|
| `decisions` + `outcomes` | Decision, Outcome, `HAS_OUTCOME` | migrate |
| `decisions_archive` | archived Decision/Outcome, preserved source archive fields | migrate if table/rows exist |
| checkpoints/receipts | canonical nodes and edges | migrate |
| `decision_entity_edges` | no edge | defer under OD-1 |
| enrichment tables/state | local source | retain SQLite |
| operational scorer state | cold-start/restore path | retain/re-derive |

Archive records are written directly with `archived=true`; status derives from inline outcome (`NULL` action → pending; correct → confirmed; incorrect → overridden). Active/archive ID overlap is a hard migration failure. `sqlite_to_age.py:239-312,1004-1015`.

## §7 Implementation order

1. **S2P factory path** — s2p-copilot: main/store construction and tests. Gate: SQLite default unchanged, dual-write factory construction succeeds.
2. **S2P governed identity** — s2p-copilot: active wrapper ID method and raw router path. Gate: generated `S2P-` IDs remain stable through score/learn/outcome.
3. **Authorization** — s2p-copilot + SDK factory: exact `s2p:soc_graph` authorization; retain test-graph gates. Gate: unauthorized product graph rejected.
4. **Baseline inventory/OD-1 evidence** — S2P SQLite plus AGE read-only queries. Gate: signed entity-edge verdict and explicit enrichment option B recorded.
5. **Backup, reset, full-history migration** — copilot-sdk tooling. Gate: active/archive topology and field parity pass.
6. **Reconciliation** — SDK reconciler. Gate: active-only AGE matches are archived; rerun reports already archived, not missing.
7. **Dual-write** — configuration/deployment. Gate: 40 zero-discrepancy active/history cycles; durable outbox clear.
8. **AGE active flip** — s2p-copilot active graph config. Gate: score/learn, trajectory/read endpoints, retention, rollback, and S2P operational routes pass.
9. **Enrichment and OD-1 follow-up** — separate approved project; no flip blocker after option B is enforced.

## §8 Environment and configuration

Dual-write:

```text
GRAPH_BACKEND=dual_write
GRAPH_DSN=<AGE DSN>
GRAPH_NAME=soc_graph
SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
SCORER_GOVERNED_WRITES=1
```

Active AGE read/write after the gate:

```text
S2P_ACTIVE_GRAPH_BACKEND=age
S2P_ACTIVE_AGE_DSN=<AGE DSN>
S2P_ACTIVE_AGE_GRAPH=soc_graph
S2P_ACTIVE_AGE_DOMAIN=s2p
S2P_SHARED_GRAPH_AUTHORIZED=s2p:soc_graph
S2P_ACTIVE_AGE_TEST_MODE=0
S2P_SHADOW_AGE=0
```

The exact-pair authorization model follows factory shared-graph validation. `copilot_sdk/graph/factory.py:186-209`. S2P must add the equivalent active-wrapper check; it must not accept graph-only authorization.

## §9 Validation sequence

1. SQLite backup and AGE domain-scoped backup/count baseline.
2. Inventory/archive/OD-1 evidence, then tagged domain reset.
3. Run full-history migration; retain checkpoint and report.
4. Run archive reconciliation and rerun it to prove idempotency.
5. Run `phase_verify.py --domain s2p`, `phase_read_diff.py --domain s2p`, and `phase_dual_parity.py --domain s2p` with separate concrete stores.
6. Start dual-write and use `phase_dual_write_e2e.py --domain s2p`; ensure all generated IDs use `S2P-` and outbox has no unresolved entries.
7. Run `phase_cycle_gate.py --domain s2p --cycles 40`. At S2P scale, intermediate samples are acceptable only if final full active/history comparison passes before flip.
8. Flip with S2P active variables, then run `phase_flip_verify.py --domain s2p` plus S2P route and Playwright coverage.

The parameterized scripts provide domain/prefix/DB/API configuration, but S2P must verify its mounted API base and live route shapes before treating the generic score/learn scripts as a production gate.

## §10 Risks and open questions

1. **Human decision:** approve option B enrichment retention for first flip, including explicit product wording that enrichment remains local.
2. **Human decision:** sign OD-1 after target-node discovery; do not infer that 353 edges are safe to migrate.
3. **Risk:** the direct router raw write must be governed or primary-only before dual-write; otherwise AGE Decision identity is not guaranteed.
4. **Risk:** large S2P scale requires measured migration benchmark and explicit timeout/rollback acceptance, as v3.20 requires before S2P. `age_shared_graph_migration_v3_20.md:458-466,846-863`.
5. **Risk:** S2P's 5×5×7 tensor/runtime migration must be regression-tested with migrated data; it is not interchangeable with Trading shapes. `s2p-copilot/CLAUDE.md:18-28`, `main.py:103-117`.

## §11 Reading log

Read in full for this design:

- `s2p-copilot/backend/app/main.py:1-201` — startup, direct SQLite construction, scorer, runtime migration, routers.
- `s2p-copilot/backend/app/services/s2p_enrichment.py:1-<EOF>` — enrichment writer/fallback behavior.
- `s2p-copilot/backend/app/services/` inventory, including `situation_graph_enrichment.py:60-116` — enrichment consumer.
- `s2p-copilot/backend/app/models/:1-<EOF>` — response and outcome receipt model surfaces.
- `copilot-sdk/copilot_sdk/graph/factory.py:1-<EOF>` — backend, dual-write, authorization and outbox construction.
- `copilot-sdk/copilot_sdk/graph/protocol.py:1-<EOF>` — GraphStore/V2 contracts.
- `copilot-sdk/copilot_sdk/scoring/scorer.py:1-<EOF>` — governed writes and retention behavior.
- `copilot-sdk/docs/design/age_shared_graph_migration_v3_20.md:291-466,779-863` — migration mapping, OD-1, batching and S2P gates.
- `copilot-sdk/docs/design/age_shared_graph_migration_v3_22_addendum.md:1-<EOF>` — archive/full-history/reconciliation/flip requirements.

