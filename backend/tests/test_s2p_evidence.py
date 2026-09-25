from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from copilot_sdk.backend.investigation_router import create_investigation_router
from copilot_sdk.scoring.investigation import EvidenceProvider, VLDInvestigator
from copilot_sdk.scoring.situation_classifier import SituationClassifier

from app.domains.s2p.config import S2PDomainConfig
from app.evidence_provider import S2PEvidenceProvider
from app.main import app
from app.vld_preseed import seed_vld_s2p_showcase, showcase_invoice


def test_evidence_provider_protocol(tmp_path) -> None:
    provider = S2PEvidenceProvider(seed_vld_s2p_showcase(tmp_path), "VLD-S2P-1")
    assert isinstance(provider, EvidenceProvider)


def test_evidence_supplier_trust_with_history(tmp_path) -> None:
    provider = S2PEvidenceProvider(seed_vld_s2p_showcase(tmp_path), "VLD-S2P-1")
    evidence = provider.read_evidence("VLD-S2P-1", 3, "supplier_exception_history")
    assert evidence is not None
    assert evidence["source"] == "supplier_history"
    assert evidence["trust_score"] > 0.8
    assert evidence["partial_delivery_pricing_error_ratio"] == 3.1


def test_evidence_contract_coverage(tmp_path) -> None:
    provider = S2PEvidenceProvider(seed_vld_s2p_showcase(tmp_path), "VLD-S2P-1")
    evidence = provider.read_evidence("VLD-S2P-1", 0, "match_status")
    assert evidence is not None
    assert evidence["source"] == "contract_db"
    assert evidence["coverage_score"] >= 0.9


def test_evidence_empty_branch(tmp_path) -> None:
    provider = S2PEvidenceProvider(seed_vld_s2p_showcase(tmp_path), "missing")
    assert provider.read_evidence("missing", 99, "supplier_exception_history") is None
    assert provider.read_evidence("VLD-S2P-1", 99, "unknown_factor") is None


def test_evidence_all_eight_factors(tmp_path) -> None:
    provider = S2PEvidenceProvider(seed_vld_s2p_showcase(tmp_path), "VLD-S2P-2")
    missing = [
        factor
        for index, factor in enumerate(S2PDomainConfig.factors)
        if provider.read_evidence("VLD-S2P-2", index, factor) is None
    ]
    assert missing == []


def test_preseed_creates_three_invoices(tmp_path) -> None:
    payload = seed_vld_s2p_showcase(tmp_path)
    assert len(payload["invoices"]) == 3
    assert {row["invoice_id"] for row in payload["invoices"]} == {"VLD-S2P-1", "VLD-S2P-2", "VLD-S2P-S1"}


def test_preseed_idempotent(tmp_path) -> None:
    first = seed_vld_s2p_showcase(tmp_path)
    second = seed_vld_s2p_showcase(tmp_path)
    assert len(first["invoices"]) == len(second["invoices"]) == 3


def test_vld_s2p1_supplier_it_knew(tmp_path) -> None:
    payload = seed_vld_s2p_showcase(tmp_path)
    invoice = showcase_invoice("VLD-S2P-1")
    trace = _investigate(
        invoice,
        payload,
        k_weights=[5.0, 0.1, 0.1, 8.0, 0.1, 0.1, 0.1, 0.1],
        budget=2,
    )
    assert _action(trace.surface_action) == "hold_for_review"
    assert _action(trace.final_action) == "auto_approve"
    assert {step.evidence_source for step in trace.steps} == {"contract_db", "supplier_history"}


def test_vld_s2p2_price_spike_context(tmp_path) -> None:
    # K-dependent PLANTED FIXTURE: reachable learned K reads contract coverage
    # then volume context and flips S2P-2. The SDK integration harness does not
    # inject K, so it stays flag_leakage; that is the before-learning baseline.
    payload = seed_vld_s2p_showcase(tmp_path)
    invoice = showcase_invoice("VLD-S2P-2")
    trace = _investigate_export_geometry(
        invoice,
        payload,
        k_weights=[0.5, 0.5, 0.5, 0.5, 0.5, 1.0, 0.5, 0.5],
        budget=2,
    )
    assert _action(trace.surface_action) == "flag_leakage"
    assert _action(trace.final_action) == "auto_approve"
    assert [step.factor_name for step in trace.steps] == ["match_status", "commodity_index_correlation"]


def test_s2p2_unit_k_no_flip(tmp_path) -> None:
    payload = seed_vld_s2p_showcase(tmp_path)
    invoice = showcase_invoice("VLD-S2P-2")
    trace = _investigate_export_geometry(
        invoice,
        payload,
        k_weights=[0.5] * S2PDomainConfig.n_factors,
        budget=2,
    )
    assert _action(trace.surface_action) == "flag_leakage"
    assert _action(trace.final_action) == "flag_leakage"
    assert [step.factor_name for step in trace.steps] == ["match_status", "amount_variance_ratio"]


def test_s2p2_threshold_k(tmp_path) -> None:
    payload = seed_vld_s2p_showcase(tmp_path)
    invoice = showcase_invoice("VLD-S2P-2")
    at_threshold = [0.1] * S2PDomainConfig.n_factors
    at_threshold[0] = 3.0
    at_threshold[5] = 0.124875
    below_trace = _investigate_export_geometry(invoice, payload, k_weights=at_threshold, budget=2)
    assert _action(below_trace.final_action) == "flag_leakage"
    assert [step.factor_name for step in below_trace.steps] == ["match_status", "amount_variance_ratio"]

    above_threshold = list(at_threshold)
    above_threshold[5] = 0.13
    above_trace = _investigate_export_geometry(invoice, payload, k_weights=above_threshold, budget=2)
    assert _action(above_trace.final_action) == "auto_approve"
    assert [step.factor_name for step in above_trace.steps] == ["match_status", "commodity_index_correlation"]


def test_s1_conservation_skip() -> None:
    invoice = showcase_invoice("VLD-S2P-S1")
    vector = _vector(invoice)
    mu = S2PDomainConfig.get_profile_centroids()[S2PDomainConfig.get_category_index(invoice["category"])]
    classifier = SituationClassifier()
    investigator = VLDInvestigator(mu, np.ones(S2PDomainConfig.n_factors), list(S2PDomainConfig.factors), tau=S2PDomainConfig.tau)
    _, p_surface = investigator.score(vector)
    q_surface = investigator.compute_Q(vector, p_surface)
    assessment = classifier.classify(vector, mu, np.ones(S2PDomainConfig.n_factors), p_surface, q_surface, default_budget=2)
    assert assessment.situation == "S1"
    assert assessment.recommended_budget == 0


def test_investigation_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/investigation/health")
    assert response.status_code == 200
    assert response.json()["investigation_available"] is True


def test_investigation_endpoint(tmp_path) -> None:
    payload = seed_vld_s2p_showcase(tmp_path)
    test_app = _router_test_app(payload)
    invoice = showcase_invoice("VLD-S2P-1")
    body = {
        "decision_id": "VLD-S2P-1",
        "category": invoice["category"],
        "factor_vector": _vector(invoice).tolist(),
        "budget": 2,
        "use_K": False,
    }
    with TestClient(test_app) as client:
        response = client.post("/api/investigation/investigate", json=body)
    assert response.status_code == 200
    data = response.json()
    assert data["decision_id"] == "VLD-S2P-1"
    assert "steps" in data


def _investigate(invoice: dict, payload: dict, *, k_weights: list[float], budget: int):
    category_index = S2PDomainConfig.get_category_index(invoice["category"])
    mu = S2PDomainConfig.get_profile_centroids()[category_index]
    investigator = VLDInvestigator(mu, np.ones(S2PDomainConfig.n_factors), list(S2PDomainConfig.factors), tau=S2PDomainConfig.tau)
    provider = S2PEvidenceProvider(payload, invoice["invoice_id"])
    return investigator.investigate(
        invoice["invoice_id"],
        invoice["category"],
        _vector(invoice),
        provider,
        budget=budget,
        K_weights=np.asarray(k_weights, dtype=float),
    )


def _investigate_export_geometry(invoice: dict, payload: dict, *, k_weights: list[float], budget: int):
    export = _s2p_export()
    names = list(export["factor_names"])
    mu = np.asarray(export["all_category_mu"][invoice["category"]], dtype=float)
    sigma = np.asarray(export.get("sigma") or np.ones(len(names)), dtype=float)
    investigator = VLDInvestigator(mu, sigma, names, tau=float(export["tau"]))
    provider = S2PEvidenceProvider(payload, invoice["invoice_id"])
    vector = np.asarray([float(invoice["factors"][name]) for name in names], dtype=float)
    return investigator.investigate(
        invoice["invoice_id"],
        invoice["category"],
        vector,
        provider,
        budget=budget,
        K_weights=np.asarray(k_weights, dtype=float),
    )


def _s2p_export() -> dict:
    sdk = Path(__file__).resolve().parents[3] / "copilot-sdk"
    with (sdk / "real_centroids_v1.json").open(encoding="utf-8") as handle:
        return json.load(handle)["copilots"]["s2p"]


def _router_test_app(payload: dict):
    from fastapi import FastAPI

    test_app = FastAPI()

    class Scorer:
        centroids = S2PDomainConfig.get_profile_centroids()
        categories = S2PDomainConfig.categories
        actions = S2PDomainConfig.actions
        factors = S2PDomainConfig.factors
        tau = S2PDomainConfig.tau

    test_app.include_router(
        create_investigation_router(
            scorer_provider=lambda: Scorer(),
            evidence_provider_factory=lambda decision_id: S2PEvidenceProvider(payload, decision_id),
            factor_names=list(S2PDomainConfig.factors),
        )
    )
    return test_app


def _vector(invoice: dict) -> np.ndarray:
    return np.asarray([float(invoice["factors"][name]) for name in S2PDomainConfig.factors], dtype=float)


def _action(index: int) -> str:
    return S2PDomainConfig.actions[int(index)]
