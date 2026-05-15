from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_no_soc_endpoints_in_openapi():
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    soc_paths = sorted(path for path in paths if "/soc/" in path)

    assert soc_paths == []


def test_s2p_endpoints_still_accessible():
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "s2p-copilot"

    preview = client.get("/api/s2p/preview/queue")
    assert preview.status_code == 200


def test_framework_conservation_still_works_or_not_mounted():
    response = client.get("/api/conservation/status")

    assert response.status_code != 500
    assert response.status_code == 200
