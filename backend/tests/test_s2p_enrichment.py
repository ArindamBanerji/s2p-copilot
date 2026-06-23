from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.enrichment import ProvenancedValue

from app.main import app
from app.services.s2p_context_builder import S2PContextBuilder
from app.services.s2p_enrichment import (
    DOMAIN,
    ENTITY_TYPE,
    NAMESPACE,
    S2PSupplierEnrichmentService,
    serialize_provenanced_value,
)


client = TestClient(app)


def _store() -> InMemoryGraphStore:
    return InMemoryGraphStore(domain=DOMAIN)


def _write_decision(
    store: InMemoryGraphStore,
    *,
    decision_id: str,
    supplier_id: str = "SUP-001",
    category: str = "price_variance",
    action: str = "hold_for_review",
    correct: bool | None = True,
    created_at: float = 1767225600.0,
    verified_at: float | None = None,
) -> str:
    store.write_decision(
        DOMAIN,
        category,
        action,
        0.8,
        {"price_variance": 0.7, "contract_gap": 0.2},
        metadata={
            "decision_id": decision_id,
            "supplier_id": supplier_id,
            "supplier_name": f"Supplier {supplier_id}",
            "invoice_id": f"INV-{decision_id}",
            "created_at": created_at,
        },
    )
    if correct is not None:
        store.write_outcome(
            decision_id,
            action if correct else "escalate_to_buyer",
            bool(correct),
            metadata={"verified_at": verified_at if verified_at is not None else created_at + 60},
        )
    return decision_id


def _service(store: InMemoryGraphStore | None = None) -> S2PSupplierEnrichmentService:
    return S2PSupplierEnrichmentService(graph_store=store or _store())


def _metric(metrics: dict, name: str):
    return metrics[name].value if hasattr(metrics[name], "value") else metrics[name]["value"]


def test_verified_metrics_from_verified_outcomes_only():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=False)
    _write_decision(store, decision_id="D-3", correct=None)

    metrics, source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["verified_decisions"].value == 2
    assert metrics["total_decisions"].value == 3
    assert source_set.verified_decision_count == 2
    assert source_set.unverified_decision_count == 1


def test_unverified_graphstore_rows_excluded_from_accuracy():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=None)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["accuracy"].value == 1.0


def test_unverified_graphstore_rows_excluded_from_exception_rate():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=False)
    _write_decision(store, decision_id="D-2", correct=None)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["exception_rate"].value == 1.0


def test_graphstore_read_count_reported_separately_from_verified_count():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=None)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["verified_decisions"].value == 1
    assert metrics["unverified_decisions"].value == 1
    assert metrics["total_decisions"].value == 2


def test_exception_rate_computation_correct():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=False)
    _write_decision(store, decision_id="D-3", correct=False)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["exception_rate"].value == 0.6667


def test_accuracy_computation_correct():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=False)
    _write_decision(store, decision_id="D-3", correct=True)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["accuracy"].value == 0.6667


def test_category_distribution_correct():
    store = _store()
    _write_decision(store, decision_id="D-1", category="price_variance", correct=True)
    _write_decision(store, decision_id="D-2", category="contract_gap", correct=None)
    _write_decision(store, decision_id="D-3", category="price_variance", correct=True)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["category_distribution"].value == {"contract_gap": 1, "price_variance": 2}
    assert metrics["category_distribution"].verified is False


def test_quarterly_distribution_handles_year_boundary():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True, verified_at=1767139200.0)
    _write_decision(store, decision_id="D-2", correct=True, verified_at=1767225600.0)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["decision_count_by_quarter"].value == {"2025-Q4": 1, "2026-Q1": 1}


def test_trend_insufficient_data_returns_insufficient_data():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["trend"].value == "insufficient_data"


def test_trend_uses_verified_history_only():
    store = _store()
    for index in range(6):
        _write_decision(store, decision_id=f"D-V-{index}", correct=True, verified_at=1767225600.0 + index)
    for index in range(6):
        _write_decision(store, decision_id=f"D-U-{index}", correct=None, created_at=1767225700.0 + index)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["trend"].value == "stable"
    assert metrics["trend"].source_count == 6


def test_trend_deteriorating_from_verified_history():
    store = _store()
    for index in range(3):
        _write_decision(store, decision_id=f"D-G-{index}", correct=True, verified_at=1767225600.0 + index)
    for index in range(3):
        _write_decision(store, decision_id=f"D-B-{index}", correct=False, verified_at=1767225700.0 + index)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["trend"].value == "deteriorating"


def test_trend_improving_from_verified_history():
    store = _store()
    for index in range(3):
        _write_decision(store, decision_id=f"D-B-{index}", correct=False, verified_at=1767225600.0 + index)
    for index in range(3):
        _write_decision(store, decision_id=f"D-G-{index}", correct=True, verified_at=1767225700.0 + index)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001", min_decisions=1)

    assert metrics["trend"].value == "improving"


def test_fixture_supplier_fields_are_context_integration_pending():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")

    assert metrics["otif_score"].source == "fixture"
    assert metrics["otif_score"].provenance_tier == "context"
    assert metrics["otif_score"].measured is False


def test_otif_response_has_provenance():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")
    payload = S2PSupplierEnrichmentService.serialize_values(metrics)

    assert payload["otif_score"]["provenance"] == "sample"
    assert payload["otif_score"]["source"] == "fixture"


def test_live_connector_provenance_is_scraped():
    payload = serialize_provenanced_value(
        ProvenancedValue(
            value=0.97,
            source="supplier_connector",
            provenance_tier="scraped_external",
            provenance_label="live supplier connector",
            measured=True,
            verified=False,
        )
    )

    assert payload["provenance"] == "scraped_external"
    assert payload["source"] == "supplier_connector"


def test_lead_time_fixture_data_has_context_provenance():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")

    assert metrics["avg_lead_time_days"].source == "fixture"
    assert metrics["avg_lead_time_days"].measured is False


def test_enrichment_lead_time_response_has_provenance():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")
    payload = S2PSupplierEnrichmentService.serialize_values(metrics)

    assert payload["avg_lead_time_days"]["provenance"] == "sample"
    assert payload["avg_lead_time_days"]["source"] == "fixture"


def test_unavailable_metrics_are_honest():
    metrics, _source_set = _service().compute_supplier_metrics("UNKNOWN-SUP")

    assert metrics["avg_lead_time_days"].source == "unavailable"
    assert metrics["otif_score"].source == "unavailable"
    assert metrics["otif_score"].measured is False


def test_zero_verified_supplier_rate_metrics_are_unavailable():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")

    assert metrics["exception_rate"].source != "verified_outcomes"
    assert metrics["exception_rate"].measured is False
    assert metrics["exception_rate"].verified is False
    assert metrics["accuracy"].source != "verified_outcomes"
    assert metrics["accuracy"].measured is False
    assert metrics["accuracy"].verified is False
    assert metrics["trend"].value == "insufficient_data"
    assert metrics["trend"].provenance_tier == "unavailable"
    assert metrics["trend"].measured is False
    assert metrics["trend"].verified is False


def test_zero_verified_supplier_quarterly_distribution_not_learned():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")

    quarterly = metrics["decision_count_by_quarter"]
    assert quarterly.value == {}
    assert quarterly.source != "verified_outcomes"
    assert quarterly.provenance_tier == "unavailable"
    assert quarterly.measured is False
    assert quarterly.verified is False


def test_verified_metrics_use_verified_provenance():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["accuracy"].source == "verified_outcomes"
    assert metrics["accuracy"].verified is True
    assert metrics["accuracy"].measured is True


def test_verified_supplier_rate_metrics_still_learned():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=False)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["exception_rate"].source == "verified_outcomes"
    assert metrics["exception_rate"].provenance_tier == "learned"
    assert metrics["exception_rate"].measured is True
    assert metrics["accuracy"].source == "verified_outcomes"
    assert metrics["accuracy"].verified is True


def test_trend_requires_sufficient_verified_history():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _write_decision(store, decision_id="D-2", correct=False)

    metrics, _source_set = _service(store).compute_supplier_metrics("SUP-001")

    assert metrics["trend"].value == "insufficient_data"
    assert metrics["trend"].source == "unavailable"
    assert metrics["trend"].measured is False
    assert metrics["trend"].verified is False


def test_run_includes_zero_verified_supplier_without_fake_measured_rates():
    store = _store()

    result = _service(store).run(dry_run=True, min_decisions=1)

    supplier = next(item for item in result["suppliers"] if item["supplier_id"] == "SUP-001")
    metrics = supplier["metrics"]
    assert metrics["exception_rate"]["source"] != "verified_outcomes"
    assert metrics["exception_rate"]["measured"] is False
    assert metrics["accuracy"]["source"] != "verified_outcomes"
    assert metrics["accuracy"]["verified"] is False
    assert metrics["trend"]["value"] == "insufficient_data"


def test_no_fixture_metric_labeled_measured_verified():
    metrics, _source_set = _service().compute_supplier_metrics("SUP-001")

    fixture_metrics = [metric for metric in metrics.values() if metric.source == "fixture"]
    assert fixture_metrics
    assert all(metric.measured is False and metric.verified is False for metric in fixture_metrics)


def test_supplier_enrichment_writes_to_graphstore_entity_enrichment_api():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)

    result = _service(store).run(dry_run=False, min_decisions=1)

    assert result["report"]["receipts_persisted"] >= 1
    assert store.read_entity_enrichment(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id="SUP-001",
        namespace=NAMESPACE,
    )


def test_supplier_enrichment_read_after_write_roundtrips_provenanced_values():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    service.run(dry_run=False, min_decisions=1)

    metrics = service.read_supplier("SUP-001")

    assert metrics["accuracy"].source == "verified_outcomes"
    assert metrics["accuracy"].value == 1.0


def test_protected_identity_fields_are_not_written_as_metrics():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    metrics, source_set = service.compute_supplier_metrics("SUP-001")
    metrics["supplier_id"] = deepcopy(metrics["accuracy"])

    receipt = store.write_entity_enrichment(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id="SUP-001",
        namespace=NAMESPACE,
        metrics=metrics,
        computed_from=source_set,
        dry_run=False,
    )

    assert "supplier_id" in receipt.protected_fields_rejected
    assert "supplier_id" not in service.read_supplier("SUP-001")


def test_receipt_reports_metrics_written_and_rejected():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    metrics, source_set = service.compute_supplier_metrics("SUP-001")
    metrics["name"] = deepcopy(metrics["accuracy"])

    receipt = store.write_entity_enrichment(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id="SUP-001",
        namespace=NAMESPACE,
        metrics=metrics,
        computed_from=source_set,
        dry_run=False,
    )

    assert receipt.metrics_written
    assert "name" in receipt.metrics_rejected


def test_summary_reads_persisted_enrichment_not_fixture_cache():
    store = _store()
    service = _service(store)
    assert service.summary() == []
    _write_decision(store, decision_id="D-1", correct=False)
    service.run(dry_run=False, min_decisions=1)

    summary = service.summary()

    assert summary
    assert summary[0]["metrics"]["exception_rate"]["value"] == 1.0


def test_supplier_endpoint_reads_persisted_enrichment():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    service.run(dry_run=False, min_decisions=1)

    metrics = service.read_supplier("SUP-001")

    assert metrics["verified_decisions"].value == 1


def test_dry_run_does_not_persist():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)

    _service(store).run(dry_run=True, min_decisions=1)

    assert store.read_entity_enrichment(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id="SUP-001",
        namespace=NAMESPACE,
    ) == {}


def test_idempotent_run_same_inputs_same_persisted_metrics():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    service.run(dry_run=False, min_decisions=1)
    first = service.read_supplier("SUP-001")
    service.run(dry_run=False, min_decisions=1)
    second = service.read_supplier("SUP-001")

    assert first["accuracy"].value == second["accuracy"].value
    assert first["exception_rate"].value == second["exception_rate"].value


def test_run_endpoint_dry_run_returns_receipt_without_persisting(monkeypatch):
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.post("/api/s2p/enrichment/run", params={"dry_run": "true", "min_decisions": "1"})

    assert response.status_code == 200
    body = response.json()
    assert body["report"]["dry_run"] is True
    assert body["receipts"]
    assert store.read_entity_enrichment(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id="SUP-001",
        namespace=NAMESPACE,
    ) == {}


def test_run_endpoint_persist_returns_persisted_receipts(monkeypatch):
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.post("/api/s2p/enrichment/run", params={"dry_run": "false", "min_decisions": "1"})

    assert response.status_code == 200
    body = response.json()
    assert any(receipt["persisted"] for receipt in body["receipts"])


def test_supplier_endpoint_returns_enriched_supplier(monkeypatch):
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    service = _service(store)
    service.run(dry_run=False, min_decisions=1)
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.get("/api/s2p/enrichment/supplier/SUP-001")

    assert response.status_code == 200
    assert response.json()["metrics"]["accuracy"]["verified"] is True


def test_supplier_endpoint_unknown_supplier_safe(monkeypatch):
    store = _store()
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.get("/api/s2p/enrichment/supplier/UNKNOWN")

    assert response.status_code == 404


def test_summary_endpoint_sorted_by_exception_rate_desc(monkeypatch):
    store = _store()
    _write_decision(store, decision_id="D-1", supplier_id="SUP-A", correct=True)
    _write_decision(store, decision_id="D-2", supplier_id="SUP-B", correct=False)
    _service(store).run(dry_run=False, min_decisions=1)
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.get("/api/s2p/enrichment/summary")

    assert response.status_code == 200
    suppliers = response.json()["suppliers"]
    assert suppliers[0]["metrics"]["exception_rate"]["value"] >= suppliers[-1]["metrics"]["exception_rate"]["value"]


def test_alerts_endpoint_flags_only_verified_measured_metrics(monkeypatch):
    store = _store()
    _write_decision(store, decision_id="D-1", correct=False)
    _service(store).run(dry_run=False, min_decisions=1)
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.get("/api/s2p/enrichment/alerts", params={"threshold": "0.1"})

    assert response.status_code == 200
    alerts = response.json()["alerts"]
    assert alerts
    assert alerts[0]["exception_rate"]["verified"] is True


def test_zero_data_summary_safe(monkeypatch):
    monkeypatch.setattr(app.state, "graph_store", _store())

    response = client.get("/api/s2p/enrichment/summary")

    assert response.status_code == 200
    assert response.json()["suppliers"] == []


def test_context_builder_output_unchanged_when_no_enrichment_exists():
    store = _store()
    baseline = S2PContextBuilder(graph_store=None).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id=None,
        context_data={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    )
    result = S2PContextBuilder(graph_store=store).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id=None,
        context_data={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    )

    baseline_supplier = next(node for node in baseline.nodes if node.type == "supplier")
    result_supplier = next(node for node in result.nodes if node.type == "supplier")
    assert "enrichment" not in baseline_supplier.properties
    assert "enrichment" not in result_supplier.properties


def test_context_builder_uses_enrichment_when_available():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _service(store).run(dry_run=False, min_decisions=1)

    result = S2PContextBuilder(graph_store=store).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id=None,
        context_data={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    )

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.properties["enrichment"]["accuracy"]["source"] == "verified_outcomes"


def test_context_builder_falls_back_to_fixture_when_enrichment_empty():
    result = S2PContextBuilder(graph_store=_store()).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id=None,
        context_data={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    )

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.properties["source"] == "fixture"
    assert "enrichment" not in supplier.properties


def test_context_builder_renders_enrichment_with_provenance():
    store = _store()
    _write_decision(store, decision_id="D-1", correct=True)
    _service(store).run(dry_run=False, min_decisions=1)

    result = S2PContextBuilder(graph_store=store).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id=None,
        context_data={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    )

    supplier = next(node for node in result.nodes if node.type == "supplier")
    enrichment = supplier.properties["enrichment"]
    assert enrichment["accuracy"]["provenance_tier"] == "learned"
    assert enrichment["avg_lead_time_days"]["source"] in {"fixture", "unavailable"}


def test_p37_trust_explanation_still_present():
    response = client.get("/api/s2p/evidence/template", params={"category": "price_variance"})

    assert response.status_code == 200
    assert "trust_explanation" in response.json()


def test_p35_evidence_template_safe_paths_still_pass():
    omitted = client.get("/api/s2p/evidence/template", params={"category": "price_variance"})
    nonexistent = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance", "invoice_id": "NOPE"},
    )
    unknown = client.get("/api/s2p/evidence/template", params={"category": "unknown_category"})

    assert omitted.status_code == 200
    assert nonexistent.status_code == 200
    assert unknown.status_code == 200
