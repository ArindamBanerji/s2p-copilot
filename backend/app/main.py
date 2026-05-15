import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from copilot_sdk.backend import create_conservation_router
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.scoring import CompoundingScorer

from app.domains.s2p.reward import S2PRewardFunction
from app.routers.s2p import learn_router, router as s2p_router
from app.routers.s2p_control_tower import router as s2p_control_tower_router
from app.routers.s2p_evidence import router as s2p_evidence_router
from app.routers.s2p_insight import router as s2p_insight_router
from app.routers.s2p_performance import router as s2p_performance_router
from app.routers.s2p_preview import router as s2p_preview_router
from app.routers.s2p_pvg import router as s2p_pvg_router
from app.routers.s2p_suppliers import router as s2p_suppliers_router


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,"
    "http://localhost:5174,"
    "http://localhost:5175,"
    "http://localhost:5176,"
    "http://localhost:5177"
)


def _cors_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]


class _S2PGraphStore(InMemoryGraphStore):
    """Preserve the public S2P decision-id prefix on top of SDK GraphStore behavior."""

    def write_decision(self, entity_id, category, action, confidence, factors, metadata=None):
        meta = dict(metadata or {})
        decision_id = str(meta.get("decision_id") or entity_id)
        if not decision_id.startswith("S2P-"):
            decision_id = f"S2P-{decision_id}"
        meta["decision_id"] = decision_id
        return super().write_decision(
            entity_id=entity_id,
            category=category,
            action=action,
            confidence=confidence,
            factors=factors,
            metadata=meta,
        )


def build_s2p_scorer() -> CompoundingScorer:
    return CompoundingScorer.from_preset(
        "s2p",
        db_path=":memory:",
        graph_store=_S2PGraphStore(),
        reward_function=S2PRewardFunction(),
    )

app = FastAPI(title="S2P Copilot", version="0.1.0")
app.state.scorer = build_s2p_scorer()
app.state.graph_store = app.state.scorer.graph_store
app.state.s2p_reward_function = app.state.scorer._reward_fn
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
app.include_router(s2p_router)
app.include_router(s2p_control_tower_router)
app.include_router(s2p_insight_router)
app.include_router(s2p_evidence_router)
app.include_router(s2p_performance_router)
app.include_router(s2p_pvg_router)
app.include_router(s2p_suppliers_router)
app.include_router(s2p_preview_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "s2p-copilot", "version": "0.1.0"}
