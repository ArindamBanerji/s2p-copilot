from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.main import SituationClassifier, app
from app.services.s2p_autonomy import S2PAutonomyManager, _score_payload
from copilot_sdk.scoring.budget_policy import AdaptiveBudgetPolicy


client = TestClient(app)


def test_twin_preserves_action_name() -> None:
    payload = _score_payload(SimpleNamespace(action_name="auto_approve", action="", confidence=0.91, action_index=0))
    assert payload["action"] == "auto_approve"


def test_twin_no_empty_action() -> None:
    payload = _score_payload(SimpleNamespace(action_name="hold_for_review", confidence=0.61, action_index=1))
    assert payload["action"]


def test_budget_uses_verified_count(monkeypatch) -> None:
    scorer = SimpleNamespace(get_verified_count=lambda: 17)
    monkeypatch.setattr(app.state, "scorer", scorer)
    assert main._s2p_verified_count() == 17


def test_budget_callback_no_self_error(monkeypatch) -> None:
    scorer = SimpleNamespace(get_verified_count=lambda: 23)
    monkeypatch.setattr(app.state, "scorer", scorer)
    provider = SituationClassifier._verified_count_provider
    assert provider is not None
    assert provider() == 23


def test_budget_easy_low_reads() -> None:
    policy = AdaptiveBudgetPolicy()
    assert policy.allocate(confidence=0.95, category="s2p", verified_count=50) == policy.min_reads


def test_budget_hard_high_reads() -> None:
    policy = AdaptiveBudgetPolicy()
    assert policy.allocate(confidence=0.20, category="s2p", verified_count=50) == policy.max_reads


def test_classifier_budget_uses_action_confidence(monkeypatch) -> None:
    captured: list[float] = []

    class CapturePolicy:
        def allocate(self, confidence: float, category: str, verified_count: int) -> int:
            captured.append(confidence)
            assert category == "s2p"
            assert verified_count == 50
            return 1

    monkeypatch.setattr(SituationClassifier, "_budget_policy", CapturePolicy())
    monkeypatch.setattr(SituationClassifier, "_verified_count_provider", staticmethod(lambda: 50))
    classifier = SituationClassifier()
    classifier.classify(
        [0.1] * 8,
        [[0.1] * 8, [0.9] * 8],
        [0.2] * 8,
        [0.91, 0.04, 0.03, 0.01, 0.01],
        [0.5, 0.5],
    )
    assert captured == [0.91]


def test_infinity_rejected() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        content=b'{"invoice_amount": Infinity, "confidence": 0.9}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_nan_rejected() -> None:
    response = client.post(
        "/api/s2p/confidence/route",
        content=b'{"invoice_amount": NaN, "confidence": 0.9}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


def test_twin_get_no_events(monkeypatch) -> None:
    manager = app.state.s2p_autonomy
    assert isinstance(manager, S2PAutonomyManager)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(manager, "_record_event", lambda *args: events.append(args))
    response = client.get("/api/s2p/learning/frozen-twin")
    assert response.status_code == 200
    assert events == []


def test_twin_fallback_false(monkeypatch) -> None:
    monkeypatch.setattr(app.state, "s2p_autonomy", None)
    response = client.get("/api/s2p/learning/frozen-twin")
    assert response.status_code == 200
    assert response.json()["frozen_available"] is False


def test_finite_amount_accepted() -> None:
    response = client.post("/api/s2p/confidence/route", json={"invoice_amount": 200.0, "confidence": 0.75})
    assert response.status_code == 200
    assert response.json()["routing"] == "auto_approve"


def test_conservation_provider_shape() -> None:
    payload = main._s2p_conservation_state()
    assert isinstance(payload["verified_outcomes"], list)
    assert isinstance(payload["recent_outcomes"], list)


def test_twin_drift_has_panel_accuracy_fields() -> None:
    response = client.get("/api/s2p/twin/drift")
    assert response.status_code in {200, 409}
    if response.status_code == 200:
        assert {"current_accuracy", "frozen_accuracy", "delta_accuracy"}.issubset(response.json())
