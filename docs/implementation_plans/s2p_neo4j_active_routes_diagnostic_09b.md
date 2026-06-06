# S2P Neo4j Active Routes Diagnostic 09b

Date: 2026-06-05
Model: gpt-5.3
Task Type: Diagnostic document creation only; no source code changes
Repo: s2p-copilot
Diagnostic Scope: Full source/config/test chase of active S2P Neo4j write/read paths, AGE parallel graph-store path, failure behavior, and fix scope before P36-P39.
Prior Diagnostics Read: YES - `s2p-copilot/docs/implementation_plans/s2p_graph_foundation_diagnostic_09.md`; YES - `s2p-copilot/docs/implementation_plans/s2p_panel_p40_diagnostic_04.md`; YES - `copilot-sdk/docs/implementation_plans/sdk_backend_endpoint_map_diagnostic_02.md`.

## Executive Summary

- Scenario classification: D - unclear runtime state, with confirmed source-level silent Neo4j write attempts on production S2P routes. Source/config inspection did not prove whether Neo4j is running.
- Is this a production data integrity issue: YES / UNCLEAR runtime. The source has production `/api/s2p/score` and `/api/s2p/outcome` Neo4j calls that are swallowed on failure.
- Does Neo4j write path use AGE-incompatible Cypher: YES. It uses `MERGE`, `MATCH`, and Neo4j `$param` query style in `backend/app/domains/s2p/graph.py`.
- Are Neo4j failures silent: YES for `/score`, `/outcome`, and learning-gate Neo4j count fallback.
- Is there an AGE equivalent write path: PARTIAL. `S2PActiveAGEGraphStore.write_decision` can write governed decisions through SDK graph store when active AGE is configured, but it is not equivalent to the legacy `S2PDecision` Neo4j decision/outcome helper.
- Minimum safe fix: remove or gate production S2P route calls to `app.domains.s2p.graph` and replace graph write/read needs with the active SDK/AGE graph store path; add loud diagnostics or tests for graph write failures.
- Whether P36/P38 must ship first: UNCLEAR. Stopping silent Neo4j calls can be a P0 fix, but full S2P AGE traversal/enrichment replacement depends on P36/P38.
- New MAP item needed: YES.

## Path Resolution

- CLAUDE_S2P value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
- CLAUDE_SDK value: `C:\Users\baner\CopyFolder\IOT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk`
- Active S2P repo used: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot`
- Fallback repo used: NO
- Report path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\docs\implementation_plans\s2p_neo4j_active_routes_diagnostic_09b.md`
- graph.py path: `backend/app/domains/s2p/graph.py`
- db/neo4j.py path: `backend/app/db/neo4j.py`
- routers/s2p.py path: `backend/app/routers/s2p.py`
- s2p_graph_status.py path: `backend/app/s2p_graph_status.py`
- framework_router.py path: `backend/app/routers/framework_router.py`
- tests path: `backend/tests`
- Prior diagnostics found: Diagnostic 09 YES; Diagnostic 04 YES; SDK Diagnostic 02 YES.

## CLAUDE.md Relevant Notes

`CLAUDE.md` states docs are aspirational until code proves them, code/tests beat docs, S2P is independent, S2P must never depend on SOC-specific code/constants, git must not be used, and tests normally run from `backend/tests`. This diagnostic did not run tests because the prompt forbids tests.

## Part 1 - write_s2p_decision Full Trace

- File: `backend/app/domains/s2p/graph.py`
- Function: `write_s2p_decision`
- Neo4j client method called: `driver.session()` and `session.run(...)` at `backend/app/domains/s2p/graph.py:47-60`.
- Try/except in graph.py: NO. `write_s2p_decision`, `write_s2p_outcome`, and `get_s2p_decision` contain no local `try/except`.
- AGE compatibility verdict: NOT AGE-compatible as written.

Node writes:
- Label: `S2PDecision`
- Properties: `decision_id`, `event_id`, `category`, `action`, `action_index`, `confidence`, `factor_vector`, `factor_names`, `supplier_id`, `amount`, `timestamp`, `outcome`.
- Evidence: `backend/app/domains/s2p/graph.py:31-44` builds `MERGE (d:S2PDecision {decision_id: $decision_id})` and `SET` properties; `backend/app/domains/s2p/graph.py:47-60` passes parameters.

Edge writes:
- Edge type: None in `write_s2p_decision`.
- From: N/A
- To: N/A
- Properties: N/A
- Evidence: `backend/app/domains/s2p/graph.py:31-45` only merges/sets one `S2PDecision` node and returns `d.decision_id`.

Outcome write function:
- Function: `write_s2p_outcome`
- Node labels: `S2PDecision`
- Edge types: None.
- Properties: `outcome`, `analyst_action`, `analyst_id`, `outcome_ts`.
- Evidence: `backend/app/domains/s2p/graph.py:65-95` matches `S2PDecision` by `decision_id`, sets outcome fields, and returns whether a record exists.

Forbidden AGE patterns:
- MERGE: YES. `backend/app/domains/s2p/graph.py:32`.
- `$param`: YES. `backend/app/domains/s2p/graph.py:32-43`, `backend/app/domains/s2p/graph.py:77-81`, `backend/app/domains/s2p/graph.py:101`.
- ON CREATE SET: NO in this file.
- Other: Neo4j driver/session API is used synchronously (`driver.session()`, `session.run`) at `backend/app/domains/s2p/graph.py:47-60`, `backend/app/domains/s2p/graph.py:86-94`, and `backend/app/domains/s2p/graph.py:104-109`.
- Evidence: ci-platform AGEClient explicitly rejects `MERGE` as unsupported by Apache AGE at `ci-platform/ci_platform/graph/age_client.py:54-67`.

## Part 2 - neo4j_client Initialization and Failure Behavior

- File: `backend/app/db/neo4j.py`
- Initialization style: lazy global singleton. `neo4j_client = Neo4jClient()` is created at import time, but `_driver` starts as `None`.
- URI/env vars: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
- Hardcoded defaults: user defaults to `neo4j`; URI and password default to `None`.
- Driver used: `neo4j.AsyncGraphDatabase` and `AsyncDriver`.
- Import-time behavior: imports the `neo4j` package and constructs `Neo4jClient`; no connectivity check at import.
- First-query behavior: `session()` calls async `connect()` if `_driver` is not initialized, then uses async session.
- Failure handling: no generic health/failure handling in the client for write helpers; some SOC helper methods return zero on exception.
- Health check: no Neo4j health endpoint found; app `/health` only returns service/version.
- Evidence: imports at `backend/app/db/neo4j.py:8`; env reads at `backend/app/db/neo4j.py:17-21`; connect/session at `backend/app/db/neo4j.py:23-49`; singleton at `backend/app/db/neo4j.py:357`; app health at `backend/app/main.py:121-123`.

Important compatibility note: `db/neo4j.py` exposes an async context manager `session()` (`backend/app/db/neo4j.py:37-44`), but `domains/s2p/graph.py` uses it synchronously with `with driver.session() as session` (`backend/app/domains/s2p/graph.py:47`, `backend/app/domains/s2p/graph.py:86`, `backend/app/domains/s2p/graph.py:104`). That mismatch means the production S2P helper call is likely to raise before any graph write unless a different mock/driver is passed.

## Part 3 - s2p.py Call Sites and Error Handling

Call site 1:
- File/line: `backend/app/routers/s2p.py:1307-1323`
- Route/function: `/api/s2p/score`, route starts at `backend/app/routers/s2p.py:1256`.
- Before or after scorer/ledger write: after `scorer.score(...)` at `backend/app/routers/s2p.py:1298-1303`, before auto-approve/novelty/shadow response work.
- Try/except: YES.
- Failure behavior: silent `pass` at `backend/app/routers/s2p.py:1322-1323`.
- Evidence: imports and call at `backend/app/routers/s2p.py:1307-1321`; silent except at `backend/app/routers/s2p.py:1322-1323`.

Call site 2:
- File/line: `backend/app/routers/s2p.py:1536-1542`
- Route/function: `/api/s2p/outcome`, context in outcome processing.
- Before or after scorer/ledger write: after `_append_evidence_receipt_before_outcome(...)` at `backend/app/routers/s2p.py:1525-1535`, before `_learn_with_scorer(...)` at `backend/app/routers/s2p.py:1544-1550`.
- Try/except: YES.
- Failure behavior: silent `pass` with comment `Neo4j unavailable - outcome still processed`.
- Evidence: `backend/app/routers/s2p.py:1536-1542`.

Route continuation:
- Score route continues after Neo4j failure: YES. It proceeds to conservation, auto-approve, novelty, `_link_decision_to_invoice`, shadow, and returns response at `backend/app/routers/s2p.py:1325-1362`.
- Outcome route continues after Neo4j failure: YES. It proceeds to `_learn_with_scorer`, receipts, supplier profile, evolver, shadow, and returns payload at `backend/app/routers/s2p.py:1544-1592`.
- Centroid update risk: direct scorer update appears already done before the Neo4j score helper; outcome learning happens after the swallowed Neo4j outcome helper.
- Ledger/audit risk: graph helper failure is silent, while scorer/receipt paths can still proceed; this creates a source-level risk of missing graph-only `S2PDecision` history.
- Additional read fallback: learning gate reads Neo4j counts in `backend/app/routers/s2p.py:1623-1647` and silently falls back to cold-start defaults.

## Part 4 - AGE Parallel Write Path

- File: `backend/app/s2p_graph_status.py`
- Class: `S2PActiveAGEGraphStore`
- write_decision behavior: validates domain `s2p`, requires `metadata.decision_id`, builds factor vector/action/category/probabilities, then delegates to `_store.write_governed_decision(...)`.
- Node labels: not defined in this wrapper; delegated to SDK graph store.
- Properties: `decision_id`, `domain`, `category`, `category_index`, `recommended_action`, `recommended_index`, `confidence`, `probabilities`, `factor_vector`, `factor_names`, `source`, `scorer_version`, `preset_version`, `factor_schema_version`, and metadata including `active_age`.
- Edge types: not defined in this wrapper.
- Query form: no direct Cypher in wrapper; delegates to SDK store from `copilot_sdk.graph.factory.create_graph_store`.
- Called from same /score route: indirectly, only if `app.state.scorer.graph_store` is an active AGE wrapper. `main.py` passes `create_s2p_active_graph_store(...)` to the scorer at `backend/app/main.py:75-80`; `/score` calls `scorer.score(...)` at `backend/app/routers/s2p.py:1298-1303`.
- Called from different path: the legacy Neo4j helper is a separate route-level call after scoring.
- Duplicate write risk: YES if active AGE is enabled and the legacy Neo4j helper also succeeds; the same decision may be represented in AGE/SDK graph store and Neo4j `S2PDecision`.
- Data loss risk if Neo4j removed: PARTIAL / needs replacement. Removing Neo4j route helper would not remove SDK scorer writes, but would remove the legacy `S2PDecision` node/update shape and any code reading only Neo4j `S2PDecision`.
- Evidence: wrapper at `backend/app/s2p_graph_status.py:182-252`; factory at `backend/app/s2p_graph_status.py:258-285`; main wiring at `backend/app/main.py:75-80`; graph status flags at `backend/app/s2p_graph_status.py:332-400`.

## Part 5 - framework_router.py Neo4j Dependency

- File: `backend/app/routers/framework_router.py`
- Neo4j imports: `from app.db.neo4j import neo4j_client` at `backend/app/routers/framework_router.py:20`.
- Endpoints: many `/soc/...` endpoints call Neo4j, including `/soc/centroid-evolution`, `/soc/convergence-calendar`, `/soc/ols-status`, `/soc/flywheel-comparison`, `/soc/iks-trend`, `/soc/shadow/*`, `/soc/checkpoint/*`, `/soc/auto-approve-stats`, `/soc/graph/*`, and `/soc/learning-health`.
- Registered in main.py: NO evidence of registration. `backend/app/main.py:91-118` registers S2P routers and graph status but not `framework_router`.
- S2P-facing: not in current `main.py`; endpoint paths are SOC-prefixed.
- SOC legacy suspicion: HIGH. File doc says domain-agnostic, but endpoint paths and examples are `/soc/...`; it uses SOC services such as `app.services.convergence_calendar` with `SOC_FACTORS` at `backend/app/routers/framework_router.py:125-155`.
- Breakage risk if removed: current S2P `main.py` likely unaffected because it does not register this router; tests/imports may still depend on framework importability.
- Recommendation: leave alone for this fix unless a later reviewed plan migrates/deprecates unused SOC framework routes.
- Evidence: import at `backend/app/routers/framework_router.py:20`; endpoints at `backend/app/routers/framework_router.py:74`, `125`, `194`, `267`, `341`, `390`, `421`, `503`, `559-637`, `647`; main registrations at `backend/app/main.py:91-118`.

## Part 6 - Environment and Dependency Check

- neo4j dependency present: YES. `pyproject.toml:8` includes `"neo4j>=5.0.0"`.
- py2neo dependency present: NO evidence found.
- NEO4J_URI or bolt config: NO backend `.env` file found and no `.env` lines with Neo4j/bolt settings were output.
- CLAUDE.md mentions Neo4j startup: NO evidence found.
- CLAUDE.md mentions PostgreSQL/AGE startup: NO evidence found in searched lines.
- Runtime service check performed: NO. This diagnostic stayed source/config/test-only.
- Neo4j expected to be running: UNCLEAR from local source/config. Dependency exists, but no env/docs startup evidence was found.
- Scenario implication: D with a confirmed source-level risk. If Neo4j is absent, the route silently drops legacy graph writes. If Neo4j is present and active AGE is configured, graph data can split across Neo4j and AGE/SQLite.
- Evidence: no `.env` files under backend from env search; dependency at `pyproject.toml:8`; CLAUDE.md graph DB search found S2P/SOC warnings but no Neo4j/AGE startup lines.

## Part 7 - Blast Radius

Neo4j import/reference files:
- File: `backend/app/db/neo4j.py`; Line: 8, 26, 357; Reference: Neo4j driver and singleton; Production route or helper: helper imported by S2P routes; Evidence: lines listed.
- File: `backend/app/domains/s2p/graph.py`; Line: 12; Reference: `write_s2p_decision`; Production route or helper: helper called by `/api/s2p/score`; Evidence: `backend/app/routers/s2p.py:1307-1321`.
- File: `backend/app/routers/s2p.py`; Lines: 1308-1311, 1537-1539, 1623-1626; Reference: imports/calls `neo4j_client`, `write_s2p_decision`, `write_s2p_outcome`, and direct Neo4j count queries; Production route or helper: YES.
- File: `backend/app/routers/framework_router.py`; Lines: 20 and many route calls; Reference: `neo4j_client`; Production route or helper: not registered in current S2P main, but source exists.
- File: `backend/app/framework/*`; multiple references to Neo4j service abstractions; Production route or helper: mostly framework helpers used by unregistered `framework_router.py`.

Tests:
- Test file: `backend/tests/test_s2p_graph.py`; Lines: 1-5, 15, 40-55, 80-83; What is tested: mock Neo4j driver with `write_s2p_decision` and `get_s2p_decision`; Would break if Neo4j helper is removed unless replaced/updated.
- Test file: `backend/tests/test_evidence_receipt_wiring.py`; Lines: 266-306; What is tested: receipt failure blocks Neo4j outcome write; Would need update if Neo4j outcome write is removed.
- Test file: `backend/tests/test_evidence_receipt_wiring.py`; Lines: 310-350; What is tested: outbox fallback precedes Neo4j outcome write; Would need update if Neo4j outcome write is removed.

Minimum change to stop production Neo4j calls:
- File: `backend/app/routers/s2p.py`
- Line/function: `/api/s2p/score` block at `backend/app/routers/s2p.py:1307-1323`; `/api/s2p/outcome` block at `backend/app/routers/s2p.py:1536-1542`; learning gate Neo4j fallback at `backend/app/routers/s2p.py:1623-1647`.
- Change needed later: remove or replace route-level Neo4j helper calls with supported graph-store/AGE path and make graph write failure behavior explicit.
- Replacement needed first: For score writes, SDK graph store write already exists through `scorer.score`; for outcome graph projections and learning-gate counts, replacement should use `scorer.graph_store`/SQLite/AGE or a new P36/P38 graph abstraction.

## Scenario Classification

Scenario: D - unclear runtime state with confirmed source-level silent legacy Neo4j calls.

Rationale: Source/config inspection found no backend `.env` Neo4j config and no CLAUDE.md Neo4j startup instructions, but the Neo4j package is a dependency and route-level imports/calls exist. The production runtime availability of Neo4j was not checked.

Data currently lost or duplicated:
- If Neo4j is not configured/running or the async/sync session mismatch raises, `S2PDecision` graph helper writes are silently lost in `/score` and `/outcome`.
- If Neo4j is configured/running and active AGE is also configured, data can be duplicated/split between Neo4j `S2PDecision`, SDK graph store SQLite/AGE governed decision records, and receipt/outcome stores.

AGE equivalent coverage: PARTIAL. `S2PActiveAGEGraphStore.write_decision` covers governed decision writes, not the legacy Neo4j `S2PDecision` node shape, `write_s2p_outcome`, or learning-gate count queries.

Production impact: HIGH enough for a P0/P1 bug-fix prompt before P36-P39 implementation. The code path is active under `/api/s2p/score` and `/api/s2p/outcome`, failures are swallowed, and tests intentionally preserve Neo4j ordering today.

Confidence: High for source-level findings; medium for runtime scenario because no service check was performed.

## Fix Scope Summary

Minimum safe fix:
1. File/function: `backend/app/routers/s2p.py` `/api/s2p/score`
   Required later change: remove/gate the `write_s2p_decision` Neo4j block at `backend/app/routers/s2p.py:1307-1323`; rely on scorer graph-store write or explicit AGE graph abstraction; do not silently swallow governed graph write failures without a documented policy.
   Evidence: silent `except Exception: pass` at `backend/app/routers/s2p.py:1322-1323`.
2. File/function: `backend/app/routers/s2p.py` `/api/s2p/outcome`
   Required later change: replace `write_s2p_outcome` Neo4j block at `backend/app/routers/s2p.py:1536-1542` with graph-store/AGE-compatible outcome projection or remove legacy graph-only update after verifying no reader depends on it.
   Evidence: comment says Neo4j unavailable and outcome still processed at `backend/app/routers/s2p.py:1541-1542`.
3. File/function: `backend/app/routers/s2p.py` learning gate
   Required later change: replace Neo4j count reads at `backend/app/routers/s2p.py:1623-1647` with scorer/graph-store counts so learning-gate state does not silently fall back to cold-start defaults.
   Evidence: `pass  # Neo4j unavailable - fall back to cold-start defaults` at `backend/app/routers/s2p.py:1647`.
4. File/function: `backend/tests/test_s2p_graph.py` and `backend/tests/test_evidence_receipt_wiring.py`
   Required later change: update tests to assert no production Neo4j write path and to cover AGE-disabled/AGE-enabled behavior.
   Evidence: Neo4j mock tests at `backend/tests/test_s2p_graph.py:1-5`, `backend/tests/test_s2p_graph.py:40-55`; Neo4j outcome ordering tests at `backend/tests/test_evidence_receipt_wiring.py:266-350`.

Whether P36/P38 must ship before fix:
- YES / NO / UNCLEAR: UNCLEAR.
- Rationale: A minimal P0 can stop silent legacy Neo4j calls without waiting for traversal. A complete replacement for graph projections, traversal, and enrichment should build on P36/P38.

New MAP item:
- YES / NO: YES
- Proposed ID: P36.5 or P0-S2P-NEO4J-DECOUPLE
- Proposed priority: P0 before P36-P39 implementation
- Proposed title: Remove Silent Neo4j Writes From Active S2P Routes
- Scope: Replace/remove production `write_s2p_decision`, `write_s2p_outcome`, and learning-gate Neo4j count dependencies in active S2P routes; preserve scorer/ledger/audit semantics; align with SDK/AGE graph-store path.
- Acceptance criteria: `/api/s2p/score` and `/api/s2p/outcome` do not import or call `app.db.neo4j`; graph write failures are explicit or routed through supported graph store; learning gate no longer silently depends on Neo4j `S2PDecision` counts; no SOC `/soc` framework router is registered in S2P; tests cover Neo4j-disabled and AGE-enabled paths.
- Tests required: route tests for score/outcome with Neo4j module absent; active AGE graph-store write test; learning-gate count source test; regression test that `framework_router` remains unregistered or explicitly deprecated.
- GPT-5.5 review required: YES, because this touches production data integrity and graph-store semantics.

## Architecture Guardrails for Later Fix

- Do not keep dual production graph writes unless there is an explicit dual-write migration plan and reconciliation test.
- Do not silently swallow graph write failures on governed decision/outcome paths.
- Do not implement a new AGE client; use ci-platform AGEClient or existing platform abstraction.
- Do not import router-local graph helpers into domain logic if it inverts dependencies.
- Do not delete legacy `neo4j.py` until all imports/routes/tests are migrated or explicitly deprecated.
- Use AGE-safe Cypher serialization; no Neo4j `MERGE`/`$param`/`ON CREATE SET` patterns in AGE paths.
- Preserve decision/outcome ordering: scorer/ledger/audit semantics must remain correct if graph write fails.
- Add regression tests later for both Neo4j-disabled and AGE-enabled paths.

## Diagnostic Limitations

- This diagnostic does not run tests.
- This diagnostic does not run service health checks.
- This diagnostic does not modify code.
- This diagnostic does not implement the AGE replacement.
- This diagnostic cannot prove runtime Neo4j availability without a runtime check.
- Verdicts are source/config inspection verdicts only.

## Recommended Next Step

Run a P0 production bug fixer or short repo-local design plan first. The prompt should decouple active S2P routes from legacy Neo4j writes and learning-gate reads, then update tests for Neo4j-disabled and AGE-enabled paths. P36/P38 can proceed after the route-level data integrity issue is bounded.
