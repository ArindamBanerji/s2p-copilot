import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app


client = TestClient(app)


def test_extended_returns_200():
    response = client.get("/api/s2p/discovery/extended")

    assert response.status_code == 200


def test_extended_sorted_by_correlation():
    discoveries = client.get("/api/s2p/discovery/extended").json()["discoveries"]
    strengths = [row["correlation_strength"] for row in discoveries]

    assert strengths == sorted(strengths, reverse=True)


def test_extended_has_per_supplier():
    data = client.get("/api/s2p/discovery/extended").json()

    assert "per_supplier" in data
    assert "SUP-YANGTZE" in data["per_supplier"]
    assert data["per_supplier"]["SUP-YANGTZE"]["discovery_count"] >= 1


def test_extended_has_type_distribution():
    data = client.get("/api/s2p/discovery/extended").json()

    assert "by_type" in data
    assert data["by_type"]["commodity_risk"] == 1
    assert sum(data["by_type"].values()) == data["total"]


def test_extended_sources_connected():
    data = client.get("/api/s2p/discovery/extended").json()

    assert data["sources_connected"] >= 4


def test_discovery_has_propagation_path():
    discovery = client.get("/api/s2p/discovery/extended").json()["discoveries"][0]

    assert "propagation_path" in discovery
    assert len(discovery["propagation_path"]) >= 3


def test_discovery_has_detection_history():
    discovery = client.get("/api/s2p/discovery/extended").json()["discoveries"][0]

    assert discovery["first_detected"]
    assert discovery["detection_count"] > 0


def test_supplier_with_discoveries():
    response = client.get("/api/s2p/discovery/supplier/SUP-YANGTZE")

    assert response.status_code == 200
    data = response.json()
    assert data["supplier_id"] == "SUP-YANGTZE"
    assert data["total"] >= 1
    assert data["total_detection_count"] >= data["total"]


def test_supplier_without_discoveries():
    response = client.get("/api/s2p/discovery/supplier/UNKNOWN")

    assert response.status_code == 200
    data = response.json()
    assert data["discoveries"] == []
    assert data["total"] == 0
    assert data["total_detection_count"] == 0


def test_supplier_sorted_by_correlation():
    discoveries = client.get("/api/s2p/discovery/supplier/SUP-YANGTZE").json()["discoveries"]
    strengths = [row["correlation_strength"] for row in discoveries]

    assert strengths == sorted(strengths, reverse=True)


def test_propagation_known_discovery():
    response = client.get("/api/s2p/discovery/propagation/DISC-EXT-001")

    assert response.status_code == 200
    data = response.json()
    assert data["discovery_id"] == "DISC-EXT-001"
    assert "propagation_path" in data
    assert data["recommendation"]


def test_propagation_unknown_404():
    response = client.get("/api/s2p/discovery/propagation/UNKNOWN")

    assert response.status_code == 404


def test_extended_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/discovery/extended" in paths


def test_supplier_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/discovery/supplier/{supplier_id}" in paths


def test_propagation_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/discovery/propagation/{discovery_id}" in paths


def test_existing_alerts_endpoint_still_works():
    response = client.get("/api/s2p/discovery/alerts")

    assert response.status_code == 200
    data = response.json()
    assert data["total_discoveries"] == 3
    assert data["highest_impact"] == "$420K exposure in 6 weeks"


def test_existing_disruptions_endpoint_still_works():
    response = client.get("/api/s2p/discovery/disruptions")

    assert response.status_code == 200
    data = response.json()
    assert data["total_disruptions"] == 3
    assert data["cumulative_savings"] == 27_500_000.0
