import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from copilot_sdk.graph.memory_store import InMemoryGraphStore

from app.main import app
from app.services.compliance_screener import ComplianceScreener

client = TestClient(app)


def test_screen_clean():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-1", "supplier_id": "S1"},
        {
            "supplier_id": "S1",
            "supplier_name": "Clean Supplier",
            "country_of_origin": "germany",
            "labor_practices": "audited",
            "carbon_footprint": 50.0,
            "environmental_due_diligence": True,
        },
    )

    assert check["cleared"] is True
    assert check["risk_level"] == "low"


def test_screen_uflpa_flag():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-2", "supplier_id": "S2"},
        {"supplier_id": "S2", "supplier_name": "Risk Supplier", "country_of_origin": "Xinjiang"},
    )

    assert check["cleared"] is False
    assert check["flags"][0]["regulation"] == "UFLPA"


def test_screen_csddd_flag():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-3", "supplier_id": "S3"},
        {"supplier_id": "S3", "supplier_name": "Chen-Lin", "environmental_due_diligence": False},
    )

    assert any(flag["regulation"] == "CSDDD" for flag in check["flags"])


def test_screen_scope3_flag():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-4", "supplier_id": "S4", "carbon_footprint": 1_500},
        {"supplier_id": "S4", "supplier_name": "High Emissions"},
    )

    assert any(flag["regulation"] == "SCOPE3" for flag in check["flags"])


def test_risk_level_high():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-5", "supplier_id": "S5"},
        {"supplier_id": "S5", "supplier_name": "Sanctioned", "sanctioned": True},
    )

    assert check["risk_level"] == "high"


def test_risk_level_low():
    check = ComplianceScreener().screen(
        {"transaction_id": "INV-6", "supplier_id": "S6"},
        {
            "supplier_id": "S6",
            "supplier_name": "Low Risk",
            "country_of_origin": "US",
            "labor_practices": "audited",
            "carbon_footprint": 50.0,
        },
    )

    assert check["risk_level"] == "low"


def test_audit_hash_deterministic():
    screener = ComplianceScreener()
    transaction = {"transaction_id": "INV-7", "supplier_id": "S7"}
    supplier = {"supplier_id": "S7", "supplier_name": "Stable", "environmental_due_diligence": True}

    assert screener.screen(transaction, supplier)["audit_hash"] == screener.screen(transaction, supplier)["audit_hash"]


def test_audit_hash_different():
    screener = ComplianceScreener()
    supplier = {"supplier_id": "S8", "supplier_name": "Stable"}

    first = screener.screen({"transaction_id": "INV-8", "supplier_id": "S8"}, supplier)
    second = screener.screen({"transaction_id": "INV-9", "supplier_id": "S8"}, supplier)

    assert first["audit_hash"] != second["audit_hash"]


def test_batch_screen():
    checks = ComplianceScreener().batch_screen(
        [{"transaction_id": f"INV-{index}", "supplier_id": "S1"} for index in range(10)],
        [{"supplier_id": "S1", "supplier_name": "Batch Supplier"}],
    )

    assert len(checks) == 10
    assert all("audit_hash" in check for check in checks)


def test_report_conservation_proof():
    report = ComplianceScreener().report([], {"alpha": 0.9, "q": 0.8, "V": 1.1, "status": "GREEN"})

    assert report["conservation_proof"]["status"] == "GREEN"
    assert report["conservation_proof"]["alpha"] == 0.9


def test_report_accuracy():
    screener = ComplianceScreener()
    checks = []
    for index in range(8):
        check = screener.screen(
            {"transaction_id": f"INV-{index}", "supplier_id": "S11"},
            {"supplier_id": "S11", "environmental_due_diligence": False},
        )
        check["verified_correct"] = index < 7
        checks.append(check)
    report = screener.report(checks)

    assert report["accuracy"] == 0.875


def test_report_narrative():
    screener = ComplianceScreener()
    check = screener.screen(
        {"transaction_id": "INV-12", "supplier_id": "S12"},
        {"supplier_id": "S12", "environmental_due_diligence": False},
    )
    check["verified_correct"] = True
    report = screener.report([check], {"alpha": 0.92, "q": 0.88, "V": 1.2, "status": "GREEN"})

    assert "Screened 100% of transactions with high or medium risk; 100% accuracy" in report["narrative"]
    assert "Tamper-evident audit trail attached" in report["narrative"]


def test_router_screen():
    response = client.post(
        "/api/s2p/compliance/screen",
        json={
            "transaction": {"transaction_id": "INV-2847", "supplier_id": "SUP-CHEN"},
            "supplier": {"supplier_id": "SUP-CHEN", "supplier_name": "Chen-Lin", "environmental_due_diligence": False},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["transaction_id"] == "INV-2847"
    assert "narrative" in data


def test_router_report():
    response = client.get("/api/s2p/compliance/report")

    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "period",
        "total_screened",
        "flagged_count",
        "high_risk_screened",
        "high_risk_pct",
        "accuracy",
        "conservation_proof",
        "provenance",
        "narrative",
    }
    assert data["provenance"] == "learned"
    assert "conservation_proof" in data
    assert "narrative" in data


def test_router_report_reads_graph_decision_and_verified_outcome(monkeypatch):
    store = InMemoryGraphStore(domain="s2p")
    decision_id = store.write_decision(
        "s2p",
        "format_compliance",
        "hold_for_review",
        0.91,
        {"tax_regulatory_compliance": 0.2},
        metadata={
            "invoice_id": "INV-GRAPH-1",
            "supplier_id": "SUP-GRAPH-1",
            "supplier_name": "Graph Supplier",
            "environmental_due_diligence": False,
        },
    )
    store.write_outcome(
        decision_id,
        "hold_for_review",
        True,
        domain="s2p",
    )
    monkeypatch.setattr(app.state, "graph_store", store)

    response = client.get("/api/s2p/compliance/report")

    assert response.status_code == 200
    data = response.json()
    assert data["total_screened"] == 1
    assert data["flagged_count"] == 1
    assert data["high_risk_screened"] == 1
    assert data["accuracy"] == 1.0
    assert data["provenance"] == "learned"
    assert data["conservation_proof"]["note"] == "Computed from 1 verified S2P outcomes"


def test_router_report_empty_graph_is_empty_learned_result(monkeypatch):
    monkeypatch.setattr(app.state, "graph_store", InMemoryGraphStore(domain="s2p"))

    response = client.get("/api/s2p/compliance/report")

    assert response.status_code == 200
    data = response.json()
    assert data["total_screened"] == 0
    assert data["flagged_count"] == 0
    assert data["high_risk_screened"] == 0
    assert data["high_risk_pct"] == 0.0
    assert data["accuracy"] is None
    assert data["provenance"] == "learned"
    assert data["conservation_proof"] == {
        "status": "RED",
        "note": "Computed from 0 verified S2P outcomes",
    }


def test_router_report_does_not_silence_verified_history_failure(monkeypatch):
    class FailingVerifiedStore(InMemoryGraphStore):
        def get_verified_decisions(self, domain, **kwargs):
            raise RuntimeError("verified history unavailable")

    monkeypatch.setattr(app.state, "graph_store", FailingVerifiedStore(domain="s2p"))

    with pytest.raises(RuntimeError, match="verified history unavailable"):
        client.get("/api/s2p/compliance/report")


def test_report_computes_from_checks():
    screener = ComplianceScreener()
    checks = []
    for index in range(10):
        supplier = {
            "supplier_id": f"S{index}",
            "country_of_origin": "germany",
            "labor_practices": "audited",
            "carbon_footprint": 50.0,
        }
        if index < 3:
            supplier["environmental_due_diligence"] = False
        checks.append(screener.screen({"transaction_id": f"INV-X-{index}", "supplier_id": f"S{index}"}, supplier))

    report = screener.report(checks)

    assert report["high_risk_pct"] == 0.3


def test_report_accuracy_from_verified():
    screener = ComplianceScreener()
    checks = []
    for index in range(8):
        check = screener.screen({"transaction_id": f"INV-V-{index}", "supplier_id": "S"}, {"supplier_id": "S"})
        check["verified_correct"] = index < 7
        checks.append(check)

    report = screener.report(checks)

    assert report["accuracy"] == 0.875


def test_report_no_conservation_labeled():
    report = ComplianceScreener().report([])

    assert report["provenance"] == "demo"
    assert report["conservation_proof"]["status"] == "unknown"
