import os
import logging
import sys
from typing import cast
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import numpy as np

from copilot_sdk.backend import (
    create_conservation_router,
    create_evolution_router,
    create_measurement_state_router,
)
from copilot_sdk.evolution import ScorerBackedProvider
from copilot_sdk.backend.self_computation_router import mount_self_computation_router
from copilot_sdk.backend.counterfactual_router import create_counterfactual_router
from copilot_sdk.backend.transfer_router import create_transfer_router
from copilot_sdk.config import GraphConfig, require_shared_graph
from copilot_sdk.graph.factory import create_graph_store
from copilot_sdk.scoring import CompoundingScorer
from copilot_sdk.scoring.startup_restore import restore_l5_runtime_state
from copilot_sdk.state import create_invalidation_header_middleware, create_tab_state_router

DATA_DIR = Path(os.environ.get("CI_DATA_DIR", Path(__file__).parent / "data")).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

from app.domains.s2p.evolution import S2PEvolutionService
from app.domains.s2p.reward import S2PRewardFunction
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers.s2p import (
    cached_conservation_state_provider,
    learn_router,
    router as s2p_router,
    set_l5_dk_welford_tracker,
)
from app.services.s2p_evolver import get_evolver, set_conservation_provider, set_graph_store
from app.routers.s2p_audit_export import router as s2p_audit_export_router
from app.framework import audit
from app.routers.framework_router import configure_graph_store, router as framework_router
from app.routers.s2p_auto_approve import router as s2p_auto_approve_router
from app.routers.s2p_clustering import router as s2p_clustering_router
from app.routers.compliance_router import router as s2p_compliance_router
from app.routers.centroid_router import router as s2p_centroid_router
from app.routers.s2p_control_tower import router as s2p_control_tower_router
from app.routers.cohort_status_router import create_cohort_status_router
from app.routers.s2p_discovery import router as s2p_discovery_router
from app.routers.s2p_early_warning import router as s2p_early_warning_router
from app.routers.s2p_evolution import router as s2p_evolution_router
from app.routers.s2p_demo_beats import router as s2p_demo_beats_router
from app.routers.s2p_explorer import router as s2p_explorer_router
from app.routers.factor_proposer_router import router as s2p_factor_proposer_router, warm_factor_snapshots
from app.routers.s2p_evidence import router as s2p_evidence_router
from app.routers.s2p_enrichment import router as s2p_enrichment_router
from app.routers.s2p_enrichment_context import router as s2p_enrichment_context_router
from app.routers.s2p_situation import router as s2p_situation_router
from app.routers.financial_router import router as s2p_financial_router, warm_financial_snapshots
from app.routers.s2p_governance import router as s2p_governance_router
from app.routers.s2p_insight import router as s2p_insight_router
from app.routers.lead_time_router import router as s2p_lead_time_router
from app.routers.optimizer_router import router as s2p_optimizer_router
from app.routers.s2p_novelty import router as s2p_novelty_router
from app.routers.s2p_payment import router as s2p_payment_router
from app.routers.s2p_performance import router as s2p_performance_router
from app.routers.s2p_preview import router as s2p_preview_router
from app.routers.s2p_process_fusion import router as s2p_process_fusion_router
from app.routers.s2p_pvg import router as s2p_pvg_router
from app.routers.s2p_proposals import create_proposal_router
from app.routers.s2p_ledger import create_ledger_router
from app.services.proposal_service import GraphProposalStore, ProposalService
from app.services.compounding_ledger import CompoundingLedger
from app.services.s2p_autonomy import S2PAutonomyManager
from app.routers.s2p_autonomy import create_s2p_autonomy_router
from app.routers.s2p_simulation import router as s2p_simulation_router
from app.routers.s2p_suppliers import router as s2p_suppliers_router
from app.s2p_shadow import create_s2p_shadow_store
from app.s2p_graph_status import (
    create_s2p_active_graph_store,
    initialize_s2p_active_graph_config,
    router as s2p_graph_status_router,
)
from app.s2p_shadow import initialize_s2p_shadow_state
from app.state import S2P_MUTATION_PATHS, create_s2p_tab_state_cache


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,"
    "http://localhost:5174,"
    "http://localhost:5175,"
    "http://localhost:5176,"
    "http://localhost:5177,"
    "http://127.0.0.1:5173,"
    "http://127.0.0.1:5174,"
    "http://127.0.0.1:5175,"
    "http://127.0.0.1:5176,"
    "http://127.0.0.1:5177"
)


def _cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]


def _resolve_profile() -> str:
    """Select an explicit scorer/store profile for runtime versus pytest."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return "test"
    if os.environ.get("CI_ALLOW_SQLITE_FALLBACK") == "1":
        return "development"
    return "production"


def build_s2p_scorer(
    db_path: str | None = None,
    graph_store=None,
    *,
    profile: str | None = None,
) -> CompoundingScorer:
    effective = db_path if db_path is not None else ":memory:"
    if effective != ":memory:":
        effective = str(Path(effective).expanduser().resolve())
    resolved_profile = profile or _resolve_profile()
    if graph_store is not None:
        selected_graph_store = graph_store
        selected_backend = type(graph_store).__name__
    else:
        graph_config = GraphConfig.load("s2p", profile=resolved_profile)
        require_shared_graph(
            backend=graph_config.backend,
            graph=graph_config.graph,
            domain=graph_config.domain,
            profile=resolved_profile,
            test_mode=graph_config.active_test_mode,
        )
        selected_graph_store = create_graph_store(
            backend=graph_config.backend,
            domain=graph_config.domain,
            db_path=str(effective),
            decision_id_prefix="S2P-",
            dsn=graph_config.dsn,
            graph_name=graph_config.graph,
            test_mode=graph_config.active_test_mode,
            shared_graph_authorization=graph_config.authorized,
            profile=resolved_profile,
        )
        selected_backend = graph_config.backend
    logger.info(
        "S2P graph store initialized: backend=%s path=%s store=%s",
        selected_backend,
        effective,
        type(selected_graph_store).__name__,
    )
    scorer = CompoundingScorer.from_preset(
        "s2p",
        graph_store=selected_graph_store,
        reward_function=S2PRewardFunction(),
        profile=resolved_profile,
    )
    _migrate_s2p_scorer_runtime(scorer)
    return scorer


def _migrate_s2p_scorer_runtime(scorer: CompoundingScorer) -> None:
    from app.domains.s2p.config import S2PDomainConfig

    profile_scorer = getattr(scorer, "_scorer", None)
    if profile_scorer is None:
        return
    mu = getattr(profile_scorer, "mu", None)
    if mu is not None and getattr(mu, "shape", ())[-1:] == (S2PDomainConfig.n_factors - 1,):
        pad = np.full((*mu.shape[:-1], 1), 0.5, dtype=float)
        profile_scorer.mu = np.concatenate([mu, pad], axis=-1)
        profile_scorer.n_factors = S2PDomainConfig.n_factors
    dk_weights = getattr(profile_scorer, "_dk_weights", None)
    if dk_weights is not None and getattr(dk_weights, "shape", ())[-1:] == (S2PDomainConfig.n_factors - 1,):
        pad = np.ones((*dk_weights.shape[:-1], 1), dtype=float)
        profile_scorer._dk_weights = np.concatenate([dk_weights, pad], axis=-1)

app = FastAPI(title="S2P Copilot", version="0.1.0")
app.state.s2p_active_graph_config = initialize_s2p_active_graph_config()
app.state.scorer = build_s2p_scorer(
    str(DATA_DIR / "s2p.db"),
    graph_store=create_s2p_active_graph_store(app.state.s2p_active_graph_config),
    profile=_resolve_profile(),
)
app.state.graph_store = app.state.scorer.graph_store
configure_graph_store(app.state.graph_store)
set_graph_store(app.state.graph_store)
set_conservation_provider(ScorerBackedProvider(app.state.scorer, "s2p"))
app.state.evolver = get_evolver()
mount_self_computation_router(
    app,
    app.state.graph_store,
    domain="s2p",
    scorer_provider=lambda: app.state.scorer,
    evolver_provider=get_evolver,
)
app.state.s2p_graph_reader = S2PGraphReader(
    store=app.state.scorer.graph_store,
    domain="s2p",
)
app.state.proposal_store = GraphProposalStore(app.state.graph_store)
app.state.proposal_service = ProposalService(store=app.state.proposal_store)


def _live_iks_observation() -> dict[str, object]:
    trajectory = app.state.scorer.trajectory()
    return {
        "iks_value": float(getattr(trajectory, "current_iks", 0.0)),
        "decisions": int(getattr(trajectory, "decisions_total", 0)),
        "source": "s2p_scorer.trajectory",
    }


app.state.compounding_ledger = CompoundingLedger(
    proposal_store=app.state.proposal_store,
    graph_store=app.state.graph_store,
    iks_provider=_live_iks_observation,
    conservation_provider=lambda: cached_conservation_state_provider(app.state),
)
app.state.s2p_autonomy = S2PAutonomyManager(
    DATA_DIR,
    app.state.scorer,
    app.state.compounding_ledger,
)
# Supplier enrichment uses the same domain-scoped AGE graph as decisions.
# There is no production SQLite side store: AGE capability failures surface
# during startup or through the enrichment request rather than being hidden.
app.state.enrichment_store = app.state.graph_store
logger.info(
    "S2P resolved data path: %s (shared_store=%s)",
    DATA_DIR / "s2p.db",
    type(app.state.graph_store).__name__,
)
l5_startup_status = restore_l5_runtime_state(
    domain="s2p",
    scorer=app.state.scorer,
    learning_store=app.state.graph_store,
)
set_l5_dk_welford_tracker(l5_startup_status.pop("welford_tracker", None))
app.state.l5_startup_status = l5_startup_status
app.state.s2p_reward_function = app.state.scorer._reward_fn
app.state.s2p_evolution = S2PEvolutionService(app.state.scorer)
shadow_store = create_s2p_shadow_store(
    active_graph=app.state.s2p_active_graph_config.graph,
    active_domain=app.state.s2p_active_graph_config.domain,
    profile=_resolve_profile(),
)
app.state.s2p_shadow = initialize_s2p_shadow_state(
    store=shadow_store or app.state.graph_store,
)
app.state.s2p_tab_state_cache = create_s2p_tab_state_cache(app.state)


def get_s2p_graph_reader(request: Request) -> S2PGraphReader:
    reader = getattr(request.app.state, "s2p_graph_reader", None)
    if reader is None:
        raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
    return cast(S2PGraphReader, reader)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Invalidated-Urls"],
)
app.middleware("http")(
    create_invalidation_header_middleware(
        "s2p",
        mutation_paths=S2P_MUTATION_PATHS,
    )
)


app.include_router(learn_router)
app.include_router(framework_router, prefix="/api")
app.include_router(
    create_conservation_router(
        "s2p",
        state_provider=lambda: cached_conservation_state_provider(app.state),
    ),
    prefix="/api",
)
app.include_router(
    create_measurement_state_router(
        "s2p",
        scorer_factory=lambda: app.state.scorer,
    ),
    prefix="/api",
)
app.include_router(
    create_counterfactual_router(
        "s2p",
        prefix="/api/s2p/score",
        scorer_provider=lambda: app.state.scorer,
    )
)
app.include_router(create_transfer_router(app.state.scorer))
app.include_router(s2p_router)
app.include_router(create_proposal_router(app.state.proposal_service))
app.include_router(create_ledger_router(app.state.compounding_ledger))
app.include_router(create_s2p_autonomy_router(app.state.s2p_autonomy))
app.include_router(
    create_evolution_router(
        domain="s2p",
        evolver_factory=get_evolver,
    )
)
app.include_router(s2p_auto_approve_router)
app.include_router(s2p_audit_export_router)
app.include_router(s2p_evolution_router)
app.include_router(s2p_demo_beats_router)
app.include_router(s2p_explorer_router)
app.include_router(s2p_factor_proposer_router)
app.include_router(s2p_centroid_router)
app.include_router(s2p_control_tower_router)
app.include_router(s2p_discovery_router)
app.include_router(s2p_simulation_router)
app.include_router(s2p_insight_router)
app.include_router(s2p_evidence_router)
app.include_router(s2p_situation_router)
app.include_router(s2p_enrichment_router)
app.include_router(s2p_enrichment_context_router)
app.include_router(s2p_governance_router)
app.include_router(s2p_performance_router)
app.include_router(s2p_financial_router)
app.include_router(s2p_lead_time_router)
app.include_router(s2p_pvg_router)
app.include_router(s2p_novelty_router)
app.include_router(s2p_clustering_router)
app.include_router(s2p_compliance_router)
app.include_router(s2p_early_warning_router)
app.include_router(s2p_payment_router)
app.include_router(s2p_optimizer_router)
app.include_router(s2p_suppliers_router)
app.include_router(s2p_preview_router)
app.include_router(s2p_process_fusion_router)
app.include_router(create_cohort_status_router(lambda: app.state.graph_store))
app.include_router(s2p_graph_status_router)
app.include_router(create_tab_state_router(app.state.s2p_tab_state_cache))


@app.on_event("startup")
async def warm_s2p_tab_state_cache() -> None:
    audit.configure_graph_store(app.state.graph_store)
    _warm_s2p_learn_store_connections()
    warm_financial_snapshots(app.state.graph_store)
    warm_factor_snapshots(app.state.scorer)
    await app.state.s2p_tab_state_cache.warm_up()


def _warm_s2p_learn_store_connections() -> None:
    """Open the graph connections used by the first learn request."""
    stores = [app.state.graph_store]
    shadow = getattr(app.state, "s2p_shadow", None)
    shadow_store = getattr(shadow, "store", None)
    if shadow_store is not None:
        stores.append(shadow_store)
    for store in stores:
        get_decision = getattr(store, "get_decision", None)
        if not callable(get_decision):
            continue
        try:
            get_decision("__startup_learn_warmup__", domain="s2p")
        except Exception as exc:
            logger.debug("S2P learn connection warmup skipped: %s", exc)
    try:
        from app.domains.s2p.config import S2PDomainConfig

        get_centroid = getattr(app.state.scorer, "get_centroid", None)
        if callable(get_centroid):
            get_centroid(S2PDomainConfig.categories[0], S2PDomainConfig.actions[0])

        fingerprint = getattr(app.state.scorer, "fingerprint", None)
        if callable(fingerprint):
            fingerprint(persist=False)
    except Exception as exc:
        logger.debug("S2P learn-path warmup skipped: %s", exc)


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "s2p-copilot", "version": "0.1.0"}
