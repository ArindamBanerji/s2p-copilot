from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_transfer_status_smoke():
    response = client.get("/api/transfer/status")

    assert response.status_code == 200
    data = response.json()
    assert "warm_started" in data
    assert isinstance(data["warm_started"], bool)

    if data["warm_started"]:
        assert isinstance(data["source_copilot"], str)
        assert isinstance(data["patterns_transferred"], int)
        assert data["patterns_transferred"] > 0
