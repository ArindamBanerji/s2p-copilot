from fastapi.testclient import TestClient

from app.main import app


def test_s2p_measurement_state_endpoint_returns_day_zero_shape():
    client = TestClient(app)
    response = client.get("/api/s2p/measurement-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] in {"instrument_validated", "accumulating", "measured"}
    assert {"decisions_verified", "decisions_needed", "accuracy", "iks"} <= payload.keys()
    assert client.get("/api/s2p/score").status_code in {404, 405}
    assert client.get("/api/s2p/health").status_code == 404


def test_s2p_measurement_router_does_not_expose_generic_score_route():
    response = TestClient(app).post("/api/score", json={})

    assert response.status_code == 404
