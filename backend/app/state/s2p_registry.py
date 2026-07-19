"""S2P tab-state cache registry."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any, Callable

from copilot_sdk.state import TabStateCache, register_tab_state_cache

from app.models.responses import CollectionResponse, GenericResponse, LearningGateResponse
from app.routers import s2p as s2p_router
from app.routers import s2p_auto_approve
from app.routers import s2p_control_tower
from app.routers import s2p_discovery
from app.routers import s2p_evidence
from app.routers import s2p_novelty
from app.routers import s2p_preview
from app.services.cohort_status import CohortStatusService
from app.state.schemas.s2p import S2PCollectionResponse, S2PLearningGateResponse, S2PObjectResponse


S2P_MUTATION_PATHS: dict[tuple[str, str], str] = {
    ("POST", "/api/s2p/score"): "score",
    ("POST", "/api/learn"): "learn",
    ("POST", "/api/s2p/outcome"): "learn",
    ("POST", "/api/s2p/auto-approve/enable"): "reset",
    ("POST", "/api/s2p/auto-approve/disable"): "reset",
    ("POST", "/api/s2p/auto-approve/evaluate"): "score",
    ("POST", "/api/s2p/evolution/propose"): "evolution",
    ("POST", "/api/s2p/evolution/reset"): "reset",
}


def create_s2p_tab_state_cache(app_state: Any) -> TabStateCache:
    cache = TabStateCache("s2p")
    request = _request_for(app_state)
    graph_store = getattr(app_state, "graph_store", None)

    _register(
        cache,
        "iks",
        "/api/s2p/iks",
        lambda: _call(s2p_router.get_iks, request),
        s2p_router.get_iks,
        tier="CRITICAL",
        reads_scorer=False,
    )
    _register(
        cache,
        "cohort-status",
        "/api/s2p/cohort-status",
        lambda: CohortStatusService(graph_store=graph_store).get_status(),
        CohortStatusService.get_status,
        tier="STANDARD",
        invalidated_by=("score", "learn", "reset"),
    )
    _register(
        cache,
        "auto-approve-stats",
        "/api/s2p/auto-approve/stats",
        s2p_router.get_auto_approve_stats_endpoint,
        s2p_router.get_auto_approve_stats_endpoint,
        tier="STANDARD",
        invalidated_by=("score", "learn", "reset"),
    )
    _register(
        cache,
        "auto-approve-status",
        "/api/s2p/auto-approve/status",
        lambda: _call(s2p_auto_approve.auto_approve_status, request),
        s2p_auto_approve.auto_approve_status,
        tier="STANDARD",
        invalidated_by=("score", "learn", "reset"),
    )
    _register(
        cache,
        "preview-queue",
        "/api/s2p/preview/queue",
        lambda: _call(s2p_preview.preview_queue, request),
        s2p_preview.preview_queue,
        tier="STANDARD",
        invalidated_by=("reset",),
        reads_scorer=False,
    )
    _register(
        cache,
        "auto-approve-audit",
        "/api/s2p/auto-approve/audit",
        s2p_auto_approve.auto_approve_audit,
        s2p_auto_approve.auto_approve_audit,
        tier="STANDARD",
        invalidated_by=("score", "reset"),
    )
    _register(
        cache,
        "novelty-status",
        "/api/s2p/novelty/status",
        s2p_novelty.novelty_status,
        s2p_novelty.novelty_status,
        tier="STANDARD",
        invalidated_by=("score", "reset"),
    )
    _register(
        cache,
        "novelty-rate",
        "/api/s2p/novelty/rate",
        s2p_novelty.novelty_rate,
        s2p_novelty.novelty_rate,
        tier="STANDARD",
        invalidated_by=("score", "reset"),
    )
    _register(
        cache,
        "evidence-chain-integrity",
        "/api/s2p/evidence/chain-integrity",
        s2p_evidence.chain_integrity,
        s2p_evidence.chain_integrity,
        tier="STANDARD",
        invalidated_by=("learn", "reset"),
    )
    _register_cold(
        cache,
        "control-tower-intents",
        "/api/s2p/control-tower/intents",
        s2p_control_tower.intents,
        s2p_control_tower.intents,
        schema=S2PCollectionResponse,
    )
    _register_cold(
        cache,
        "preview-compounding",
        "/api/s2p/preview/compounding",
        s2p_preview.preview_compounding,
        s2p_preview.preview_compounding,
    )
    _register_cold(
        cache,
        "preview-config",
        "/api/s2p/preview/config",
        s2p_preview.preview_config,
        s2p_preview.preview_config,
    )
    _register_cold(
        cache,
        "discovery-alerts",
        "/api/s2p/discovery/alerts",
        s2p_discovery.discovery_alerts,
        s2p_discovery.discovery_alerts,
    )
    _register_cold(
        cache,
        "discovery-disruptions",
        "/api/s2p/discovery/disruptions",
        s2p_discovery.disruption_recovery,
        s2p_discovery.disruption_recovery,
    )
    _register_cold(
        cache,
        "discovery-extended",
        "/api/s2p/discovery/extended",
        s2p_discovery.extended_discoveries,
        s2p_discovery.extended_discoveries,
    )
    _register_cold(
        cache,
        "evidence-rules",
        "/api/s2p/evidence/rules",
        s2p_evidence.rules,
        s2p_evidence.rules,
    )
    _register_cold(
        cache,
        "evidence-compliance",
        "/api/s2p/evidence/compliance",
        s2p_evidence.compliance,
        s2p_evidence.compliance,
    )
    _register_cold(
        cache,
        "learning-gate",
        "/api/s2p/learning-gate",
        s2p_router.get_learning_gate,
        s2p_router.get_learning_gate,
        schema=S2PLearningGateResponse,
    )

    return register_tab_state_cache(cache)


def _register(
    cache: TabStateCache,
    key: str,
    url: str,
    compute_fn: Callable[[], Any],
    service_fn: Callable[..., Any],
    *,
    tier: str,
    invalidated_by: tuple[str, ...] = ("score", "learn", "reset"),
    reads_scorer: bool = False,
    schema: type[S2PObjectResponse] = S2PObjectResponse,
) -> None:
    cache.register(
        key,
        inspect.unwrap(compute_fn),
        invalidated_by=invalidated_by,
        critical=tier == "CRITICAL",
        category="STATIC",
        schema=schema,
        service_fn=inspect.unwrap(service_fn),
        url=url,
        tier=tier,
        reads_scorer=reads_scorer,
    )


def _register_cold(
    cache: TabStateCache,
    key: str,
    url: str,
    compute_fn: Callable[[], Any],
    service_fn: Callable[..., Any],
    *,
    schema: type[S2PObjectResponse] = S2PObjectResponse,
) -> None:
    _register(
        cache,
        key,
        url,
        compute_fn,
        service_fn,
        tier="COLD",
        invalidated_by=("reset",),
        schema=schema,
    )


def _request_for(app_state: Any) -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=app_state))


def _call(handler: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return inspect.unwrap(handler)(*args, **kwargs)
