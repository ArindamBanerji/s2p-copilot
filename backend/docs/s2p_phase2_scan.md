# S2P Phase 2 Scan - Remaining Gaps

## Executive Summary

The live S2P backend is farther along than the MAP queue implies: it has 85 mounted `/api/` routes, a real score endpoint that computes canonical factors from invoice plus graph context before scoring, a learn/outcome loop, fixture-backed discovery/simulation/process views, and frontend calls for most of these routes. S2P-P10 should be reduced, not kept as a broad "factor computers" item: the seven canonical factor computers exist and are covered, but `process_bottleneck_factor` is absent. S2P-P11 is PARTIAL: triage and learning endpoints exist, a reward function is wired into the scorer, prompt/rule variant exploration is configured through the S2P evolver, and process context is exposed through cache/fixture-backed endpoints; the remaining uncertainty is explicit score/learn credit assignment or exploration inside the live triage loop, if product scope requires it. The recommended S2P-PHASE2-IMPL scope is to keep only the precise missing pieces: decide whether to add `process_bottleneck_factor`, define any required score/learn credit-assignment behavior, define live process-context source contracts, and verify or clean up two generic exported frontend helpers that do not appear to be active UI blockers.

## Method and Scope

- Scan time: 2026-05-25 22:09:27 -07:00.
- Repo path: `C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\s2p-copilot\backend`.
- Read-only constraint: no source, test, or config edits.
- Only write: `docs/s2p_phase2_scan.md`.
- Tests were not run. The scan used source reads, route introspection, and safe GET probes only.
- `CLAUDE.md` was checked and was not present.
- Caveat: frontend code was found in the adjacent `copilot-sdk` repo under `apps/s2p/frontend/src`, not inside this backend repo.

## Section 1: Factor Computer Status

| File | Lines | Class/Function | Type | Protocol Compliance | Test Coverage | Notes |
|---|---:|---|---|---|---|---|
| `app/domains/s2p/factor_protocol.py` | 6-18 | `FactorComputer` | Protocol | Defines local runtime-checkable protocol | `tests/test_factors.py:46-48` checks factor instances | Protocol requires `name` and `compute(invoice, context)` |
| `app/domains/s2p/factors.py` | 126-147 | `MatchStatus` | Dedicated factor computer | Structural: has `name` and `compute` | `tests/test_factors.py:51-67` | Graph-first PO/GR logic with fallback |
| `app/domains/s2p/factors.py` | 150-178 | `AmountVarianceRatio` | Dedicated factor computer | Structural | `tests/test_factors.py:70-77` | Uses PO amount, variance pct, historical mean, or fallback |
| `app/domains/s2p/factors.py` | 181-206 | `DuplicateScore` | Dedicated factor computer | Structural | `tests/test_factors.py:80-96` | Uses graph invoice neighbors or fallback |
| `app/domains/s2p/factors.py` | 209-229 | `SupplierExceptionHistory` | Dedicated factor computer | Structural | `tests/test_factors.py:99-101` | Uses supplier graph exception rate or fallback |
| `app/domains/s2p/factors.py` | 232-255 | `PaymentTermsImpact` | Dedicated factor computer | Structural | `tests/test_factors.py:104-112` | Parses invoice/supplier payment days |
| `app/domains/s2p/factors.py` | 258-271 | `CommodityIndexCorrelation` | Dedicated factor computer | Structural | `tests/test_factors.py:115-117` | Uses commodity volatility or fallback |
| `app/domains/s2p/factors.py` | 274-290 | `TaxRegulatoryCompliance` | Dedicated factor computer | Structural | `tests/test_factors.py:120-135` | Uses contract graph evidence or metadata fallback |
| `app/domains/s2p/factors.py` | 293-312 | `ALL_FACTORS`, `FACTOR_NAMES`, `S2P_FACTOR_COMPUTERS` | Registry | Registry entries are protocol-compatible by test | `tests/test_factors.py:41-48` | Contains exactly seven canonical factors |
| `app/domains/s2p/factors.py` | 315-329 | `compute_all_factors` | Aggregator | Calls each dedicated factor | `tests/test_factors.py:138-181`; `tests/test_s2p_score_endpoint.py:121-138` | Computes all factors and falls back on individual failures |

- `process_bottleneck_factor`: ABSENT. A source search for `process_bottleneck_factor`, `process_bottleneck`, and `process bottleneck` found no matches in `app/**/*.py`. Bottleneck logic exists as process context helpers, not as a canonical factor computer.
- Conclusion: PARTIAL for S2P-P10. The canonical seven factor computers are BUILT and covered, but the named `process_bottleneck_factor` is not present.

Evidence:

- Active domain shape is 5 categories, 5 actions, and 7 factors in `app/domains/s2p/config.py:20-47` and `app/domains/s2p/config.py:80-83`.
- The local factor protocol is defined in `app/domains/s2p/factor_protocol.py:6-18`.
- The factor registry contains the seven canonical implementations in `app/domains/s2p/factors.py:293-312`.
- The aggregator computes every registered factor in `app/domains/s2p/factors.py:315-329`.
- Tests assert seven canonical factors and protocol compatibility in `tests/test_factors.py:41-48`.

## Section 2: Triage Pipeline Status

Current `/api/s2p/score` behavior:

- `router = APIRouter(prefix="/api/s2p")` is declared in `app/routers/s2p.py:36`.
- `POST /score` is declared in `app/routers/s2p.py:638-643`.
- The request schema accepts invoice/event metadata and optional explicit factor fields in `app/routers/s2p.py:601-619`.
- The endpoint validates the category against `S2PDomainConfig.categories` in `app/routers/s2p.py:644-649`.
- It builds an invoice from fixtures/request data in `app/routers/s2p.py:672-673`.
- It resolves graph context in `app/routers/s2p.py:676`.
- It computes factors through `compute_all_factors(invoice, context=context)` in `app/routers/s2p.py:677`.
- It builds the canonical factor vector in `app/routers/s2p.py:678`.
- It calls the scorer in `app/routers/s2p.py:679-685`.
- It writes decision graph links best-effort in `app/routers/s2p.py:689-705`.
- It returns action, probabilities, factor vector, decision id, auto-approve data, novelty score, and process context in `app/routers/s2p.py:721-735`.

Pipeline status:

- Factors are computed by the backend, not simply passed through. The optional request factor fields are fallback inputs, but live scoring calls `compute_all_factors` before scorer selection.
- Action selection is present through `scorer.score(...)` in `app/routers/s2p.py:681-685`.
- Learn/outcome loop is present:
  - Generic `POST /api/learn` is declared in `app/routers/s2p.py:792-794`.
  - S2P `POST /api/s2p/outcome` is declared in `app/routers/s2p.py:842-847`.
  - `_learn_with_scorer` calls `scorer.learn(...)` and maps missing decisions to 404 in `app/routers/s2p.py:492-517`.
  - Outcome handling verifies or creates a decision, then calls `_learn_with_scorer` in `app/routers/s2p.py:869-889`.
- Persistent state is present: `app.main` builds `CompoundingScorer.from_preset("s2p", graph_store=SQLiteGraphStore(...), reward_function=S2PRewardFunction())` in `app/main.py:54-60`, then stores it on `app.state` in `app/main.py:62-66`.
- Reward is implemented and wired:
  - `S2PRewardFunction.compute` returns graded rewards in `app/domains/s2p/reward.py:8-27`.
  - The reward function is passed into the scorer in `app/main.py:54-60`.
  - Triage outcomes are recorded into the S2P evolver with reward-derived success in `app/services/s2p_evolver.py:42-55`.
- RL/exploration/credit status: PARTIAL. The live path includes reward-backed scorer learning, and S2P prompt/rule variant exploration is configured separately through `S2P_EVOLVER_CONFIG` with `exploration_constant=1.414` in `app/domains/s2p/evolver_config.py:52-57`. That config is passed into `_s2p_evolver = PromptVariantEvolver(config=S2P_EVOLVER_CONFIG)` in `app/services/s2p_evolver.py:12-13`, and triage outcomes can be recorded against the evolver in `app/services/s2p_evolver.py:42-55`. This is not the same thing as explicit score/learn RL credit assignment in the live triage loop. The score and learn endpoints call the SDK scorer in `app/routers/s2p.py:681-685`, `app/routers/s2p.py:810-816`, and `app/routers/s2p.py:883-889`, but no local app-level `credit_assign`, `credit assignment`, `thompson`, or `bandit` implementation was found.

Conclusion: PARTIAL for S2P-P11. A triage plus learn loop is built; reward-backed learning is wired; prompt/rule variant exploration exists through the S2P evolver; explicit score/learn credit assignment or bandit policy in the live triage loop remains an implementation decision/unknown; process context is partial/static as described below.

## Section 3: Process Context Panel Status

Backend process/context endpoints and data:

- No standalone `/api/s2p/process-context` or `/api/s2p/context-panel` route appears in the route dump.
- Score responses embed process context:
  - `_score_process_context` loads Celonis cache candidates and extracts a bottleneck activity in `app/routers/s2p.py:59-87`.
  - `ScoreResponse` includes `process_context` in `app/routers/s2p.py:632`.
  - The score endpoint returns `process_context=_score_process_context()` in `app/routers/s2p.py:731`.
- Preview queue embeds process context:
  - `_load_celonis_cache` reads `celonis_process_data.json` candidates in `app/routers/s2p_preview.py:40-54`.
  - `_build_process_context` extracts bottleneck metadata in `app/routers/s2p_preview.py:57-76`.
  - `preview_queue` attaches process context to exceptions in `app/routers/s2p_preview.py:313-340`.
- Insight exposes a direct process endpoint:
  - `router = APIRouter(prefix="/api/s2p/insight")` in `app/routers/s2p_insight.py:17`.
  - `/process-signals` returns availability, process model, variant, activities, recommendations, and source in `app/routers/s2p_insight.py:154-167`.
- PVG exposes process-cycle data:
  - `_load_process_data` reads local and adjacent Celonis cache files in `app/routers/s2p_pvg.py:43-52`.
  - `/pvg/cycle-time` returns activities, total minutes, bottleneck name, bottleneck percent, and source in `app/routers/s2p_pvg.py:227-264`.
- Control tower references a process context panel name as metadata, not as a data endpoint: `evidence_panels` includes `process_context` in `app/routers/s2p_control_tower.py:24-30` and `app/routers/s2p_control_tower.py:45-50`.
- The S2P frontend calls the process-signals endpoint in `copilot-sdk/apps/s2p/frontend/src/api.ts:169-173`.

Static/fixture/live classification:

- Current process context is PARTIAL and cache/fixture-backed. The backend reads committed/local JSON cache files and returns embedded process signals; no evidence shows a live Celonis/SAP enrichment service or a dedicated panel endpoint.

Conclusion: PARTIAL. Backend data exists and frontend calls one backed process-signal route, but a dedicated live process context panel is not evidenced.

## Section 4: 404 Inventory

| Path Probed or Referenced | Status | Reason | Genuine Gap? | Evidence |
|---|---:|---|---|---|
| `/api/s2p`, `/api/s2p/` | 404 | Prefix only; no root route in route dump | No | Mounted child routes in `app/main.py:83-99`; route dump has child routes only |
| `/api/s2p/evolution`, `/api/s2p/evolution/` | 404 | Prefix only | No | Mounted at `app/main.py:84`; concrete routes are `/rules`, `/variants`, `/promotion-check`, `/reset`, `/shadow-results`, `/promoted` in route dump |
| `/api/s2p/governance`, `/api/s2p/governance/` | 404 | Prefix only | No | Mounted at `app/main.py:91`; concrete governance routes appear in route dump |
| `/api/s2p/explorer`, `/api/s2p/explorer/` | 404 | Prefix only | No | Mounted at `app/main.py:85`; concrete explorer routes appear in route dump |
| `/api/s2p/discovery`, `/api/s2p/discovery/` | 404 | Prefix only | No | Router prefix is `/api/s2p/discovery` in `app/routers/s2p_discovery.py:9`; concrete routes start at `app/routers/s2p_discovery.py:216` |
| `/api/s2p/simulation`, `/api/s2p/simulation/` | 404 | Prefix only | No | Router prefix is `/api/s2p/simulation` in `app/routers/s2p_simulation.py:10`; concrete routes start at `app/routers/s2p_simulation.py:194` |
| `/api/s2p/novelty`, `/api/s2p/novelty/` | 404 | Prefix only | No | Mounted at `app/main.py:94`; concrete novelty routes appear in route dump |
| `/api/s2p/suppliers`, `/api/s2p/suppliers/` | 200 | Real routes exist | No | Route dump includes both `/api/s2p/suppliers` and `/api/s2p/suppliers/` |
| `GET /api/learn` | 405 | POST-only route | No | `learn_router = APIRouter(prefix="/api")` in `app/routers/s2p.py:37`; `@learn_router.post("/learn")` in `app/routers/s2p.py:792-794` |
| `/api/score` | 404 | Generic score route absent; S2P score is `/api/s2p/score` | Possible frontend gap only if used | `@router.post("/score")` under `/api/s2p` in `app/routers/s2p.py:36` and `app/routers/s2p.py:638-643` |
| `/api/fingerprint` | 404 | Generic fingerprint route absent; S2P route is `/api/s2p/insight/fingerprint` | Not an active UI blocker based on source scan | Generic helper path exists in `copilot-sdk/apps/s2p/frontend/src/api.ts:100-102`; active panel uses `fetchS2PFingerprint` in `FactorFingerprintPanel.tsx:2` and `FactorFingerprintPanel.tsx:24`, which calls `/api/s2p/insight/fingerprint` in `api.ts:150-153`; backend route is `app/routers/s2p_insight.py:75-87` |
| `/api/trajectory` | 404 | Generic trajectory route absent; S2P route is `/api/s2p/performance/trajectory` | Not an active UI blocker based on source scan | Generic helper path exists in `copilot-sdk/apps/s2p/frontend/src/api.ts:104-106`; active chart uses `fetchS2PTrajectory` in `TrajectoryChart.tsx:3` and `TrajectoryChart.tsx:11`, which calls `/api/s2p/performance/trajectory` in `api.ts:243-244`; backend route is `app/routers/s2p_performance.py:70-86` |
| `/api/conservation`, `/api/conservation/` | 404 | Prefix only; `/api/conservation/status` exists | No | Conservation router mounted with prefix `/api` in `app/main.py:75-81`; route dump includes `/api/conservation/status` |
| `/api/self`, `/api/self/` | 404 | No self routes in app route dump | Genuine only if S2P frontend references it | S2P frontend scan found no `/api/self` calls; purchasing frontend has `/api/self/*`, but that is outside S2P |
| `/api/health` | 404 | Health is not under `/api` | No | `@app.get("/health")` in `app/main.py:102-104` |

Safe GET probe statuses:

- Prefix-only 404s: `/api/s2p`, `/api/s2p/evolution`, `/api/s2p/governance`, `/api/s2p/explorer`, `/api/s2p/discovery`, `/api/s2p/simulation`, `/api/s2p/novelty`, plus trailing-slash variants.
- Frontend-referenced 404s: `/api/fingerprint`, `/api/trajectory` appear as exported generic helper paths in `api.ts`; active S2P UI components found in this scan use the backed S2P-specific helpers instead.
- MAP-implied missing endpoints: no direct MAP path was supplied, but a dedicated process-context route and explicit exploration/credit-assignment endpoints are not present in the route dump.

## Section 5: Unknown / Extra Endpoints

Comparison basis: "unknown to MAP" means not named by the uploaded MAP queue items S2P-P10, S2P-P11, and S2P-PHASE2-IMPL.

| Method(s) | Path | Source Router | What It Appears To Do | Static/Live/Unknown | MAP Relevance |
|---|---|---|---|---|---|
| GET | `/api/s2p/control-tower/intents` | `s2p_control_tower.py` | Lists invoice triage intents | Static domain metadata | Extra Phase 2 capability |
| GET | `/api/s2p/control-tower/classify` | `s2p_control_tower.py` | Classifies invoice/category into intent and evidence panels | Fixture/computed | Relevant to triage/context |
| GET | `/api/s2p/control-tower/queue` | `s2p_control_tower.py` | Priority queue over fixture invoices | Fixture/computed | Relevant to triage |
| GET | `/api/s2p/evidence/*` | `s2p_evidence.py` | Audit pack, receipts, rules, compliance, chain integrity | Mixed fixture/store | Extra governance/evidence capability |
| GET | `/api/s2p/insight/fingerprint` | `s2p_insight.py` | Invoice factor fingerprint | Fixture/computed | Relevant to factors/context |
| GET | `/api/s2p/insight/similar` | `s2p_insight.py` | Similar invoices by factor distance | Fixture/computed | Extra triage evidence |
| GET | `/api/s2p/insight/cross-graph` | `s2p_insight.py` | Supplier/process impact summary | Fixture/cache-backed | Relevant to process context |
| GET | `/api/s2p/insight/process-signals` | `s2p_insight.py` | Process model, activities, recommendations | Cache-backed | Relevant to process context |
| GET | `/api/s2p/performance/*` | `s2p_performance.py` | Trajectory, what-if, summary from graph store | Store-backed with safe fallbacks | Extra learning/performance capability |
| GET | `/api/s2p/preview/*` | `s2p_preview.py` | Demo queue, config, conservation, suppliers, compounding | Fixture/demo | Extra preview capability |
| GET | `/api/s2p/pvg/*` | `s2p_pvg.py` | Process value graph leakage/impact/cycle-time | Fixture/cache-backed | Relevant to process value/context |
| GET | `/api/s2p/discovery/*` | `s2p_discovery.py` | Discovery, disruption, propagation examples | Static fixture tuples | Extra discovery capability |
| GET | `/api/s2p/simulation/*` | `s2p_simulation.py` | Disruption scenario simulation | Static fixture tuples | Extra simulation capability |
| GET | `/api/s2p/suppliers/*` | supplier routers | Supplier profiles, heatmap, clustering, warnings, payment | Fixture/computed | Extra supplier analytics |
| GET/POST | `/api/conservation/*` | SDK conservation router | Conservation status and what-if | Store-backed | Relevant to learning gate |
| GET | `/api/transfer/status` | SDK transfer router | Transfer status | Unknown | Extra infrastructure |

Source evidence:

- Control tower intent metadata and endpoints are in `app/routers/s2p_control_tower.py:16-52` and `app/routers/s2p_control_tower.py:116-173`.
- Discovery static tuples and endpoints are in `app/routers/s2p_discovery.py:12-52`, `app/routers/s2p_discovery.py:92-213`, and `app/routers/s2p_discovery.py:216-350`.
- Simulation static scenarios and endpoints are in `app/routers/s2p_simulation.py:13-154` and `app/routers/s2p_simulation.py:194-239`.
- PVG process/cache endpoints are in `app/routers/s2p_pvg.py:43-52` and `app/routers/s2p_pvg.py:128-264`.
- Performance graph-store endpoints are in `app/routers/s2p_performance.py:70-149`.

## Section 6: Frontend -> Backend Alignment

Frontend path found:

- `..\..\copilot-sdk\apps\s2p\frontend\src` exists.
- The requested `..\..\..\copilot-sdk\apps\s2p\frontend\src` path does not exist from the backend directory.

| Frontend Call | Frontend File:Line | Backend Route Match | Status | Notes |
|---|---|---|---|---|
| `/api/s2p/preview/queue` | `copilot-sdk/apps/s2p/frontend/src/api.ts:76-82` | `/api/s2p/preview/queue` | Backed | Preview router route dump |
| `/api/s2p/preview/conservation` | `api.ts:89-90`, `api.ts:108-110` | `/api/s2p/preview/conservation` | Backed | Fallback after conservation status |
| `/api/s2p/preview/suppliers` | `api.ts:93-97` | `/api/s2p/preview/suppliers` | Backed | Preview router route dump |
| `/api/fingerprint` | `api.ts:100-102` | No route | Exported generic helper only; not proven active | Active `FactorFingerprintPanel.tsx:2` and `FactorFingerprintPanel.tsx:24` use `fetchS2PFingerprint`, which calls backed `/api/s2p/insight/fingerprint` in `api.ts:150-153` |
| `/api/trajectory` | `api.ts:104-106` | No route | Exported generic helper only; not proven active | Active `TrajectoryChart.tsx:3` and `TrajectoryChart.tsx:11` use `fetchS2PTrajectory`, which calls backed `/api/s2p/performance/trajectory` in `api.ts:243-244` |
| `/api/conservation/status` | `api.ts:108-110` | `/api/conservation/status` | Backed | SDK conservation router mounted in `app/main.py:75-81` |
| `/api/s2p/score` | `api.ts:122-131` | `/api/s2p/score` | Backed | `app/routers/s2p.py:638-643` |
| `/api/s2p/outcome` | `api.ts:126-127` | `/api/s2p/outcome` | Backed | `app/routers/s2p.py:842-847` |
| `/api/learn` | `api.ts:134-135` | POST `/api/learn` | Backed | `app/routers/s2p.py:792-794` |
| `/api/s2p/evidence/template` | `api.ts:138-143` | `/api/s2p/evidence/template` | Backed | Evidence route dump |
| `/api/s2p/insight/fingerprint` | `api.ts:150-153` | `/api/s2p/insight/fingerprint` | Backed | `app/routers/s2p_insight.py:75-87` |
| `/api/s2p/insight/similar` | `api.ts:156-158` | `/api/s2p/insight/similar` | Backed | `app/routers/s2p_insight.py:90-119` |
| `/api/s2p/insight/cross-graph` | `api.ts:165-166` | `/api/s2p/insight/cross-graph` | Backed | `app/routers/s2p_insight.py:122-151` |
| `/api/s2p/insight/process-signals` | `api.ts:169-173` | `/api/s2p/insight/process-signals` | Backed | `app/routers/s2p_insight.py:154-167` |
| `/api/s2p/suppliers/early-warnings` | `api.ts:176-177` | `/api/s2p/suppliers/early-warnings` | Backed | Route dump |
| `/api/s2p/suppliers/trend-signals` | `api.ts:180-185` | `/api/s2p/suppliers/trend-signals` | Backed | Route dump |
| `/api/s2p/evidence/audit-trail/{invoice_id}` | `api.ts:191` | `/api/s2p/evidence/audit-trail/{invoice_id}` | Backed | Route dump |
| `/api/s2p/evidence/rules` | `api.ts:200` | `/api/s2p/evidence/rules` | Backed | Route dump |
| `/api/s2p/evolution/*` | `api.ts:204-228` | evolution routes | Backed | Route dump |
| `/api/s2p/evidence/compliance` | `api.ts:232` | `/api/s2p/evidence/compliance` | Backed | Route dump |
| `/api/s2p/discovery/alerts` | `api.ts:236` | `/api/s2p/discovery/alerts` | Backed | `app/routers/s2p_discovery.py:216-230` |
| `/api/s2p/discovery/disruptions` | `api.ts:240` | `/api/s2p/discovery/disruptions` | Backed | `app/routers/s2p_discovery.py:233-258` |
| `/api/s2p/performance/trajectory` | `api.ts:243-244` | `/api/s2p/performance/trajectory` | Backed | `app/routers/s2p_performance.py:70-86` |
| `/api/s2p/performance/what-if` | `api.ts:247-252` | `/api/s2p/performance/what-if` | Backed | `app/routers/s2p_performance.py:89-120` |
| `/api/s2p/performance/summary` | `api.ts:255-256` | `/api/s2p/performance/summary` | Backed | `app/routers/s2p_performance.py:123-149` |
| `/api/s2p/novelty/*` | `api.ts:259-265` | novelty routes | Backed | Route dump |
| `/api/s2p/explorer/*` | `api.ts:268-279` | explorer routes | Backed | Route dump |
| `/api/s2p/evidence/receipts`, `/chain-integrity`, `/audit-pack` | `api.ts:282-292` | evidence routes | Backed | Route dump |
| `/api/s2p/simulation/scenarios`, `/impact-summary` | `api.ts:295-300` | simulation routes | Backed | `app/routers/s2p_simulation.py:194-239` |
| `/api/s2p/discovery/extended` | `api.ts:303-304` | `/api/s2p/discovery/extended` | Backed | `app/routers/s2p_discovery.py:299-311` |
| `/api/s2p/governance/compliance-screening`, `/rationalization` | `api.ts:307-312` | governance routes | Backed | Route dump |
| `/api/s2p/auto-approve/*` | `api.ts:315-323` | core S2P auto-approve routes | Backed | Route dump |
| `/api/s2p/control-tower/*` | `api.ts:335-349` | control tower routes | Backed | `app/routers/s2p_control_tower.py:116-173` |
| `/api/s2p/pvg/*` | `api.ts:352-366` | PVG routes | Backed | `app/routers/s2p_pvg.py:133-264` |
| `/api/s2p/suppliers*` analytics calls | `api.ts:369-418` | supplier routes | Backed | Route dump |

Purchasing frontend calls were also scanned because the requested likely paths included `apps/purchasing/frontend/src`, but those calls are for another application and are not S2P alignment evidence.

Active-use nuance:

- `getFingerprint` and `getTrajectory` are exported generic helpers in `copilot-sdk/apps/s2p/frontend/src/api.ts:100-105`, and those generic backend paths are absent.
- The active S2P UI components found by source search use the S2P-specific backed helpers: `FactorFingerprintPanel.tsx:2` imports `fetchS2PFingerprint` and calls it at `FactorFingerprintPanel.tsx:24`; `TrajectoryChart.tsx:3` imports `fetchS2PTrajectory` and calls it at `TrajectoryChart.tsx:11`.
- Therefore `/api/fingerprint` and `/api/trajectory` should be treated as a cleanup/backward-compatibility or external-caller question, not as a Phase 2 implementation blocker, unless another active caller is found.

## Section 7: MAP Impact Assessment

| MAP Item | Status | Evidence | Recommendation |
|---|---|---|---|
| S2P-P10 factors | PARTIAL | Seven factor computers exist in `app/domains/s2p/factors.py:126-312`; protocol exists in `app/domains/s2p/factor_protocol.py:6-18`; process bottleneck factor search had no hits | REDUCE |
| S2P-P11 triage+RL+context | PARTIAL | Score computes factors and calls scorer in `app/routers/s2p.py:676-685`; learn/outcome loop exists in `app/routers/s2p.py:792-889`; reward function is wired in `app/main.py:54-60`; prompt/rule variant exploration is configured in `app/domains/s2p/evolver_config.py:52-57` and instantiated in `app/services/s2p_evolver.py:12-13`; process endpoints are cache-backed in `app/routers/s2p_insight.py:154-167` and `app/routers/s2p_pvg.py:227-264`; explicit score/learn credit assignment remains UNKNOWN/not evidenced | REDUCE |

## Section 8: Recommended S2P-PHASE2-IMPL Scope

1. Drop broad "build factor computers" work. Keep only a decision/implementation slice for `process_bottleneck_factor` if it remains a product requirement. It should be added as a canonical factor only if the domain shape intentionally changes from 5x5x7; otherwise it should stay a process-context signal.
2. Drop broad "build triage pipeline" work. The score -> computed factors -> scorer action -> decision id -> learn/outcome loop exists.
3. Reduce "RL" to a precise requirement. Current evidence supports reward-backed learning and prompt/rule variant exploration via the S2P evolver; it does not prove explicit score/learn credit assignment or bandit policy inside the live triage loop. Define whether that mechanism is required before implementation.
4. Reduce "process context panel" to frontend/backend integration details. Backend has `/api/s2p/insight/process-signals` and `/api/s2p/pvg/cycle-time`; if the panel must be live, specify the external source contract. If cache-backed is acceptable, wire the UI to the existing routes.
5. Do not implement generic `/api/fingerprint` and `/api/trajectory` solely from the exported helper scan. Active S2P UI components found here use backed S2P-specific calls, so the safer scope is to verify external usage and then clean up or preserve those generic helpers as a backward-compatibility decision.
6. Treat prefix-only 404s as non-issues. Do not add empty index endpoints unless UX/API discovery needs them.

## Appendix A: Route Dump

The live app introspection found 85 `/api/` routes:

```text
['GET'] /api/conservation/status
['POST'] /api/conservation/what-if
['POST'] /api/learn
['GET'] /api/s2p/auto-approve/expansion-proof
['GET'] /api/s2p/auto-approve/stats
['GET'] /api/s2p/control-tower/classify
['GET'] /api/s2p/control-tower/intents
['GET'] /api/s2p/control-tower/queue
['GET'] /api/s2p/discovery/alerts
['GET'] /api/s2p/discovery/disruptions
['GET'] /api/s2p/discovery/extended
['GET'] /api/s2p/discovery/propagation/{discovery_id}
['GET'] /api/s2p/discovery/supplier/{supplier_id}
['GET'] /api/s2p/evidence/audit-pack
['GET'] /api/s2p/evidence/audit-trail/{invoice_id}
['GET'] /api/s2p/evidence/chain-integrity
['GET'] /api/s2p/evidence/compliance
['GET'] /api/s2p/evidence/receipts
['GET'] /api/s2p/evidence/receipts/{invoice_id}
['GET'] /api/s2p/evidence/rules
['GET'] /api/s2p/evidence/template
['GET'] /api/s2p/evolution/promoted
['GET'] /api/s2p/evolution/promotion-check
['POST'] /api/s2p/evolution/reset
['GET'] /api/s2p/evolution/rules
['GET'] /api/s2p/evolution/shadow-results
['GET'] /api/s2p/evolution/variants
['GET'] /api/s2p/explorer/centroid/{category}/{action}
['GET'] /api/s2p/explorer/contribution
['GET'] /api/s2p/explorer/dk-weights
['GET'] /api/s2p/explorer/drift/{category}
['GET'] /api/s2p/explorer/export/centroids
['GET'] /api/s2p/explorer/export/csv
['GET'] /api/s2p/financial-impact
['GET'] /api/s2p/governance/compliance-gaps
['GET'] /api/s2p/governance/compliance-screening
['GET'] /api/s2p/governance/conservation-proof
['GET'] /api/s2p/governance/rationalization
['GET'] /api/s2p/governance/rationalization/overlap
['GET'] /api/s2p/governance/rationalization/supplier/{supplier_id}
['GET'] /api/s2p/iks
['GET'] /api/s2p/insight/cross-graph
['GET'] /api/s2p/insight/fingerprint
['GET'] /api/s2p/insight/process-signals
['GET'] /api/s2p/insight/similar
['GET'] /api/s2p/learning-gate
['GET'] /api/s2p/novelty/auto-pause
['GET'] /api/s2p/novelty/history
['GET'] /api/s2p/novelty/rate
['GET'] /api/s2p/novelty/status
['POST'] /api/s2p/outcome
['GET'] /api/s2p/performance/summary
['GET'] /api/s2p/performance/trajectory
['GET'] /api/s2p/performance/what-if
['GET'] /api/s2p/preview/compounding
['GET'] /api/s2p/preview/config
['GET'] /api/s2p/preview/conservation
['GET'] /api/s2p/preview/queue
['GET'] /api/s2p/preview/suppliers
['GET'] /api/s2p/pvg/cycle-time
['GET'] /api/s2p/pvg/impact
['GET'] /api/s2p/pvg/leakage
['GET'] /api/s2p/pvg/variants
['POST'] /api/s2p/score
['GET'] /api/s2p/simulation/impact-summary
['GET'] /api/s2p/simulation/scenarios
['GET'] /api/s2p/simulation/scenarios/{scenario_id}
['GET'] /api/s2p/simulation/what-if/{scenario_id}
['GET'] /api/s2p/suppliers
['GET'] /api/s2p/suppliers/
['GET'] /api/s2p/suppliers/clustering
['GET'] /api/s2p/suppliers/clusters
['GET'] /api/s2p/suppliers/correlations
['GET'] /api/s2p/suppliers/declining
['GET'] /api/s2p/suppliers/early-warnings
['GET'] /api/s2p/suppliers/heatmap
['GET'] /api/s2p/suppliers/payment-behavior
['GET'] /api/s2p/suppliers/payment-strategy
['GET'] /api/s2p/suppliers/similarity
['GET'] /api/s2p/suppliers/trend-signals
['GET'] /api/s2p/suppliers/trends
['GET'] /api/s2p/suppliers/{supplier_id}/heatmap
['GET'] /api/s2p/suppliers/{supplier_id}/history
['GET'] /api/s2p/suppliers/{supplier_id}/profile
['GET'] /api/transfer/status
```

## Appendix B: Search Terms Used

Key search patterns:

- Factor computers: `class .*Factor`, `def .*factor`, `compute_factor`, `factor_computer`, `FactorComputer`, `factor_names`, `n_factors`, `factors`.
- Process bottleneck: `bottleneck`, `process_bottleneck`, `process bottleneck`, `process_bottleneck_factor`.
- Triage/score: `triage`, `analyze_alert`, `analyze_invoice`, `score.*event`, `score`.
- RL/reward/exploration: `rl_`, `reward`, `exploration`, `thompson`, `credit_assign`, `credit assignment`, `posterior`, `policy`, `bandit`, `reinforcement`.
- Process context: `process.*context`, `context.*panel`, `pipeline.*context`, `process_context`, `context_panel`, `context`, `enrichment`, `celonis`, `sap`, `process`, `pipeline`, `invoice`, `supplier`.
- Frontend API calls: `/api/` in `*.ts` and `*.tsx` under `copilot-sdk/apps/s2p/frontend/src`.

Caveats:

- Route existence was verified through FastAPI app introspection, not by running tests.
- Safe GET probes were limited to non-mutating endpoints and path-prefix candidates.
- Static/demo/fixture classification is based on source data declarations and local JSON/cache loaders, not production deployment behavior.
