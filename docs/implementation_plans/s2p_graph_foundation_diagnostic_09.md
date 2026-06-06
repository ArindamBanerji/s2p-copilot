# S2P Graph Foundation Diagnostic 09

Date: 2026-06-05
Model: gpt-5.3
Task Type: Diagnostic document creation only; no source code changes
Repo: s2p-copilot
Diagnostic Scope: S2P graph foundation for P36-P39, including graph contract, legacy Neo4j state, AGE/storage usage, router registration, and enrichment groundwork.
Prior Diagnostics Read: YES - `s2p-copilot/docs/implementation_plans/s2p_panel_p40_diagnostic_04.md`; YES - `copilot-sdk/docs/implementation_plans/sdk_backend_endpoint_map_diagnostic_02.md`; S2P implementation plans found included `s2p_failure_causal_diagnostic_plan.md` and `s2p_panel_p40_diagnostic_04.md`.

## Executive Summary

* Overall graph foundation state: PARTIAL. S2P has a formal `graph_contract.py`, SQLite scoring storage, active AGE cutover/status plumbing, and a legacy Neo4j path, but it does not yet expose a registered S2P AGE traversal API for Invoice/Supplier/PO/GoodsReceipt graph traversal.
* P36 graph schema verdict: SUPPLEMENT. Formal and legacy graph contracts exist, but the formal contract omits `GoodsReceipt` and the required edge names `SUPPLIES`, `MATCHES_PO`, and `TRIGGERED_EXCEPTION`.
* P37 enrichment groundwork verdict: SUPPLEMENT. Many S2P enrichment signals exist in factors, discovery, control tower, PVG, supplier, receipt, and exception code, but graph-enrichment writes for the required node/edge contract are not present.
* P38 graph traversal verdict: SUPPLEMENT. Active AGE graph-store cutover support exists through `s2p_graph_status.py`, but there is no S2P AGE traversal endpoint; P38 should still add `s2p_age_graph.py` and avoid the legacy `neo4j.py`.
* P39 graph enrichment verdict: FULL until P36/P38 are completed. Existing enrichment signals are useful groundwork, but there is no AGE graph enrichment layer for Supplier/Invoice/PO/GoodsReceipt relationships.
* Biggest architecture risk: accidentally extending `backend/app/db/neo4j.py`, which is active in some routes but is SOC/Neo4j legacy code, instead of using the ci-platform AGE client path.
* Recommended next prompt: targeted P36/P38 supplement to define the S2P AGE contract and implement `backend/app/s2p_age_graph.py` traversal using ci-platform AGEClient via the shared graph stack.

## Path Resolution

* CLAUDE_S2P value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
* CLAUDE_SDK value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk`
* Active S2P repo used: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
* Fallback repo used: NO. `copilot-sdk/apps/s2p/backend` was not present.
* S2P backend app path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend\app`
* S2P main.py path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend\app\main.py`
* Report path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\docs\implementation_plans\s2p_graph_foundation_diagnostic_09.md`
* ci-platform AGEClient path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\ci-platform\ci_platform\graph\age_client.py`
* Prior diagnostics found: S2P Diagnostic 04 YES; SDK Diagnostic 02 YES.

## CLAUDE.md Relevant Notes

`s2p-copilot/CLAUDE.md` says docs are aspirational until implementation is proven, code and tests beat docs, behavioral claims require file and line evidence, downstream consumers must be checked before interface changes, S2P must remain independent from SOC-specific logic/constants, and git must not be used. This diagnostic followed those constraints and did not run tests because the task forbids tests.

## Part 1 - S2P Backend File Tree

```text
__init__.py (0KB)
db\__init__.py (0KB)
db\neo4j.py (13.5KB)
domains\__init__.py (0KB)
domains\s2p\__init__.py (0KB)
domains\s2p\auto_approve.py (5.8KB)
domains\s2p\config.py (6KB)
domains\s2p\evolution\__init__.py (0.7KB)
domains\s2p\evolution\rule_templates.py (11.1KB)
domains\s2p\evolution\service.py (6.3KB) TODO_OR_STUB_SIGNAL_COUNT=18
domains\s2p\evolution\shadow_runner.py (1.2KB)
domains\s2p\evolver_config.py (2.4KB)
domains\s2p\factor_protocol.py (0.4KB)
domains\s2p\factors.py (11.6KB) TODO_OR_STUB_SIGNAL_COUNT=1
domains\s2p\graph.py (3.1KB)
domains\s2p\reward.py (1.1KB)
framework\__init__.py (0.6KB)
framework\agent.py (14.8KB)
framework\audit.py (15.5KB)
framework\checkpoint.py (5.4KB)
framework\composite_gate.py (8.3KB)
framework\convergence_math.py (1.5KB)
framework\decision_history.py (2.4KB)
framework\economics.py (3.8KB)
framework\event_bus.py (3.6KB)
framework\feedback_base.py (5.8KB)
framework\feedback_store.py (0.5KB)
framework\iks_base.py (4.1KB)
framework\intervention_controls.py (18.6KB)
framework\learning_state.py (5.8KB) TODO_OR_STUB_SIGNAL_COUNT=1
framework\narrative_base.py (4.1KB)
framework\ols_status.py (4.8KB)
framework\provenance.py (12.1KB)
framework\shadow_mode.py (3.9KB)
framework\similar_cases_base.py (6.9KB)
graph_contract.py (4.7KB)
main.py (4.6KB)
models\__init__.py (0.4KB)
models\intents.py (5.1KB)
models\outcome_receipt.py (6.2KB)
models\responses.py (2.3KB) TODO_OR_STUB_SIGNAL_COUNT=1
routers\__init__.py (0KB)
routers\framework_router.py (26.9KB)
routers\s2p_audit_export.py (12.3KB)
routers\s2p_clustering.py (8.2KB) TODO_OR_STUB_SIGNAL_COUNT=10
routers\s2p_control_tower.py (9.9KB)
routers\s2p_data_helpers.py (1.1KB) TODO_OR_STUB_SIGNAL_COUNT=1
routers\s2p_discovery.py (14.2KB) TODO_OR_STUB_SIGNAL_COUNT=1
routers\s2p_early_warning.py (10.6KB) TODO_OR_STUB_SIGNAL_COUNT=13
routers\s2p_evidence.py (14.1KB) TODO_OR_STUB_SIGNAL_COUNT=1
routers\s2p_evolution.py (2.6KB)
routers\s2p_explorer.py (19.8KB)
routers\s2p_governance.py (14.9KB) TODO_OR_STUB_SIGNAL_COUNT=1
routers\s2p_insight.py (9.2KB) TODO_OR_STUB_SIGNAL_COUNT=2
routers\s2p_novelty.py (2.5KB)
routers\s2p_payment.py (9.9KB) TODO_OR_STUB_SIGNAL_COUNT=14
routers\s2p_performance.py (8.2KB) TODO_OR_STUB_SIGNAL_COUNT=5
routers\s2p_preview.py (21.4KB) TODO_OR_STUB_SIGNAL_COUNT=18
routers\s2p_pvg.py (9.7KB) TODO_OR_STUB_SIGNAL_COUNT=3
routers\s2p_simulation.py (10KB)
routers\s2p_suppliers.py (15KB) TODO_OR_STUB_SIGNAL_COUNT=4
routers\s2p.py (58.3KB) TODO_OR_STUB_SIGNAL_COUNT=9
s2p_graph_status.py (15.3KB)
s2p_shadow.py (7.2KB)
seed_graph.py (9.1KB)
services\__init__.py (0KB)
services\intent_classifier.py (3.5KB)
services\novelty_tracker.py (5.2KB)
services\ols_status.py (0.3KB) TODO_OR_STUB_SIGNAL_COUNT=2
services\receipt_store.py (3.8KB)
services\s2p_evolution_dimensions.py (2.8KB)
services\s2p_evolver.py (6.5KB)
services\s2p_learning_gate.py (4.4KB)
services\supplier_profile_accumulator.py (14.2KB) TODO_OR_STUB_SIGNAL_COUNT=24
services\synthetic_invoices.py (7.8KB) TODO_OR_STUB_SIGNAL_COUNT=4
```

### Graph-Relevant Files Found

| File | Size | Graph Relevance | TODO/Stub Signals |
| ---- | ---: | --------------- | ----------------- |
| `backend/app/graph_contract.py` | 4.7KB | Formal and legacy S2P graph contract definitions. | None found in tree scan. |
| `backend/app/s2p_graph_status.py` | 15.3KB | `/api/s2p/graph/status`, active AGE cutover config, graph-store adapter. | None found in tree scan. |
| `backend/app/db/neo4j.py` | 13.5KB | Legacy SOC/Security Neo4j client still imported by some active routes. | None found in tree scan. |
| `backend/app/domains/s2p/graph.py` | 3.1KB | Writes S2PDecision/outcome nodes through legacy Neo4j client. | None found in tree scan. |
| `backend/app/seed_graph.py` | 9.1KB | Seed graph data includes process and GoodsReceipt concepts. | None found in tree scan. |
| `backend/app/routers/framework_router.py` | 26.9KB | SOC graph explorer endpoints backed by legacy Neo4j client. | None found in tree scan. |
| `backend/app/routers/s2p.py` | 58.3KB | Main S2P score/outcome routes; imports legacy Neo4j for S2PDecision writes and summary. | TODO/stub count 9. |
| `backend/app/routers/s2p_explorer.py` | 19.8KB | DK-weight and explorer endpoints; not a graph traversal API. | None found in tree scan. |

## Part 2 - neo4j.py Legacy Inspection

* Exists: YES
* Path: `backend/app/db/neo4j.py`
* Imported by S2P backend: YES. `backend/app/routers/framework_router.py:20` imports `neo4j_client`; `backend/app/routers/s2p.py:1308` and `backend/app/routers/s2p.py:1537` import `neo4j_client`.
* Queries found: SOC/Security query paths dominate: `Alert`, `Asset`, `User`, `AlertType`, `Playbook`, `TravelContext`, `SLA`, and `AttackPattern` are used in `backend/app/db/neo4j.py:57-93`; `DecisionContext`, `HAD_CONTEXT`, `FOR_ALERT`, and `APPLIED_PLAYBOOK` are used in `create_decision_trace` at `backend/app/db/neo4j.py:147-210`; `TRIGGERED_EVOLUTION` is created at `backend/app/db/neo4j.py:216-264`.
* Active/dead/unclear: Active but legacy. It is imported and called, but its schema and routes are SOC-oriented rather than the requested S2P AGE graph foundation.
* Conflict risk with `s2p_age_graph.py`: HIGH if P38 extends this file. It uses Neo4j driver semantics, SOC labels, and SOC endpoints.
* Recommendation: Do not modify `neo4j.py` for P38/P39. Treat it as a legacy compatibility path unless a separate migration removes active imports.

## Part 3 - Graph Contract / Node and Edge Definitions

### Node types

| Node Type | Present? | Formal / Inline / Implied | Evidence |
| --------- | -------: | ------------------------- | -------- |
| Invoice | YES | Formal and legacy | `backend/app/graph_contract.py:19-30` legacy `Invoice`; `backend/app/graph_contract.py:114` formal `NodeType("Invoice", ...)`. |
| Supplier | YES | Formal and legacy | `backend/app/graph_contract.py:31-41` legacy `Supplier`; `backend/app/graph_contract.py:115` formal `NodeType("Supplier", ...)`. |
| PO / PurchaseOrder | YES | Formal and legacy | `backend/app/graph_contract.py:42-45` legacy `PurchaseOrder`; `backend/app/graph_contract.py:116` formal `NodeType("PurchaseOrder", ...)`. |
| GoodsReceipt | PARTIAL | Legacy only | `backend/app/graph_contract.py:46-49` legacy `GoodsReceipt`; not present in formal `S2P_GRAPH_CONTRACT` node list at `backend/app/graph_contract.py:113-121`. |

### Edge types

| Edge Type | Present? | Formal / Inline / Implied | Evidence |
| --------- | -------: | ------------------------- | -------- |
| SUPPLIES | PARTIAL | Legacy only, different target | `backend/app/graph_contract.py:90` has `SUPPLIES` from `Supplier` to `Commodity`, not the requested core Invoice/Supplier/PO/Receipt relationship. |
| MATCHES_PO | NO | Not found | Legacy has `REFERENCES` at `backend/app/graph_contract.py:87`; formal has `MATCHED_TO` at `backend/app/graph_contract.py:126`, not `MATCHES_PO`. |
| TRIGGERED_EXCEPTION | NO | Not found | Search found `TRIGGERED_EVOLUTION` in `backend/app/db/neo4j.py:229`, not `TRIGGERED_EXCEPTION`. |

### Serialization / _S Pattern

* `_S` exists: YES, but in ci-platform, not S2P source.
* Safe quoting pattern: YES in shared AGEClient; NO direct S2P usage found.
* Evidence: `ci-platform/ci_platform/graph/age_client.py:73` defines `AGEClient`; `ci-platform/ci_platform/graph/age_client.py:193-214` defines `serialize_for_age`; `ci-platform/ci_platform/graph/age_client.py:216-218` defines `_S`. S2P source search found no production `def _S`.

### P36 Schema Status

* Verdict: SUPPLEMENT
* Remaining scope: tighten the formal S2P graph contract around `Invoice`, `Supplier`, `PurchaseOrder`, and `GoodsReceipt`; add/standardize required edge names and safe AGE serialization usage.
* Likely later files: add or extend S2P graph contract code, likely alongside new `backend/app/s2p_age_graph.py`; avoid `backend/app/db/neo4j.py`.

## Part 4 - Storage and Decision Write Paths

### Storage usage

| Storage / Client | Used? | File / Function | Evidence |
| ---------------- | ----: | --------------- | -------- |
| AGEClient from ci-platform | PARTIAL / indirect | Shared client exists; S2P uses SDK graph factory, not direct import. | `ci-platform/ci_platform/graph/age_client.py:73`; `backend/app/s2p_graph_status.py:273-283` calls `create_graph_store(backend="age", ...)`. |
| SQLiteGraphStore | YES | Main scorer fallback/default graph store. | `backend/app/main.py:9` imports `SQLiteGraphStore`; `backend/app/main.py:61-72` builds `CompoundingScorer` with a selected graph store. |
| GraphStore | YES / abstract | Active graph store adapter wraps a created graph store. | `backend/app/s2p_graph_status.py:182-255` defines `S2PActiveAGEGraphStore`; `backend/app/s2p_graph_status.py:258-285` creates it. |
| DecisionStore | NO obvious S2P graph foundation usage | Search found no S2P-specific DecisionStore graph path. | No direct S2P decision-store graph traversal evidence found in inspected search output. |
| EvidenceLedger | YES, framework audit only | Framework audit uses ci-platform evidence ledger. | `backend/app/framework/audit.py:17` imports `EvidenceLedger`; `backend/app/framework/audit.py:24` creates `_LEDGER`. |
| raw sqlite | YES indirectly | `SQLiteGraphStore` stores scorer decisions in `s2p.db`. | `backend/app/main.py:76-80` initializes scorer with `DATA_DIR / "s2p.db"` and sets `app.state.graph_store`. |
| neo4j | YES legacy | SOC Neo4j client and S2PDecision writes. | `backend/app/db/neo4j.py`; `backend/app/domains/s2p/graph.py:12`; `backend/app/routers/s2p.py:1308-1311`. |

### Decision write paths

| File | Function / Signal | What is written | Evidence |
| ---- | ----------------- | --------------- | -------- |
| `backend/app/s2p_graph_status.py` | `S2PActiveAGEGraphStore.write_decision` | Protocol v2 governed decision into active AGE graph store. | `backend/app/s2p_graph_status.py:191-252`, calling `write_governed_decision` at `backend/app/s2p_graph_status.py:231-251`. |
| `backend/app/routers/s2p.py` | `/score` route | Score route writes through scorer and additionally calls legacy `write_s2p_decision`. | `backend/app/routers/s2p.py:1256`; `backend/app/routers/s2p.py:1308-1311`. |
| `backend/app/domains/s2p/graph.py` | `write_s2p_decision` | Legacy `S2PDecision` and relationships through Neo4j Cypher. | `backend/app/domains/s2p/graph.py:12`; `backend/app/domains/s2p/graph.py:32`; `backend/app/domains/s2p/graph.py:77`; `backend/app/domains/s2p/graph.py:101`. |
| `backend/app/routers/s2p.py` | `/outcome` route | Outcome learning and legacy outcome graph writes. | `backend/app/routers/s2p.py:1491`; `backend/app/routers/s2p.py:1537-1539`. |

### P38 Storage Scope

* Existing AGE usage: PARTIAL. AGE active graph-store support is present through `s2p_graph_status.py` and SDK graph factory, but S2P app code does not directly import ci-platform AGEClient for traversal.
* Existing traversal API: NO for S2P AGE. The only S2P graph router found is `/api/s2p/graph/status`.
* Verdict: SUPPLEMENT
* Scope: add a new `backend/app/s2p_age_graph.py` traversal/query module and router wiring that uses the shared ci-platform AGE path. Do not modify or reuse legacy `db/neo4j.py`.

## Part 5 - S2P main.py Router Registrations

### Registered routers

| Router / Factory | Prefix | Tags | Source | Evidence |
| ---------------- | ------ | ---- | ------ | -------- |
| `learn_router` | `/api` | `S2P` | `app.routers.s2p` | `backend/app/routers/s2p.py:43`; registered at `backend/app/main.py:91`. |
| `create_conservation_router("s2p", ...)` | `/api` | SDK-defined | `copilot_sdk.backend` | Imported at `backend/app/main.py:7`; registered at `backend/app/main.py:92-98`. |
| `create_transfer_router(app.state.scorer)` | SDK-defined | SDK-defined | `copilot_sdk.backend.transfer_router` | Imported at `backend/app/main.py:8`; registered at `backend/app/main.py:99`. |
| `s2p_router` | `/api/s2p` | `S2P` | `app.routers.s2p` | `backend/app/routers/s2p.py:42`; registered at `backend/app/main.py:100`. |
| `s2p_evolution_router` | app-local | app-local | `app.routers.s2p_evolution` | Imported at `backend/app/main.py:24`; registered at `backend/app/main.py:102`. |
| `s2p_explorer_router` | `/api/s2p/explorer` | `s2p-explorer` | `app.routers.s2p_explorer` | `backend/app/routers/s2p_explorer.py:17`; registered at `backend/app/main.py:103`. |
| `s2p_graph_status_router` | `/api/s2p/graph` | `s2p-graph` | `app.s2p_graph_status` | `backend/app/s2p_graph_status.py:17`; registered at `backend/app/main.py:118`. |
| Other S2P routers | app-local | app-local | audit/export/control/discovery/simulation/insight/evidence/governance/performance/PVG/novelty/clustering/early-warning/payment/suppliers/preview | Registered at `backend/app/main.py:101-117`. |

### SDK router registration

* create_scoring_router registered: NO. S2P uses app-local `s2p_router` score/learn routes.
* create_conservation_router registered: YES.
* create_evolution_router registered: NO. S2P has app-local `s2p_evolution_router`.
* create_transfer_router registered: YES.
* mount_self_computation_router registered: NO.

### S2P-specific routes

* auto-approve route registered: YES, inside `s2p_router`, with `/api/s2p/auto-approve/stats` and `/api/s2p/auto-approve/expansion-proof` at `backend/app/routers/s2p.py:1365` and `backend/app/routers/s2p.py:1370`.
* s2p_explorer / dk_weights registered: YES. `backend/app/routers/s2p_explorer.py:479` exposes `/dk-weights`; router is registered at `backend/app/main.py:103`.
* graph route registered: YES, status only. `backend/app/s2p_graph_status.py:404` exposes `/status`; router registered at `backend/app/main.py:118`.
* API prefix confirmed: `/api/s2p` for main S2P, `/api/s2p/explorer` for explorer, `/api/s2p/graph/status` for graph status.

## Part 6 - Existing Cypher / AGE Queries

| File | Line | Query / Signal | Safe Serialization? | Relevance |
| ---- | ---: | -------------- | ------------------- | --------- |
| `backend/app/domains/s2p/graph.py` | 32 | `MERGE (d:S2PDecision {decision_id: $decision_id})` | Parameterized Neo4j, not AGE `_S`. | S2P legacy decision graph write. |
| `backend/app/domains/s2p/graph.py` | 77 | `MATCH (d:S2PDecision {decision_id: $decision_id})` | Parameterized Neo4j, not AGE `_S`. | S2P legacy decision relationship update. |
| `backend/app/domains/s2p/graph.py` | 101 | `MATCH (d:S2PDecision {decision_id: $decision_id})` | Parameterized Neo4j, not AGE `_S`. | S2P legacy outcome graph update. |
| `backend/app/routers/framework_router.py` | 559-637 | `/soc/graph/*` query/top-nodes/neighbors/summary/prebuilt endpoints. | Uses `GraphExplorerService.run_safe_query`, but backed by legacy `neo4j_client`. | Active graph traversal exists only under SOC paths, not S2P AGE. |
| `backend/app/db/neo4j.py` | 57-93 | SOC context traversal over Alert/Asset/User/Playbook/etc. | Neo4j parameterized driver, not AGE `_S`. | Legacy SOC graph context. |
| `backend/app/s2p_graph_status.py` | 273-283 | `create_graph_store(backend="age", domain=..., graph_name=...)` | Delegates to SDK graph factory. | AGE store creation, not traversal query. |

## Part 7 - Enrichment Groundwork for P37/P39

| Signal / Relationship | Present? | File / Evidence | P37/P39 Impact |
| --------------------- | -------: | --------------- | -------------- |
| Supplier to invoice | PARTIAL | `backend/app/graph_contract.py:125` formal `SUPPLIED_BY` from `Invoice` to `Supplier`; S2PDecision writes in `backend/app/domains/s2p/graph.py`. | Useful schema signal, but no AGE enrichment write path found. |
| Invoice to PO | PARTIAL | `backend/app/graph_contract.py:126` formal `MATCHED_TO` from `Invoice` to `PurchaseOrder`; legacy `REFERENCES` at `backend/app/graph_contract.py:87`. | Needs standardization to required `MATCHES_PO` or clear mapping. |
| PO to GoodsReceipt | PARTIAL | Legacy `MATCHED_TO` Invoice->GoodsReceipt at `backend/app/graph_contract.py:88`; seed process has goods receipt at `backend/app/seed_graph.py:31-32`. | Formal contract and AGE write path missing. |
| invoice match | YES / partial | `backend/app/domains/s2p/config.py:59-60` references invoice quantity and duplicate-risk matching; routers contain matching-related logic. | Domain logic exists, graph relationship enrichment remains missing. |
| supplier risk / score | YES | `backend/app/domains/s2p/factors.py:25`, `backend/app/domains/s2p/factors.py:209-228`; supplier routers and accumulators also use exception/risk profiles. | Good input signals for enrichment. |
| exception trigger | PARTIAL | `backend/app/graph_contract.py:37` supplier `exception_rate`; discovery and early-warning routes include exception clusters and supplier exception history. | Needs explicit `TRIGGERED_EXCEPTION` edge. |
| commodity / lead time / cycle time | YES | `backend/app/domains/s2p/config.py:44`, `backend/app/domains/s2p/factors.py:258-310`, discovery route commodity and lead-time signals. | Good enrichment data, no AGE relationship write path found. |
| control tower / PVG relationship | YES / not graph | `backend/app/main.py:104` registers control tower; `backend/app/main.py:111` registers PVG. | Feature signals exist but were not found as graph enrichment. |
| graph enrichment service | NO | Search found no S2P AGE graph enrichment service or `s2p_age_graph.py`. | P39 remains dependent on P36/P38. |

## Final P36-P39 Scope Table

| Prompt | Verdict | Scope | Key files to create/modify later | Key Evidence |
| ------ | ------- | ----- | -------------------------------- | ------------ |
| P36 S2P-GRAPH-SCHEMA | SUPPLEMENT | Formal graph contract exists but must be aligned to required nodes/edges and AGE serialization. | `backend/app/graph_contract.py` or new S2P AGE contract module; likely `backend/app/s2p_age_graph.py`. | Formal contract at `backend/app/graph_contract.py:108-134`; missing formal GoodsReceipt and required `MATCHES_PO`/`TRIGGERED_EXCEPTION`. |
| P37 S2P-ENRICHMENT | SUPPLEMENT | Domain data and signals exist, but graph enrichment write path is missing. | Add enrichment service after schema decision; likely use `backend/app/s2p_age_graph.py` plus S2P domain data helpers. | Supplier/commodity/exception signals in `domains/s2p/factors.py`, discovery routes, and graph contract; no graph enrichment service found. |
| P38 S2P-GRAPH-TRAVERSAL | SUPPLEMENT | Add S2P AGE traversal/query API. Existing graph route is status-only. | Create `backend/app/s2p_age_graph.py`; register router in `backend/app/main.py`; use ci-platform AGEClient/shared graph stack. | `backend/app/s2p_graph_status.py:17` prefix and `backend/app/s2p_graph_status.py:404` `/status`; SOC graph explorer exists at `framework_router.py:559-637` but uses legacy `neo4j_client`. |
| P39 S2P-GRAPH-ENRICHMENT | FULL | Implement AGE graph enrichment after P36/P38 establish contract and traversal. | Likely `backend/app/s2p_age_graph.py` plus enrichment/write helpers. | No AGE enrichment endpoint/service found; `s2p_graph_status.py:348-355` says evidence receipt mapping and receipt mapping are not active/first-cutover. |

## Architecture Guardrails for Later Implementation

* Do not modify legacy `backend/app/db/neo4j.py` unless a later reviewed plan explicitly proves it is active and must change for S2P AGE work.
* Use ci-platform `AGEClient`; do not implement a new AGE client.
* Put S2P-specific AGE traversal in a new S2P backend module, likely `backend/app/s2p_age_graph.py`, because no better existing S2P AGE traversal module was found.
* Use safe Cypher serialization / `_S` pattern from the shared AGE client path.
* Keep graph writes/read paths aligned with canonical SDK/platform `GraphStore` and decision-write patterns where applicable.
* Avoid parallel in-memory graph state that bypasses persistent stores.
* Preserve existing API prefixes and router registration patterns: `/api/s2p`, `/api/s2p/explorer`, and `/api/s2p/graph`.

## Diagnostic Limitations

* This diagnostic does not run tests.
* This diagnostic does not validate runtime API behavior.
* This diagnostic does not validate live AGE/Postgres connectivity.
* This diagnostic does not validate frontend/UI graph rendering.
* This diagnostic does not prove production data connectivity.
* DROP means source-level/API-layer evidence suggests no implementation prompt is needed, not that E2E validation passed.

## Recommended Next Step

Run a targeted P36/P38 supplement prompt: define the canonical S2P AGE contract for Invoice, Supplier, PurchaseOrder, and GoodsReceipt; add `backend/app/s2p_age_graph.py`; use the shared ci-platform AGEClient/SDK graph path; register S2P traversal endpoints under `/api/s2p/graph`; leave `backend/app/db/neo4j.py` untouched.
