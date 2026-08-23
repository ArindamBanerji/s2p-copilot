from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG
from app.main import app
from app.routers import s2p_evolution as s2p_evolution_router
from app.services import s2p_evolver


SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "s2p_evolver.py"
ROUTER_PATH = Path(__file__).resolve().parents[1] / "app" / "routers" / "s2p_evolution.py"


@pytest.fixture(autouse=True)
def reset_evolver_state():
    class FixedProvider:
        def __init__(self, status: str = "GREEN") -> None:
            self.status = status

        def get_state(self) -> dict[str, str]:
            return {"status": self.status}

        def __call__(self) -> dict[str, str]:
            return self.get_state()

    provider = FixedProvider()
    s2p_evolver.set_conservation_provider(provider)
    s2p_evolver.reset_s2p_evolver()
    yield provider
    s2p_evolver.reset_s2p_evolver()
    s2p_evolver.set_conservation_provider(provider)


def _seed_qualifying_candidate() -> None:
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
        s2p_evolver.record_triage_outcome(
            "EVIDENCE_ORDER_v2",
            is_correct=True,
            category="price_variance",
        )


def test_promotion_blocked_on_amber(reset_evolver_state):
    _seed_qualifying_candidate()

    reset_evolver_state.status = "AMBER"
    result = s2p_evolver.check_promotion()

    assert result is not None
    assert result["promoted"] is False
    assert "conservation" in str(result["reason"])


def test_promotion_allowed_on_green():
    _seed_qualifying_candidate()

    result = s2p_evolver.check_promotion()

    assert result is not None
    assert result["promoted_id"] == "EVIDENCE_ORDER_v2"
    assert result.get("reason") != "conservation"


def test_no_literal_green_in_code():
    service_source = SERVICE_PATH.read_text(encoding="utf-8")
    router_source = ROUTER_PATH.read_text(encoding="utf-8")

    for source in (service_source, router_source):
        assert 'conservation_state="GREEN"' not in source
        assert "conservation_state='GREEN'" not in source


def test_provider_returns_live_state(monkeypatch, reset_evolver_state):
    _seed_qualifying_candidate()
    reset_evolver_state.status = "AMBER"
    seen_requests = []

    def live_provider(request):
        seen_requests.append(request)
        return "AMBER"

    monkeypatch.setattr(s2p_evolution_router, "_current_conservation_status", live_provider)
    response = TestClient(app).get("/api/s2p/evolution/promotion-check")

    assert response.status_code == 200
    assert seen_requests
    assert response.json()["promotion"]["reason"] == "conservation_gate_red"
