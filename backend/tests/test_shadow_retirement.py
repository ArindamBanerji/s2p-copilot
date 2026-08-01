"""Regression coverage for S2P's single-store shadow lifecycle."""

from app.main import build_s2p_scorer
from app.s2p_shadow import (
    S2PShadowConfig,
    S2PShadowState,
    initialize_s2p_shadow_state,
)
from app.services.s2p_enrichment import S2PSupplierEnrichmentService
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore
from copilot_sdk.scoring.scorer import CompoundingScorer


def _shared_runtime() -> tuple[InMemoryGraphStore, CompoundingScorer, S2PShadowState]:
    store = InMemoryGraphStore(domain="s2p", decision_id_prefix="S2P-")
    scorer = build_s2p_scorer(graph_store=store, profile="test")
    shadow = initialize_s2p_shadow_state(
        env={"S2P_SHADOW_AGE": "1", "S2P_AGE_GRAPH": "soc_graph"},
        store=store,
    )
    return store, scorer, shadow


def test_production_startup_uses_one_store() -> None:
    store, scorer, shadow = _shared_runtime()

    assert scorer.graph_store is store
    assert shadow.store is store


def test_production_startup_no_shadow_graph() -> None:
    store, _, shadow = _shared_runtime()

    assert shadow.store is store
    assert shadow.config.graph == "soc_graph"
    assert not hasattr(shadow, "shadow_store")


def test_shared_graph_accepts_soc_graph() -> None:
    config = S2PShadowConfig.from_env(
        {"S2P_SHADOW_AGE": "1", "S2P_AGE_GRAPH": "soc_graph"}
    )

    assert config.enabled is True
    assert config.graph == "soc_graph"


def test_enrichment_sees_scorer_decisions() -> None:
    store, _, _ = _shared_runtime()
    decision_id = store.write_decision(
        domain="s2p",
        category="invoice",
        action="approve",
        confidence=0.9,
        factors={"amount": 0.2},
        metadata={"decision_id": "S2P-SHARED-1"},
    )
    service = S2PSupplierEnrichmentService(graph_store=store)

    assert service.graph_store.get_decision(decision_id, domain="s2p") is not None


def test_enrichment_store_identity() -> None:
    store, scorer, _ = _shared_runtime()
    enrichment = S2PSupplierEnrichmentService(graph_store=store)

    assert enrichment.graph_store is scorer.graph_store


def test_s2p_test_profile_sqlite_still_works(tmp_path) -> None:
    store = SQLiteGraphStore(tmp_path / "s2p.db", domain="s2p", decision_id_prefix="S2P-")
    scorer = build_s2p_scorer(graph_store=store, profile="test")

    assert scorer.graph_store is store
    store.close()


def test_shadow_records_have_lifecycle_label_and_distinct_id() -> None:
    from app.routers.s2p import _shadow_decision_id

    store, _, _ = _shared_runtime()
    production_id = "S2P-PRODUCTION-1"
    shadow_id = _shadow_decision_id(production_id)
    store.write_governed_decision(
        decision_id=shadow_id,
        domain="s2p",
        category="invoice",
        category_index=0,
        recommended_action="approve",
        recommended_index=0,
        confidence=0.9,
        probabilities=[0.9, 0.1],
        factor_vector=[0.2],
        factor_names=["amount"],
        metadata={
            "lifecycle": "shadow",
            "production_decision_id": production_id,
        },
    )
    record = store.get_decision(shadow_id, domain="s2p")

    assert record is not None
    assert record["decision_id"] != production_id
    assert record["metadata"]["lifecycle"] == "shadow"
    assert record["metadata"]["production_decision_id"] == production_id
