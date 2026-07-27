import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from copilot_sdk.graph.enrichment import ProvenancedValue

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.routers import s2p_suppliers
from app.services.supplier_intelligence import (
    DISCREPANCY_ACTIONS,
    SupplierIntelligenceComposer,
)
from app.services.supplier_profile_accumulator import accumulator


class FakeGraphStore:
    def __init__(self, enrichment=None, decisions=None):
        self.enrichment = enrichment or {}
        self.decisions = decisions or []
        self.write_called = False

    def read_entity_enrichment(self, **kwargs):
        return self.enrichment.get(kwargs["entity_id"], {})

    def get_verified_decisions(self, domain: str | None = None):
        if domain is not None:
            assert domain == "s2p"
        return list(self.decisions)

    def write_entity_enrichment(self, **kwargs):
        self.write_called = True
        raise AssertionError("R18A must not write enrichment")


@pytest.fixture(autouse=True)
def reset_supplier_accumulator():
    accumulator.reset()
    previous_graph_store = getattr(app.state, "graph_store", None)
    yield
    accumulator.reset()
    if previous_graph_store is None:
        try:
            delattr(app.state, "graph_store")
        except AttributeError:
            pass
    else:
        app.state.graph_store = previous_graph_store


def _verified(value, count=50, label="verified metric"):
    return ProvenancedValue.from_verified(value, source_count=count, label=label)


def _fixture(value, label="fixture/context only"):
    return ProvenancedValue.from_fixture(value, label=label)


def _enrichment(
    *,
    exception_rate=0.04,
    accuracy=0.94,
    count=60,
    include_context=True,
):
    metrics = {
        "verified_decisions": _verified(count, count=count),
        "exception_rate": _verified(exception_rate, count=count),
        "accuracy": _verified(accuracy, count=count),
    }
    if include_context:
        metrics["total_decisions"] = ProvenancedValue(
            value=count + 5,
            source="graph_store",
            provenance_tier="context",
            source_count=count + 5,
            measured=True,
            verified=False,
            provenance_label="graph read count, not verified outcome count",
        )
        metrics["otif_score"] = _fixture(0.92, "fixture OTIF context")
        metrics["avg_lead_time_days"] = _fixture(21.0, "fixture lead-time context")
    return metrics


def _request(graph_store=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph_store=graph_store)))


def test_intelligence_depth_thresholds_per_metric():
    composer = SupplierIntelligenceComposer()
    depth = composer.intelligence_depth(
        {
            "emerging": _verified(1, count=1),
            "developing": _verified(1, count=20),
            "reliable": _verified(1, count=50),
            "deep": _verified(1, count=100),
            "context_total_decisions": ProvenancedValue(
                value=500,
                source="graph_store",
                provenance_tier="context",
                source_count=500,
                measured=True,
                verified=False,
                provenance_label="all-row graph count, not verified depth",
            ),
        }
    )

    assert "context_total_decisions" not in depth["per_metric"]
    assert depth["per_metric"]["emerging"]["tier"] == "emerging"
    assert depth["per_metric"]["developing"]["tier"] == "developing"
    assert depth["per_metric"]["reliable"]["tier"] == "reliable"
    assert depth["per_metric"]["deep"]["tier"] == "deep"
    assert depth["metrics_past_threshold"] == 2


def test_headline_tier_reflects_breadth_not_peak():
    composer = SupplierIntelligenceComposer()
    depth = composer.intelligence_depth(
        {
            "one_deep": _verified(1, count=120),
            "a": _verified(1, count=5),
            "b": _verified(1, count=8),
            "c": _verified(1, count=9),
        }
    )

    assert depth["headline_tier"] == "developing"
    assert depth["label"] == "1 of 4 metrics past threshold"


def test_depth_excludes_context_unverified_source_counts():
    composer = SupplierIntelligenceComposer()
    depth = composer.intelligence_depth(
        {
            "accuracy": _verified(0.91, count=5),
            "total_decisions": ProvenancedValue(
                value=250,
                source="graph_store",
                provenance_tier="context",
                source_count=250,
                measured=True,
                verified=False,
                provenance_label="GraphStore decision history · total rows",
            ),
            "category_distribution": ProvenancedValue(
                value={"price_variance": 250},
                source="graph_store",
                provenance_tier="context",
                source_count=250,
                measured=True,
                verified=False,
                provenance_label="includes unverified rows",
            ),
        }
    )

    assert depth["metrics_total"] == 1
    assert depth["metrics_past_threshold"] == 0
    assert depth["headline_tier"] == "emerging"
    assert set(depth["per_metric"]) == {"accuracy"}


def test_comprehensive_vs_deep_distinction():
    composer = SupplierIntelligenceComposer()
    comprehensive = composer.intelligence_depth(
        {"a": _verified(1, count=50), "b": _verified(1, count=99)}
    )
    deep = composer.intelligence_depth(
        {"a": _verified(1, count=100), "b": _verified(1, count=75)}
    )

    assert comprehensive["headline_tier"] == "comprehensive"
    assert deep["headline_tier"] == "deep"


def test_none_emerging_reliable_depth_cases():
    composer = SupplierIntelligenceComposer()

    assert composer.intelligence_depth({})["headline_tier"] == "none"
    assert composer.intelligence_depth({"a": _verified(1, count=1)})["headline_tier"] == "emerging"
    assert composer.intelligence_depth({"a": _verified(1, count=50), "b": _verified(1, count=1)})["headline_tier"] == "reliable"


def test_trajectory_projection():
    composer = SupplierIntelligenceComposer()
    projection = composer.trajectory_projection(10, 5, {"reliable": 50, "deep": 100})

    assert projection["thresholds"]["reliable"]["remaining_decisions"] == 40
    assert projection["thresholds"]["reliable"]["estimated_weeks"] == 8.0


def test_risk_tier_requires_verified_metrics_for_high_medium_low():
    composer = SupplierIntelligenceComposer()
    high = composer.risk_tier(_enrichment(exception_rate=0.18, accuracy=0.82, count=40))
    low = composer.risk_tier(_enrichment(exception_rate=0.02, accuracy=0.95, count=40))
    medium = composer.risk_tier(_enrichment(exception_rate=0.08, accuracy=0.86, count=40))

    assert high["tier"] == "high"
    assert low["tier"] == "low"
    assert medium["tier"] == "medium"
    assert {high["basis"], low["basis"], medium["basis"]} == {"learned"}


def test_context_only_signals_do_not_produce_high_medium_low():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier({"otif_score": _fixture(0.70), "avg_lead_time_days": _fixture(30)})

    assert risk["tier"] in {"monitor", "integration_pending"}
    assert risk["basis"] == "context"
    assert "context_only_risk_cannot_be_high_medium_or_low" in risk["warnings"]


def test_context_only_risk_guard_with_fixture_warning():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier(
        {
            "otif_score": _fixture(0.65, "fixture OTIF context"),
            "avg_lead_time_days": _fixture(30, "fixture lead-time context"),
        }
    )

    assert risk["tier"] not in {"high", "medium", "low"}
    assert risk["basis"] == "context"


def test_insufficient_data_threshold():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier(_enrichment(exception_rate=0.22, accuracy=0.60, count=5))

    assert risk["tier"] == "insufficient_data"
    assert risk["basis"] == "insufficient_data"
    assert risk["source_count"] == 5


def test_risk_does_not_default_to_medium_from_non_risk_learned_metadata():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier(
        {
            "verified_decisions": _verified(100, count=100),
            "decision_count_by_quarter": _verified({"2026-Q1": 100}, count=100),
        }
    )

    assert risk["tier"] == "insufficient_data"
    assert risk["basis"] == "insufficient_data"
    assert "learned_risk_bearing_metric_unavailable" in risk["warnings"]


def test_learned_stable_trend_alone_does_not_create_learned_risk():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier({"trend": _verified("stable", count=100)})

    assert risk["tier"] not in {"high", "medium", "low"}
    assert risk["basis"] == "insufficient_data"
    assert "learned_risk_bearing_metric_unavailable" in risk["warnings"]


def test_learned_improving_trend_alone_does_not_create_learned_risk():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier({"trend": _verified("improving", count=100)})

    assert risk["tier"] not in {"high", "medium", "low"}
    assert risk["basis"] == "insufficient_data"
    assert "learned_risk_bearing_metric_unavailable" in risk["warnings"]


def test_learned_negative_trend_can_be_risk_bearing():
    composer = SupplierIntelligenceComposer()
    deteriorating = composer.risk_tier({"trend": _verified("deteriorating", count=40)})
    declining = composer.risk_tier({"trend": _verified("declining", count=40)})

    assert deteriorating["tier"] == "high"
    assert deteriorating["basis"] == "learned"
    assert declining["tier"] == "high"


def test_context_deteriorating_trend_cannot_create_high_medium_low():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier({"trend": _fixture("deteriorating", "fixture trend context")})

    assert risk["tier"] not in {"high", "medium", "low"}
    assert risk["basis"] == "context"


def test_caught_discrepancies_use_actual_action_taxonomy():
    actual_actions = set(S2PDomainConfig.get_actions())

    assert {"flag_leakage", "hold_for_review", "escalate_to_buyer"} <= actual_actions
    assert "escalate_compliance" not in actual_actions
    assert set(DISCREPANCY_ACTIONS) <= actual_actions


def test_caught_discrepancies_count_only_verified_correct_discrepancy_actions():
    composer = SupplierIntelligenceComposer()
    caught = composer.caught_discrepancies(
        [
            {"metadata": {"supplier_id": "SUP-001", "amount": 100}, "actual_action": "flag_leakage", "is_correct": True},
            {"metadata": {"supplier_id": "SUP-001", "amount": 50}, "actual_action": "auto_approve", "is_correct": True},
            {"metadata": {"supplier_id": "SUP-001", "amount": 75}, "actual_action": "hold_for_review", "is_correct": False},
            {"metadata": {"supplier_id": "SUP-001", "amount": 25}, "actual_action": "escalate_to_buyer", "confirmed": True},
        ],
        {"supplier_id": "SUP-001"},
    )

    assert caught["count"] == 2
    assert caught["flagged_invoice_value"] == 125.0
    assert caught["source"] == "verified_outcomes"


def test_caught_discrepancies_require_explicit_correctness():
    composer = SupplierIntelligenceComposer()
    caught = composer.caught_discrepancies(
        [
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 10},
                "actual_action": "flag_leakage",
                "recommended_action": "flag_leakage",
            },
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 20},
                "actual_action": "flag_leakage",
                "is_correct": True,
            },
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 30},
                "actual_action": "hold_for_review",
                "correct": True,
            },
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 40},
                "actual_action": "escalate_to_buyer",
                "confirmed": True,
            },
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 60},
                "actual_action": "hold_for_review",
                "verified_correct": True,
            },
            {
                "metadata": {"supplier_id": "SUP-001", "amount": 50},
                "actual_action": "made_up_action",
                "is_correct": True,
            },
        ],
        {"supplier_id": "SUP-001"},
    )

    assert caught["count"] == 4
    assert caught["flagged_invoice_value"] == 150.0


def test_caught_discrepancies_no_safe_mapping_returns_zero_for_unknown_action():
    composer = SupplierIntelligenceComposer()
    caught = composer.caught_discrepancies(
        [{"metadata": {"supplier_id": "SUP-001"}, "actual_action": "made_up_action", "is_correct": True}],
        {"supplier_id": "SUP-001"},
    )

    assert caught["count"] == 0


def test_economic_exposure_source_breakdown_and_no_roi_claim():
    composer = SupplierIntelligenceComposer()
    exposure = composer.economic_exposure(
        _enrichment(exception_rate=0.10, accuracy=0.90, count=25),
        {"avg_invoice_amount": 1000},
    )

    assert exposure is not None
    assert exposure["amount"] == 2500.0
    assert exposure["source_breakdown"]["exception_rate"]["source"] == "verified_outcomes"
    assert exposure["source_breakdown"]["avg_invoice_amount"]["source"] == "fixture"
    caveat = exposure["caveat"].lower()
    assert "not confirmed savings" in caveat
    assert "roi" in caveat


def test_new_manager_summary():
    composer = SupplierIntelligenceComposer()
    summary = composer.new_manager_summary(
        "Supplier A",
        {"headline_tier": "reliable", "label": "3 of 4 metrics past threshold"},
        {"tier": "monitor", "basis": "context"},
        {"count": 2},
    )

    assert "Supplier A" in summary
    assert "reliable" in summary
    assert "monitor" in summary
    assert "Caught discrepancies: 2" in summary


def test_fallback_without_enrichment_is_integration_pending():
    composer = SupplierIntelligenceComposer(graph_store=FakeGraphStore({}))
    intelligence = composer.compose_profile("SUP-001")

    assert intelligence["depth"]["headline_tier"] == "none"
    assert intelligence["risk"]["tier"] == "integration_pending"
    assert "p39b_enrichment_unavailable" in intelligence["warnings"]


def test_fixture_fallback_remains_context_not_learned():
    composer = SupplierIntelligenceComposer()
    metric = composer.resolve_metric(
        "exception_rate",
        "SUP-001",
    )

    assert metric["source"] == "fixture"
    assert metric["verified"] is False
    assert metric["measured"] is False


def test_learned_provenance_preserved_in_compose_behavioral_metrics_and_depth():
    store = FakeGraphStore({"SUP-001": _enrichment(exception_rate=0.03, accuracy=0.96, count=60)})
    composer = SupplierIntelligenceComposer(graph_store=store)
    intelligence = composer.compose_profile("SUP-001")

    learned = intelligence["behavioral_metrics"]["learned"]["exception_rate"]
    assert learned["source"] == "verified_outcomes"
    assert learned["provenance_tier"] == "learned"
    assert learned["measured"] is True
    assert learned["verified"] is True
    assert learned["source_count"] == 60
    assert "exception_rate" in intelligence["depth"]["per_metric"]
    assert "total_decisions" not in intelligence["depth"]["per_metric"]


def test_existing_supplier_profile_fields_unchanged_by_intelligence_block():
    base_profile = s2p_suppliers._profile_detail(accumulator.get_profile("SUP-001"))
    response = s2p_suppliers.profile("SUP-001", _request(FakeGraphStore({"SUP-001": {}})))

    for key, value in base_profile.items():
        assert key in response
        assert type(response[key]) is type(value)
        assert response[key] == value
    assert set(response) == set(base_profile) | {"intelligence"}


def test_endpoint_adds_intelligence_with_enrichment_and_keeps_json_safe():
    store = FakeGraphStore({"SUP-001": _enrichment(exception_rate=0.03, accuracy=0.96, count=60)})
    response = s2p_suppliers.profile("SUP-001", _request(store))

    assert response["intelligence"]["depth"]["headline_tier"] in {"comprehensive", "reliable", "deep"}
    assert response["intelligence"]["risk"]["tier"] == "low"
    assert response["intelligence"]["behavioral_metrics"]["learned"]["exception_rate"]["source"] == "verified_outcomes"
    json.dumps(response)
    assert store.write_called is False


def test_unknown_supplier_behavior_remains_404():
    with pytest.raises(Exception) as excinfo:
        s2p_suppliers.profile("UNKNOWN", _request(FakeGraphStore({})))

    assert getattr(excinfo.value, "status_code", None) == 404


def test_fixture_otif_is_not_measured_or_verified():
    composer = SupplierIntelligenceComposer(graph_store=FakeGraphStore({"SUP-001": {}}))
    intelligence = composer.compose_profile("SUP-001")

    otif = intelligence["behavioral_metrics"]["context"]["otif"]
    assert otif["source"] == "fixture"
    assert otif["measured"] is False
    assert otif["verified"] is False


def test_lead_time_context_is_not_measured_or_verified():
    composer = SupplierIntelligenceComposer()
    intelligence = composer.compose_profile("SUP-001")

    lead_time = intelligence["behavioral_metrics"]["context"].get("avg_lead_time_days")
    if lead_time is not None:
        assert lead_time["source"] == "fixture"
        assert lead_time["measured"] is False
        assert lead_time["verified"] is False


def test_learned_metrics_carry_source_count():
    composer = SupplierIntelligenceComposer(graph_store=FakeGraphStore({"SUP-001": _enrichment(count=75)}))
    intelligence = composer.compose_profile("SUP-001")

    assert intelligence["behavioral_metrics"]["learned"]["accuracy"]["source_count"] == 75


def test_graph_read_count_is_not_treated_as_verified_risk_count():
    composer = SupplierIntelligenceComposer()
    risk = composer.risk_tier(
        {
            "total_decisions": ProvenancedValue(
                value=100,
                source="graph_store",
                provenance_tier="context",
                source_count=100,
                measured=True,
                verified=False,
            ),
            "exception_rate": _verified(0.25, count=5),
        }
    )

    assert risk["tier"] == "insufficient_data"
    assert risk["source_count"] == 5
