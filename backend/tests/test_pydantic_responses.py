import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer
from app.models.outcome_receipt import OutcomeReceipt
from app.models.responses import FlexibleResponse, S2PScoreResponse
from app.routers import s2p as s2p_router
from app.services.receipt_store import get_receipt_store


client = TestClient(app)

SCORE_BODY = {
    "event_id": "PYDANTIC-S2P-INV-001",
    "category": "price_variance",
    "amount": 12500.0,
    "supplier_id": "SUP-001",
    "match_status": 0.2,
    "amount_variance_ratio": 0.35,
    "duplicate_score": 0.1,
    "supplier_exception_history": 0.3,
    "payment_terms_impact": 0.2,
    "commodity_index_correlation": 0.4,
    "tax_regulatory_compliance": 0.1,
}


def _reset_app_state() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    s2p_router._clear_score_conservation_status_cache()


def test_flexible_response_preserves_extra_fields():
    model = FlexibleResponse(required=False, extra_metric=42, nested={"ok": True})

    dumped = model.model_dump()
    assert dumped["required"] is False
    assert dumped["extra_metric"] == 42
    assert dumped["nested"] == {"ok": True}


def test_all_s2p_router_routes_have_response_models():
    missing = []
    for route in app.routes:
        module = getattr(route.endpoint, "__module__", "")
        if module.startswith("app.routers.s2p"):
            path = getattr(route, "path", "")
            if path and path != "/health" and getattr(route, "response_model", None) is None:
                missing.append((path, getattr(route.endpoint, "__name__", "")))

    assert missing == []


def test_score_response_model_preserves_existing_fields():
    response = client.post("/api/s2p/score", json=SCORE_BODY)
    assert response.status_code == 200

    data = response.json()
    parsed = S2PScoreResponse.model_validate(data)
    assert parsed.event_id == SCORE_BODY["event_id"]
    assert parsed.category == SCORE_BODY["category"]
    assert "novelty_score" in data
    assert "process_context" in data
    assert "active_variant" in data
    assert "auto_approve" in data


def test_modeled_s2p_endpoints_return_json_success():
    _reset_app_state()

    score_response = client.post("/api/s2p/score", json={**SCORE_BODY, "event_id": "PYDANTIC-S2P-INV-002"})
    assert score_response.status_code == 200
    score = score_response.json()

    outcome_response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )
    assert outcome_response.status_code == 200

    learn_score_response = client.post(
        "/api/s2p/score",
        json={**SCORE_BODY, "event_id": "PYDANTIC-S2P-INV-LEARN"},
    )
    assert learn_score_response.status_code == 200
    learn_score = learn_score_response.json()

    learn_response = client.post(
        "/api/learn",
        json={
            "decision_id": learn_score["decision_id"],
            "actual_action": learn_score["action"],
            "outcome": "confirmed",
        },
    )
    assert learn_response.status_code == 200

    reset_response = client.post("/api/s2p/evolution/reset")
    assert reset_response.status_code == 200
    receipt_invoice_id = "PYDANTIC-RECEIPT-INVOICE"
    get_receipt_store().add(
        OutcomeReceipt(
            receipt_id="PYDANTIC-RECEIPT-001",
            invoice_id=receipt_invoice_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scored_action=score["action"],
            confidence=score["confidence"],
            factor_vector=score["factor_vector"],
            category=score["category"],
            human_action=score["action"],
        )
    )

    get_cases = [
        ("/api/s2p/auto-approve/stats", {}),
        ("/api/s2p/auto-approve/expansion-proof", {"category": "price_variance"}),
        ("/api/s2p/iks", {}),
        ("/api/s2p/learning-gate", {}),
        ("/api/s2p/control-tower/intents", {}),
        ("/api/s2p/control-tower/classify", {"invoice_id": "S2P-INV-0001"}),
        ("/api/s2p/control-tower/queue", {"limit": "2"}),
        ("/api/s2p/discovery/alerts", {}),
        ("/api/s2p/discovery/disruptions", {}),
        ("/api/s2p/discovery/extended", {}),
        ("/api/s2p/discovery/supplier/SUP-YANGTZE", {}),
        ("/api/s2p/discovery/propagation/DISC-EXT-001", {}),
        ("/api/s2p/simulation/scenarios", {}),
        ("/api/s2p/simulation/scenarios/SIM-001", {}),
        ("/api/s2p/simulation/what-if/SIM-001", {}),
        ("/api/s2p/simulation/impact-summary", {}),
        ("/api/s2p/insight/fingerprint", {"invoice_id": "S2P-INV-0001"}),
        ("/api/s2p/insight/similar", {"invoice_id": "S2P-INV-0001"}),
        ("/api/s2p/insight/cross-graph", {}),
        ("/api/s2p/insight/process-signals", {"supplier_id": "SUP-001"}),
        ("/api/s2p/evidence/audit-trail/S2P-INV-0001", {}),
        ("/api/s2p/evidence/receipts", {}),
        (f"/api/s2p/evidence/receipts/{receipt_invoice_id}", {}),
        ("/api/s2p/evidence/chain-integrity", {}),
        ("/api/s2p/evidence/audit-pack", {}),
        ("/api/s2p/evidence/template", {"invoice_id": "S2P-INV-0001", "category": "price_variance"}),
        ("/api/s2p/evidence/rules", {}),
        ("/api/s2p/evidence/compliance", {}),
        ("/api/s2p/governance/compliance-screening", {}),
        ("/api/s2p/governance/compliance-gaps", {}),
        ("/api/s2p/governance/conservation-proof", {}),
        ("/api/s2p/governance/sox-readiness", {}),
        ("/api/s2p/governance/rationalization", {}),
        ("/api/s2p/governance/rationalization/overlap", {}),
        ("/api/s2p/governance/rationalization/supplier/SUP-001", {}),
        ("/api/s2p/performance/trajectory", {}),
        ("/api/s2p/performance/what-if", {"additional_correct": "1", "additional_incorrect": "0"}),
        ("/api/s2p/performance/summary", {}),
        ("/api/s2p/financial-impact", {}),
        ("/api/s2p/pvg/variants", {}),
        ("/api/s2p/pvg/impact", {"period": "annual"}),
        ("/api/s2p/pvg/leakage", {}),
        ("/api/s2p/pvg/cycle-time", {}),
        ("/api/s2p/novelty/status", {}),
        ("/api/s2p/novelty/history", {"limit": "5"}),
        ("/api/s2p/novelty/rate", {}),
        ("/api/s2p/novelty/auto-pause", {}),
        ("/api/s2p/suppliers/clusters", {}),
        ("/api/s2p/suppliers/similarity", {"supplier_id": "SUP-001"}),
        ("/api/s2p/suppliers/early-warnings", {}),
        ("/api/s2p/suppliers/trends", {}),
        ("/api/s2p/suppliers/trend-signals", {"supplier_id": "SUP-001"}),
        ("/api/s2p/suppliers/payment-strategy", {}),
        ("/api/s2p/suppliers/payment-behavior", {"supplier_id": "SUP-001"}),
        ("/api/s2p/suppliers", {}),
        ("/api/s2p/suppliers/clustering", {}),
        ("/api/s2p/suppliers/declining", {}),
        ("/api/s2p/suppliers/heatmap", {}),
        ("/api/s2p/suppliers/correlations", {}),
        ("/api/s2p/suppliers/SUP-001/profile", {}),
        ("/api/s2p/suppliers/SUP-001/history", {}),
        ("/api/s2p/suppliers/SUP-001/heatmap", {}),
        ("/api/s2p/preview/queue", {}),
        ("/api/s2p/preview/conservation", {}),
        ("/api/s2p/preview/compounding", {}),
        ("/api/s2p/preview/suppliers", {}),
        ("/api/s2p/preview/config", {}),
        ("/api/s2p/evolution/rules", {}),
        ("/api/s2p/evolution/variants", {}),
        ("/api/s2p/evolution/promotion-check", {}),
        ("/api/s2p/evolution/shadow-results", {}),
        ("/api/s2p/evolution/promoted", {}),
        ("/api/s2p/explorer/export/centroids", {}),
        ("/api/s2p/explorer/export/csv", {}),
        ("/api/s2p/explorer/centroid/price_variance/auto_approve", {}),
        ("/api/s2p/explorer/drift/price_variance", {}),
        ("/api/s2p/explorer/dk-weights", {}),
        ("/api/s2p/explorer/contribution", {"invoice_id": score["event_id"]}),
    ]
    for path, params in get_cases:
        response = client.get(path, params=params)
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text[:200]}"
        assert isinstance(response.json(), dict), path


def test_openapi_contains_s2p_response_model_schemas():
    schema = client.get("/openapi.json").json()
    schemas = schema.get("components", {}).get("schemas", {})
    assert "S2PScoreResponse" in schemas
    assert "GenericResponse" in schemas

    score_response = schema["paths"]["/api/s2p/score"]["post"]["responses"]["200"]
    assert "S2PScoreResponse" in str(score_response)

    preview_response = schema["paths"]["/api/s2p/preview/queue"]["get"]["responses"]["200"]
    assert "GenericResponse" in str(preview_response)
