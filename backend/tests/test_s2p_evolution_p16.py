from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_p16_rule_templates_no_centroid_symbols():
    source = (Path(__file__).resolve().parents[1] / "app" / "domains" / "s2p" / "evolution" / "rule_templates.py").read_text(
        encoding="utf-8"
    )

    forbidden = [
        "centroid",
        "ProfileScorer",
        "update_centroid",
        "gae_scorer",
        "save_centroids",
        "warm_start",
    ]
    found = [term for term in forbidden if term in source]

    assert found == []


def test_default_s2p_score_still_works():
    payload = {
        "event_id": "EVOLVE-SCORE-001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-001",
        "match_status": 0.92,
        "amount_variance_ratio": 0.08,
        "duplicate_score": 0.04,
        "supplier_exception_history": 0.05,
        "payment_terms_impact": 0.48,
        "commodity_index_correlation": 0.76,
        "tax_regulatory_compliance": 0.90,
    }

    response = TestClient(app).post("/api/s2p/score", json=payload)

    assert response.status_code == 200
    assert response.json()["category"] == "price_variance"
