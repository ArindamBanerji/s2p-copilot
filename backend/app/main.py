import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from copilot_sdk.backend import create_conservation_router
from copilot_sdk.backend.transfer_router import create_transfer_router
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore
from copilot_sdk.scoring import CompoundingScorer

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

from app.domains.s2p.evolution import S2PEvolutionService
from app.domains.s2p.reward import S2PRewardFunction
from app.routers.s2p import learn_router, router as s2p_router
from app.routers.s2p_clustering import router as s2p_clustering_router
from app.routers.s2p_control_tower import router as s2p_control_tower_router
from app.routers.s2p_discovery import router as s2p_discovery_router
from app.routers.s2p_early_warning import router as s2p_early_warning_router
from app.routers.s2p_evolution import router as s2p_evolution_router
from app.routers.s2p_explorer import router as s2p_explorer_router
from app.routers.s2p_evidence import router as s2p_evidence_router
from app.routers.s2p_governance import router as s2p_governance_router
from app.routers.s2p_insight import router as s2p_insight_router
from app.routers.s2p_novelty import router as s2p_novelty_router
from app.routers.s2p_payment import router as s2p_payment_router
from app.routers.s2p_performance import router as s2p_performance_router
from app.routers.s2p_preview import router as s2p_preview_router
from app.routers.s2p_pvg import router as s2p_pvg_router
from app.routers.s2p_simulation import router as s2p_simulation_router
from app.routers.s2p_suppliers import router as s2p_suppliers_router


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,"
    "http://localhost:5174,"
    "http://localhost:5175,"
    "http://localhost:5176,"
    "http://localhost:5177,"
    "http://127.0.0.1:5177"
)


def _cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]


def build_s2p_scorer(db_path: str | None = None) -> CompoundingScorer:
    effective = db_path if db_path is not None else ":memory:"
    return CompoundingScorer.from_preset(
        "s2p",
        graph_store=SQLiteGraphStore(effective, domain="s2p", decision_id_prefix="S2P-"),
        reward_function=S2PRewardFunction(),
    )

app = FastAPI(title="S2P Copilot", version="0.1.0")
app.state.scorer = build_s2p_scorer(str(DATA_DIR / "s2p.db"))
app.state.graph_store = app.state.scorer.graph_store
app.state.s2p_reward_function = app.state.scorer._reward_fn
app.state.s2p_evolution = S2PEvolutionService(app.state.scorer)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(learn_router)
app.include_router(
    create_conservation_router(
        "s2p",
        state_provider=lambda: app.state.scorer.graph_store,
    ),
    prefix="/api",
)
app.include_router(create_transfer_router(app.state.scorer))
app.include_router(s2p_router)
app.include_router(s2p_evolution_router)
app.include_router(s2p_explorer_router)
app.include_router(s2p_control_tower_router)
app.include_router(s2p_discovery_router)
app.include_router(s2p_simulation_router)
app.include_router(s2p_insight_router)
app.include_router(s2p_evidence_router)
app.include_router(s2p_governance_router)
app.include_router(s2p_performance_router)
app.include_router(s2p_pvg_router)
app.include_router(s2p_novelty_router)
app.include_router(s2p_clustering_router)
app.include_router(s2p_early_warning_router)
app.include_router(s2p_payment_router)
app.include_router(s2p_suppliers_router)
app.include_router(s2p_preview_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "s2p-copilot", "version": "0.1.0"}
