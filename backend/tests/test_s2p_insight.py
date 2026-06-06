import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.routers import s2p_insight

client = TestClient(app)


def test_fingerprint_returns_seven_factors():
    response = client.get("/api/s2p/insight/fingerprint", params={"invoice_id": "S2P-INV-0001"})

    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == "S2P-INV-0001"
    assert set(data["factors"]) == set(S2PDomainConfig.factors)
    assert data["dominant_factor"] in S2PDomainConfig.factors


def test_fingerprint_unknown_invoice_safe():
    response = client.get("/api/s2p/insight/fingerprint", params={"invoice_id": "missing"})

    assert response.status_code == 200
    assert "not found" in response.json()["error"]


def test_similar_returns_list_with_distances():
    response = client.get("/api/s2p/insight/similar", params={"invoice_id": "S2P-INV-0001", "limit": 3})

    assert response.status_code == 200
    data = response.json()
    assert len(data["similar"]) == 3
    assert all("distance" in item for item in data["similar"])
    assert data["similar"] == sorted(data["similar"], key=lambda item: item["distance"])


def test_similar_invoices_returns_results():
    response = client.get("/api/s2p/insight/similar", params={"invoice_id": "S2P-INV-0001", "limit": 3})

    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 3
    assert len(data["similar"]) == 3


def test_similar_excludes_self():
    response = client.get("/api/s2p/insight/similar", params={"invoice_id": "S2P-INV-0001", "limit": 10})

    assert response.status_code == 200
    assert all(item["invoice_id"] != "S2P-INV-0001" for item in response.json()["similar"])


def test_process_context_returns_fixture_timeline():
    response = client.get("/api/s2p/insight/process-context/S2P-INV-0001")

    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == "S2P-INV-0001"
    assert data["supplier_id"] == "SUP-001"
    assert data["category"] == "contract_gap"
    assert data["source"] == "fixture"
    assert data["engine"] == "ci-platform-s2p"
    assert data["total_cycle_time_hours"] == 24.0
    assert len(data["activities"]) == 6
    assert data["activity_timeline"] == data["activities"]
    assert {"activity", "pct_of_total", "duration_hours", "system"}.issubset(data["activities"][0])


def test_process_context_bottleneck_is_longest_activity():
    response = client.get("/api/s2p/insight/process-context/S2P-INV-0001")

    assert response.status_code == 200
    data = response.json()
    longest = max(data["activities"], key=lambda item: item["duration_hours"])
    assert data["bottleneck"]["activity"] == longest["activity"]
    assert data["bottleneck"]["duration_hours"] == longest["duration_hours"]
    assert data["bottleneck"]["pct_of_total"] == longest["pct_of_total"]
    assert data["bottleneck"]["reason"]
    assert data["bottleneck"]["system"] == longest["system"]


def test_process_context_missing_invoice_returns_404():
    response = client.get("/api/s2p/insight/process-context/missing")

    assert response.status_code == 404


def test_process_context_activity_durations_sum_to_total():
    response = client.get("/api/s2p/insight/process-context/S2P-INV-0001")

    assert response.status_code == 200
    data = response.json()
    total = sum(activity["duration_hours"] for activity in data["activities"])
    assert abs(total - data["total_cycle_time_hours"]) <= 0.02


def test_cross_graph_returns_correlations():
    response = client.get("/api/s2p/insight/cross-graph")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] > 0
    assert data["bottleneck_duration"] >= 0
    assert {"supplier", "supplier_id", "exception_rate", "impact_score"}.issubset(data["insights"][0])


def test_cross_graph_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(s2p_insight, "_load_suppliers", lambda: [])
    monkeypatch.setattr(s2p_insight, "_load_celonis", lambda: {})

    response = client.get("/api/s2p/insight/cross-graph")

    assert response.status_code == 200
    assert response.json()["insights"] == []


def test_process_signals_returns_bottleneck_process_data():
    response = client.get("/api/s2p/insight/process-signals", params={"supplier_id": "SUP-001"})

    assert response.status_code == 200
    data = response.json()
    assert data["available"] is True
    assert data["process_model"] == "Purchase-to-Pay"
    assert any(activity.get("bottleneck") is True for activity in data["activities"])


def test_process_signals_unknown_supplier_safe():
    response = client.get("/api/s2p/insight/process-signals", params={"supplier_id": "UNKNOWN"})

    assert response.status_code == 200
    assert response.json()["supplier_id"] == "UNKNOWN"


def test_all_insight_endpoints_200():
    paths = [
        "/api/s2p/insight/fingerprint?invoice_id=S2P-INV-0001",
        "/api/s2p/insight/similar?invoice_id=S2P-INV-0001",
        "/api/s2p/insight/process-context/S2P-INV-0001",
        "/api/s2p/insight/cross-graph",
        "/api/s2p/insight/process-signals",
    ]

    for path in paths:
        assert client.get(path).status_code == 200


def test_process_fusion_narrative_data_available():
    data = client.get("/api/s2p/insight/cross-graph").json()
    process = client.get("/api/s2p/insight/process-signals").json()

    assert data["bottleneck_activity"]
    assert process["recommendations"]
