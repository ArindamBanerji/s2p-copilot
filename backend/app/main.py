import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from copilot_sdk.backend import create_conservation_router
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.scoring import CompoundingScorer
from copilot_sdk.scoring.config import DomainShape
from copilot_sdk.scoring.storage import DecisionStore
from gae import ProfileScorer

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.reward import S2PRewardFunction
from app.routers.framework_router import router as framework_router
from app.routers.s2p import learn_router, router as s2p_router
from app.routers.s2p_control_tower import router as s2p_control_tower_router
from app.routers.s2p_evidence import router as s2p_evidence_router
from app.routers.s2p_insight import router as s2p_insight_router
from app.routers.s2p_performance import router as s2p_performance_router
from app.routers.s2p_preview import router as s2p_preview_router
from app.routers.s2p_pvg import router as s2p_pvg_router
from app.routers.s2p_suppliers import router as s2p_suppliers_router


class _S2PSdkPreset:
    @property
    def name(self) -> str:
        return "s2p"

    @property
    def shape(self) -> DomainShape:
        return DomainShape(
            n_categories=S2PDomainConfig.n_categories,
            n_actions=S2PDomainConfig.n_actions,
            n_factors=S2PDomainConfig.n_factors,
            category_names=tuple(S2PDomainConfig.categories),
            action_names=tuple(S2PDomainConfig.actions),
            factor_names=tuple(S2PDomainConfig.factors),
        )

    @property
    def penalty_ratio(self) -> float:
        return float(S2PDomainConfig.penalty_ratio)

    @property
    def eta_confirm(self) -> float:
        return float(S2PDomainConfig.eta_confirm)

    @property
    def eta_override(self) -> float:
        return float(S2PDomainConfig.eta_override)

    @property
    def temperature(self) -> float:
        return float(getattr(S2PDomainConfig, "tau", 0.1))

    @property
    def bootstrap_centroids(self) -> np.ndarray:
        return np.asarray(S2PDomainConfig.get_profile_centroids(), dtype=np.float64)


class _S2PInMemoryGraphStore(InMemoryGraphStore):
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
    preset = _S2PSdkPreset()
    profile_scorer = ProfileScorer(
        mu=preset.bootstrap_centroids.copy(),
        actions=list(S2PDomainConfig.actions),
        categories=list(S2PDomainConfig.categories),
    )
    return CompoundingScorer(
        preset=preset,
        store=DecisionStore(":memory:"),
        scorer=profile_scorer,
        graph_store=_S2PInMemoryGraphStore(),
        reward_function=S2PRewardFunction(),
    )

app = FastAPI(title="S2P Copilot", version="0.1.0")
app.state.scorer = build_s2p_scorer()
app.state.graph_store = app.state.scorer.graph_store
app.state.s2p_reward_function = app.state.scorer._reward_fn
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
app.include_router(framework_router)
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
