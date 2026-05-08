# Graph Report - s2p-copilot  (2026-05-03)

## Corpus Check
- 57 files · ~83,743 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 645 nodes · 817 edges · 54 communities detected
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 141 edges (avg confidence: 0.68)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]

## God Nodes (most connected - your core abstractions)
1. `SyntheticInvoiceGenerator` - 33 edges
2. `InterventionControls` - 23 edges
3. `reset_scorer()` - 20 edges
4. `Neo4jClient` - 14 edges
5. `S2PEvent` - 13 edges
6. `FrozenROICalculator` - 13 edges
7. `get_scorer()` - 11 edges
8. `CheckpointService` - 11 edges
9. `CompositeDiscriminant` - 11 edges
10. `S2PDomainConfig` - 10 edges

## Surprising Connections (you probably didn't know these)
- `TestNoSOCImports` --uses--> `S2PDomainConfig`  [INFERRED]
  backend\tests\test_domain_isolation.py → backend\app\domains\s2p\config.py
- `TestTensorDimensions` --uses--> `S2PDomainConfig`  [INFERRED]
  backend\tests\test_domain_isolation.py → backend\app\domains\s2p\config.py
- `TestS2PScoring` --uses--> `S2PDomainConfig`  [INFERRED]
  backend\tests\test_domain_isolation.py → backend\app\domains\s2p\config.py
- `TestS2PIndependence` --uses--> `S2PDomainConfig`  [INFERRED]
  backend\tests\test_domain_isolation.py → backend\app\domains\s2p\config.py
- `run_demo()` --calls--> `compute_factor_vector()`  [INFERRED]
  backend\demo\s2p_demo.py → backend\app\domains\s2p\factors.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.03
Nodes (89): BaseModel, CheckpointService, Centroid checkpoint and rollback (TD-033)., CompositeDiscriminant, CompositeDiscriminant — multi-signal auto-approve gate (Phase 5).  Uses 13 featu, Multi-signal auto-approve gate.      Uses scorer output features + graph context, FrozenROICalculator, Compute frozen-mode annual ROI.          Returns dict with:           time_saved (+81 more)

### Community 1 - "Community 1"
Cohesion: 0.05
Nodes (50): S2P Copilot 10-Scenario Demo. Runs end-to-end: score -> outcome -> IKS progressi, run_demo(), get_iks(), GET /api/s2p/iks     Returns current S2P Institutional Knowledge Score., _build_scorer(), get_s2p_iks(), get_scorer(), _interpret_iks() (+42 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (35): OutcomeRequest, OutcomeResponse, S2P Copilot router — domain-specific endpoints. Framework endpoints are in frame, Record analyst outcome and optionally update centroids.     POST /api/s2p/outcom, Score a procurement event and return recommended action.     POST /api/s2p/score, record_outcome(), score_procurement_event(), ScoreRequest (+27 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (34): S2P v2 domain configuration for invoice exception triage.      This is versioned, S2PDomainConfigV2, Synthetic S2P invoice generation for v2 preview and scoring experiments., Generate deterministic S2P v2 invoice fixtures around profile centroids., SyntheticInvoice, SyntheticInvoiceGenerator, SyntheticSupplier, _fixture_path() (+26 more)

### Community 4 - "Community 4"
Cohesion: 0.09
Nodes (10): _queue(), tests/test_s2p_preview.py - S2P v2 preview endpoint tests., test_queue_actions_are_v2(), test_queue_categories_are_v2(), test_queue_custom_limit(), test_queue_default_limit_5(), test_queue_factor_vector_length_7(), test_queue_invoices_have_required_fields() (+2 more)

### Community 5 - "Community 5"
Cohesion: 0.1
Nodes (14): Neo4jClient, Neo4j Aura client for Security Graph Handles all graph queries for the SOC Copi, Neo4j Aura client with connection pooling, Create a Decision node with DecisionContext in Neo4j.         Returns decision_, Create an EvolutionEvent and link it to the triggering Decision.         This c, Initialize connection pool, Get recent evolution events for display, Get total learned pattern count (+6 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (16): tests/test_domain_isolation.py — S2P domain isolation tests.  Enforces the multi, S2P defines 4 actions: approve, escalate, reject, review.         (CLAUDE.md spe, S2P penalty_ratio=5.0; SOC uses 20.0 — must never drift to SOC value., S2P categories are procurement domain; SOC categories must be absent., S2P scorer must be dimensionally and semantically independent., A scorer built from S2P DomainConfig has a different mu shape than         a sco, S2P config uses procurement domain verbs and factor names exclusively.         N, S2P modules must have zero SOC domain imports. (+8 more)

### Community 7 - "Community 7"
Cohesion: 0.11
Nodes (17): get_learning_gate(), GET /api/s2p/learning-gate     Returns S2P Learning Activation Gate status., evaluate_s2p_learning_gate(), S2P Learning Activation Gate (Block 3.1)  Controls when S2P centroid updates act, Evaluate whether S2P learning should be active.      Args:         verified_deci, S2PLearningGateResult, tests/test_s2p_learning_gate.py — S2P Learning Activation Gate tests.  Run from, Cold start (0 decisions) must be AMBER, not GREEN. (+9 more)

### Community 8 - "Community 8"
Cohesion: 0.22
Nodes (18): _clamp_limit(), _get_action_list(), _get_canonical_factor_list(), _get_category_list(), _get_config_list(), _get_factor_list(), _get_gae_version(), _get_scored_invoices() (+10 more)

### Community 9 - "Community 9"
Cohesion: 0.13
Nodes (17): _entry_to_dict(), get_decisions(), SOC Audit Service — thin adapter over ci_platform Evidence Ledger.  Hash-chain i, Append a sealed LedgerEntry to the ci_platform ledger and return it as a SOC dic, Find the most-recent LedgerEntry for alert_id and update its outcome.      Mutat, Return all decision records, most recent first, excluding RESET sentinels., Back-fill the ledger from existing session state — specifically     FEEDBACK_GIV, Clear all decision records (demo reset). (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (1): tests/test_s2p_domain_config.py - Versioned S2PDomainConfig tests.

### Community 11 - "Community 11"
Cohesion: 0.17
Nodes (13): get_s2p_decision(), S2P graph write-back operations. Writes S2PDecision nodes to Neo4j. Analogous to, Write a scored S2P decision to Neo4j.     Returns decision_id., Write analyst outcome to existing S2PDecision node.     Returns True if decision, Retrieve a decision by ID. Returns None if not found., write_s2p_decision(), write_s2p_outcome(), _make_driver() (+5 more)

### Community 12 - "Community 12"
Cohesion: 0.15
Nodes (9): DecisionResult, SOC Copilot Agent - Simple Rule-Based Decision Engine ~150 lines total. The demo, Agent decision output, Calculate faithfulness score: Does reasoning match decision and context?, Evaluate 4 deterministic eval gates.         All are deterministic checks - no L, Simple rule-based SOC decision engine.     No LLM orchestration - just determini, Determine if this decision should trigger an evolution event.          Returns:, Main decision function. Rule-based logic.          Args:             alert_type: (+1 more)

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (11): DecisionMade, EventBus, GraphMutated, OutcomeVerified, Lightweight event bus for SOC Copilot (v4.1 — replaced by ci-platform at v4.5)., Emitted after a Decision node is written to the graph.     Channel A: Decision n, Emitted after a Decision node is marked correct/incorrect.     Channel B: Outcom, Emitted for every graph write (decision or outcome).     Provides a single audit (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.18
Nodes (7): build_provenance(), DecisionProvenance, FactorProvenance, get_provenance_from_graph(), ProvenanceService, ProvenanceService — factor provenance and decision audit trail (Phase 6).  Provi, Builds factor provenance records for a decision.

### Community 15 - "Community 15"
Cohesion: 0.17
Nodes (10): create_narrative_provider(), get_narrative_provider(), NarrativeProvider, NarrativeProvider ABC for CopilotFramework. Domain implementations (e.g. Templat, Set the module-level singleton. Called once at app startup., Register a NarrativeProvider class under a name.      Called by the domain servi, Create a NarrativeProvider instance by name.      Args:         provider_type: n, register_narrative_provider() (+2 more)

### Community 16 - "Community 16"
Cohesion: 0.21
Nodes (8): cosine_similarity(), get_theta(), SimilarCaseFinder ABC for CopilotFramework. Domain implementations supply SOC/S2, Return up to k similar past Decision nodes for *category*.          Category fil, Return fraction of *similar_cases* whose action matches *current_action*., Case-based reasoning retrieval — domain subclass supplies get_theta()., Fetch up to *limit* verified Decision nodes for *category* from Neo4j,         m, SimilarCasesBase

### Community 17 - "Community 17"
Cohesion: 0.2
Nodes (9): get_all_trust_scores(), get_reward_summary(), get_trust_status(), Feedback trust/reward mechanics for CopilotFramework. Domain-agnostic — no SOC r, Return all current trust scores and the full update history.      Returns     --, Aggregate current in-memory feedback state into an RL reward summary.      Rewar, Update trust score for a situation type after a decision outcome.      Asymmetri, Get trust status for a single situation type.      Returns     -------     { (+1 more)

### Community 18 - "Community 18"
Cohesion: 0.22
Nodes (9): compute_iks(), interpret(), interpret_iks_v2(), _mean_centroid_drift(), IKS (Institutional Knowledge Score) algorithm for CopilotFramework. compute_iks(, Return a human-readable interpretation of the IKS (v1) score., Return a human-readable interpretation of the IKS v2 composite score., Compute mean ‖μ(t)[c,a,:] − μ₀[c,a,:]‖₂ over all (c, a) pairs.      Parameters (+1 more)

### Community 19 - "Community 19"
Cohesion: 0.2
Nodes (9): load_from_file(), make_state(), LearningState singleton for CopilotFramework. Domain layer (SOC/S2P) builds the, Read the metadata field from the checkpoint. Returns {} if absent., Atomically persist W matrix + WeightUpdate history to a JSON checkpoint.      Us, Create a fresh LearningState from raw parameters., Deserialize W matrix and WeightUpdate history from a JSON checkpoint.      Param, read_checkpoint_metadata() (+1 more)

### Community 20 - "Community 20"
Cohesion: 0.22
Nodes (1): S2P Domain Configuration. Procurement copilot — Source-to-Pay domain. C=6 catego

### Community 21 - "Community 21"
Cohesion: 0.25
Nodes (7): tests/test_framework_discipline.py — CopilotFramework extraction discipline.  En, Framework files must never import from app.domains or app.routers.     This test, All framework modules import without error., Importing from app.services still works after stub replacement.     Same object, test_framework_has_no_domain_imports(), test_framework_modules_importable(), test_reexport_stubs_transparent()

### Community 22 - "Community 22"
Cohesion: 0.25
Nodes (7): tests/test_scaffold.py — S2P Copilot Step 0 smoke tests.  Verifies: health endpo, GET /health returns 200 with service='s2p-copilot'., Core framework modules import without error., GAE 0.7.20+ is importable with required symbols., test_framework_discipline_enforced(), test_gae_importable(), test_health_endpoint_returns_ok()

### Community 23 - "Community 23"
Cohesion: 0.29
Nodes (1): tests/test_s2p_config.py — S2PDomainConfig unit tests.  Run from backend/:     p

### Community 24 - "Community 24"
Cohesion: 0.29
Nodes (1): tests/test_s2p_outcome.py — POST /api/s2p/outcome endpoint tests.  Run from back

### Community 25 - "Community 25"
Cohesion: 0.29
Nodes (1): tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.  Run from

### Community 26 - "Community 26"
Cohesion: 0.33
Nodes (5): decisions_to_days(), predict_n_half(), Domain-agnostic convergence math for CopilotFramework.  CLAIM-CONV-01 (V-MV-CONV, Predict N_half (decisions to 50% convergence) from deployment params.     CLAIM-, Convert decision count to calendar days.     V IS used here — volume determines

### Community 27 - "Community 27"
Cohesion: 0.33
Nodes (5): get_ols_status(), ols_status.py — OLS (Override Lift Score) Dashboard service (L-09).  Uses GAE 0., Compute OLS dashboard status for the frontend.      Parameters     ----------, get_ols_status_endpoint(), Return OLS (Override Lift Score) dashboard status.      Uses GAE 0.7.18 OLSMonit

### Community 28 - "Community 28"
Cohesion: 0.4
Nodes (1): CheckpointService — centroid checkpoint and rollback (TD-033, Phase 4 §17.5).  C

### Community 29 - "Community 29"
Cohesion: 0.4
Nodes (3): DecisionHistoryService, DecisionHistoryService — per-category decision counts and rolling accuracy.  Pro, Tracks per-category decision counts and rolling accuracy.

### Community 30 - "Community 30"
Cohesion: 0.4
Nodes (1): ShadowModeService — Phase 4 shadow mode (§21).  Shadow mode: system makes decisi

### Community 31 - "Community 31"
Cohesion: 0.67
Nodes (3): Map 8-factor scenario to actual POST /api/s2p/score request.      Mapping ration, run_scenarios(), _to_api_payload()

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Domain-agnostic feedback state store for CopilotFramework.  FEEDBACK_GIVEN is ex

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): CopilotFramework — domain-agnostic copilot infrastructure.  This package is desi

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Re-export stub — implementation lives in app.framework.ols_status. Preserved so

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Context manager for Neo4j sessions

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Bootstrap centroids — uniform 0.5 prior.         Real values from P28 Phase 1 af

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): Bootstrap sigma profile — uniform 0.15 prior.         Real values from P28 Phase

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): Return ndarray profile centroids with shape (5, 5, 7).

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Return nested dict {category: {action: [factor_values]}}.          Kept for comp

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Bootstrap sigma profile for the v2 scoring factors.

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Build a GAE CalibrationProfile using the actual constructor fields.          eta

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Snapshot current centroids to a Checkpoint node in Neo4j.          Parameters

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Return all Checkpoint nodes ordered by timestamp DESC.

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Restore centroids from a Checkpoint node and freeze the scorer.          Paramet

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Evaluate whether a decision should be auto-approved.          Parameters

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Get decision count and rolling accuracy for a category.          Uses the last 1

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Build provenance for a decision.          Parameters         ----------

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Retrieve a stored decision's factor vector from Neo4j and rebuild provenance.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Mark a Decision node as shadow_mode=True.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Record what the analyst actually did (the ground truth).         Also sets d.agr

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Generate shadow mode report: agreement rates by category.          Returns

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Return cosine similarity in [0, 1].  Returns 0.0 for zero vectors.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Return per-category cosine similarity threshold for retrieval.

## Knowledge Gaps
- **225 isolated node(s):** `Neo4j Aura client for Security Graph Handles all graph queries for the SOC Copi`, `Neo4j Aura client with connection pooling`, `Initialize connection pool`, `Close connection pool`, `Context manager for Neo4j sessions` (+220 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 10`** (17 nodes): `test_s2p_domain_config.py`, `tests/test_s2p_domain_config.py - Versioned S2PDomainConfig tests.`, `test_legacy_config_unchanged()`, `test_v2_actions_count_5()`, `test_v2_auto_approve_high_match_status()`, `test_v2_calibration_profile_valid()`, `test_v2_canonical_factors_count_4()`, `test_v2_canonical_not_in_scoring()`, `test_v2_categories_count_5()`, `test_v2_centroid_shape_5_5_7()`, `test_v2_centroids_bounded_0_1()`, `test_v2_domain_name_s2p()`, `test_v2_factors_count_7()`, `test_v2_no_soc_actions()`, `test_v2_no_soc_categories()`, `test_v2_no_soc_factors()`, `test_v2_scorer_accepts_shape()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (9 nodes): `config.py`, `get_action_index()`, `get_calibration_profile()`, `get_category_index()`, `get_factor_index()`, `get_initial_centroids()`, `get_profile_centroids()`, `get_sigma_profile()`, `S2P Domain Configuration. Procurement copilot — Source-to-Pay domain. C=6 catego`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (7 nodes): `test_s2p_config.py`, `tests/test_s2p_config.py — S2PDomainConfig unit tests.  Run from backend/:     p`, `test_actions_are_s2p_not_soc()`, `test_factors_are_s2p_not_soc()`, `test_get_initial_centroids_shape()`, `test_penalty_ratio_is_s2p_not_soc()`, `test_tensor_shape_correct()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 24`** (7 nodes): `test_s2p_outcome.py`, `tests/test_s2p_outcome.py — POST /api/s2p/outcome endpoint tests.  Run from back`, `test_invalid_analyst_action_returns_422()`, `test_invalid_outcome_returns_422()`, `test_learning_disabled_by_default()`, `test_outcome_endpoint_confirm_returns_200()`, `test_outcome_endpoint_override_returns_200()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 25`** (7 nodes): `test_s2p_score_endpoint.py`, `tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.  Run from`, `test_score_action_is_valid_s2p_action()`, `test_score_endpoint_returns_200()`, `test_score_factor_vector_length()`, `test_score_invalid_category_returns_422()`, `test_score_response_has_required_fields()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 28`** (5 nodes): `checkpoint.py`, `create_checkpoint()`, `list_checkpoints()`, `CheckpointService — centroid checkpoint and rollback (TD-033, Phase 4 §17.5).  C`, `rollback()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (5 nodes): `shadow_mode.py`, `get_shadow_report()`, `ShadowModeService — Phase 4 shadow mode (§21).  Shadow mode: system makes decisi`, `record_analyst_action()`, `record_shadow_decision()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (2 nodes): `feedback_store.py`, `Domain-agnostic feedback state store for CopilotFramework.  FEEDBACK_GIVEN is ex`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (2 nodes): `__init__.py`, `CopilotFramework — domain-agnostic copilot infrastructure.  This package is desi`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (2 nodes): `ols_status.py`, `Re-export stub — implementation lives in app.framework.ols_status. Preserved so`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): `Context manager for Neo4j sessions`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Bootstrap centroids — uniform 0.5 prior.         Real values from P28 Phase 1 af`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `Bootstrap sigma profile — uniform 0.15 prior.         Real values from P28 Phase`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `Return ndarray profile centroids with shape (5, 5, 7).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Return nested dict {category: {action: [factor_values]}}.          Kept for comp`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Bootstrap sigma profile for the v2 scoring factors.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Build a GAE CalibrationProfile using the actual constructor fields.          eta`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Snapshot current centroids to a Checkpoint node in Neo4j.          Parameters`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Return all Checkpoint nodes ordered by timestamp DESC.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Restore centroids from a Checkpoint node and freeze the scorer.          Paramet`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Evaluate whether a decision should be auto-approved.          Parameters`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Get decision count and rolling accuracy for a category.          Uses the last 1`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Build provenance for a decision.          Parameters         ----------`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Retrieve a stored decision's factor vector from Neo4j and rebuild provenance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Mark a Decision node as shadow_mode=True.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Record what the analyst actually did (the ground truth).         Also sets d.agr`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Generate shadow mode report: agreement rates by category.          Returns`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Return cosine similarity in [0, 1].  Returns 0.0 for zero vectors.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Return per-category cosine similarity threshold for retrieval.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `S2PDomainConfig` connect `Community 2` to `Community 1`, `Community 20`, `Community 6`?**
  _High betweenness centrality (0.155) - this node is a cross-community bridge._
- **Why does `S2PDomainConfigV2` connect `Community 3` to `Community 20`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `SyntheticInvoiceGenerator` connect `Community 3` to `Community 8`?**
  _High betweenness centrality (0.093) - this node is a cross-community bridge._
- **Are the 22 inferred relationships involving `SyntheticInvoiceGenerator` (e.g. with `S2PDomainConfigV2` and `_get_scored_invoices()`) actually correct?**
  _`SyntheticInvoiceGenerator` has 22 INFERRED edges - model-reasoned connections that need verification._
- **Are the 10 inferred relationships involving `InterventionControls` (e.g. with `CheckpointService` and `ShadowToggleRequest`) actually correct?**
  _`InterventionControls` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `reset_scorer()` (e.g. with `.setup_method()` and `.setup_method()`) actually correct?**
  _`reset_scorer()` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `S2PEvent` (e.g. with `ScoreRequest` and `ScoreResponse`) actually correct?**
  _`S2PEvent` has 11 INFERRED edges - model-reasoned connections that need verification._