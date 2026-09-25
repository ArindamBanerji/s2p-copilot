from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_thresholds_get() -> None:
    response = client.get("/api/s2p/confidence/thresholds")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["thresholds"]) == 4
    assert payload["calibration_source"] == "geometry_derived"


def test_route_low_amount_high_conf() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        json={"invoice_amount": 200, "confidence": 0.75, "calibration_source": "geometry_derived"},
    )
    assert response.status_code == 200
    assert response.json()["routing"] == "auto_approve"


def test_route_low_amount_low_conf() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        json={"invoice_amount": 200, "confidence": 0.65, "calibration_source": "geometry_derived"},
    )
    assert response.status_code == 200
    assert response.json()["routing"] == "manual_review"


def test_route_high_amount_high_conf() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        json={"invoice_amount": 60000, "confidence": 0.92, "calibration_source": "geometry_derived"},
    )
    assert response.status_code == 200
    assert response.json()["routing"] == "auto_approve"


def test_route_high_amount_low_conf() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        json={"invoice_amount": 60000, "confidence": 0.85, "calibration_source": "geometry_derived"},
    )
    assert response.status_code == 200
    assert response.json()["routing"] == "manual_review"


def test_k14_noise_rate_present() -> None:
    response = client.get("/api/s2p/confidence/thresholds")
    assert response.status_code == 200
    assert response.json()["k14_noise_rate"] == 0.92
