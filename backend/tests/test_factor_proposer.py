from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app
from app.routers import factor_proposer_router
from app.services.factor_proposer import FactorProposer


client = TestClient(app)


WEIGHTS = {
    "match_status": 0.22,
    "amount_variance_ratio": 0.18,
    "duplicate_score": 0.16,
    "supplier_exception_history": 0.10,
    "payment_terms_impact": 0.09,
    "commodity_index_correlation": 0.08,
    "tax_regulatory_compliance": 0.04,
}

STATS = {
    "match_status": {"variance": 0.85, "outcome_corr": 0.46},
    "amount_variance_ratio": {"variance": 0.75, "outcome_corr": 0.38},
    "duplicate_score": {"variance": 0.68, "outcome_corr": 0.34},
    "supplier_exception_history": {"variance": 0.52, "outcome_corr": 0.18},
    "payment_terms_impact": {"variance": 0.44, "outcome_corr": 0.12},
    "commodity_index_correlation": {"variance": 0.36, "outcome_corr": 0.08},
    "tax_regulatory_compliance": {"variance": 0.25, "outcome_corr": 0.03},
}


def test_analyze_returns_all_factors() -> None:
    rows = FactorProposer(WEIGHTS, STATS).analyze()

    assert len(rows) == len(WEIGHTS)


def test_analyze_sorted_by_contribution() -> None:
    rows = FactorProposer(WEIGHTS, STATS).analyze()
    values = [row.signal_contribution_pct for row in rows]

    assert values == sorted(values, reverse=True)


def test_low_weight_low_corr_flagged() -> None:
    rows = FactorProposer(WEIGHTS, STATS).analyze()
    target = next(row for row in rows if row.factor_name == "tax_regulatory_compliance")

    assert target.verdict == "replace_candidate"


def test_high_weight_kept() -> None:
    rows = FactorProposer(WEIGHTS, STATS).analyze()
    target = next(row for row in rows if row.factor_name == "match_status")

    assert target.verdict == "keep"


def test_medium_weight_review() -> None:
    rows = FactorProposer(WEIGHTS, STATS).analyze()
    target = next(row for row in rows if row.factor_name == "payment_terms_impact")

    assert target.verdict == "review"


def test_propose_returns_recommendation() -> None:
    proposal = FactorProposer(WEIGHTS, STATS).propose_replacement("tax_regulatory_compliance", [])

    assert proposal["replacement"] == "tariff_exposure"
    assert proposal["estimated_pp"] > 0


def test_propose_unknown_factor() -> None:
    try:
        FactorProposer(WEIGHTS, STATS).propose_replacement("missing", [])
    except KeyError:
        assert True
    else:
        raise AssertionError("expected KeyError")


def test_contribution_pct_sums_to_100() -> None:
    total = sum(row.signal_contribution_pct for row in FactorProposer(WEIGHTS, STATS).analyze())

    assert abs(total - 100.0) < 0.01


def test_empty_weights() -> None:
    assert FactorProposer({}, {}).analyze() == []


def test_router_analysis() -> None:
    payload = client.get("/api/s2p/factors/analysis").json()

    assert payload["count"] > 0
    assert isinstance(payload["factors"], list)


def test_router_recommendations() -> None:
    payload = client.get("/api/s2p/factors/recommendations").json()

    assert all(row["verdict"] == "replace_candidate" for row in payload["recommendations"])


def test_router_propose() -> None:
    response = client.post(
        "/api/s2p/factors/propose",
        json={"factor": "tax_regulatory_compliance", "candidates": ["tariff_exposure"]},
    )

    assert response.status_code == 200
    assert response.json()["replacement"] == "tariff_exposure"


def test_proposer_uses_live_dk_weights(monkeypatch) -> None:
    live_weights = [0.04, 0.22, 0.18, 0.16, 0.10, 0.09, 0.08]
    monkeypatch.setattr(factor_proposer_router, "_read_dk_weights", lambda _scorer: live_weights)

    payload = client.get("/api/s2p/factors/analysis").json()
    by_factor = {row["factor_name"]: row for row in payload["factors"]}

    assert by_factor["match_status"]["current_dk_weight"] == live_weights[0]
    assert by_factor["amount_variance_ratio"]["current_dk_weight"] == live_weights[1]
