from __future__ import annotations

from app.domains.s2p.config import S2PDomainConfig
from app.services.s2p_evidence_templates import (
    EvidenceTemplate,
    S2PEvidenceEngine,
    S2P_FACTOR_MAP,
)


FULL_CONTEXT = {
    "invoice_id": "INV-1",
    "supplier": "Aster",
    "commodity": "metals",
    "variance_pct": 12.5,
    "commodity_delta": 3.0,
    "lookback": 30,
    "ref": "CTR-1",
    "allows_blocks": "allows",
    "threshold": 20.0,
    "within_exceeds": "within",
    "inv_qty": 100,
    "po_qty": 95,
    "gr_qty": 95,
    "delta": 5,
    "match_status": "mismatch requires review",
    "match_id": "INV-0",
    "match_date": "2026-01-01",
    "match_amt": 1200.0,
    "similarity": 82.1,
    "verdict": "possible duplicate",
    "po_id": "PO-1",
    "scope": "metals",
    "covered_pct": 72.0,
    "gap_items": "line-item coverage",
    "n_rules": 2,
    "issues": "tax completeness",
    "compliance_pct": 91.5,
}


def _render(category: str):
    return S2PEvidenceEngine().render(category, FULL_CONTEXT, "hold_for_review", 0.873)


def test_render_price_variance_full_context() -> None:
    rendered = _render("price_variance")

    assert "12.5% price delta" in rendered.text
    assert rendered.confidence == 0.873


def test_render_quantity_mismatch_full_context() -> None:
    rendered = _render("quantity_mismatch")

    assert "Invoice qty 100" in rendered.text
    assert "Delta 5" in rendered.text


def test_render_duplicate_risk_full_context() -> None:
    rendered = _render("duplicate_risk")

    assert "Similar candidate INV-0" in rendered.text
    assert "82.1%" in rendered.text


def test_render_contract_gap_full_context() -> None:
    rendered = _render("contract_gap")

    assert "Contract CTR-1" in rendered.text
    assert "coverage 72.0%" in rendered.text


def test_render_format_compliance_full_context() -> None:
    rendered = _render("format_compliance")

    assert "fails 2 format rules" in rendered.text
    assert "91.5%" in rendered.text


def test_missing_context_safe() -> None:
    rendered = S2PEvidenceEngine().render("price_variance", {"invoice_id": "INV-1"}, "", 0.0)

    assert rendered.text
    assert "unknown" in rendered.text
    assert "commodity" in rendered.missing_fields


def test_all_missing_context_safe() -> None:
    rendered = S2PEvidenceEngine().render("quantity_mismatch", {}, "", 0.0)

    assert rendered.text
    assert rendered.confidence == 0.0


def test_unknown_category_safe_generic_result() -> None:
    rendered = S2PEvidenceEngine().render("new_category", {}, "review", 0.5)

    assert rendered.category == "new_category"
    assert "requires review" in rendered.text
    assert rendered.factors_used == []


def test_render_from_decision_works() -> None:
    rendered = S2PEvidenceEngine().render_from_decision(
        {
            "decision_id": "D-1",
            "category": "duplicate_risk",
            "recommended_action": "flag_leakage",
            "confidence": 0.7,
            "metadata": {"invoice_id": "INV-1", "supplier_name": "Aster"},
            "factors": {name: 0.1 for name in S2PDomainConfig.factors},
        }
    )

    assert rendered.category == "duplicate_risk"
    assert "INV-1" in rendered.text


def test_available_categories_are_canonical() -> None:
    assert S2PEvidenceEngine().available_categories() == [
        "price_variance",
        "quantity_mismatch",
        "duplicate_risk",
        "contract_gap",
        "format_compliance",
    ]


def test_confidence_formatting() -> None:
    rendered = _render("price_variance")

    assert "Confidence: 87%" in rendered.text
    assert rendered.to_dict()["confidence_pct"] == "87%"


def test_custom_template_override() -> None:
    engine = S2PEvidenceEngine(
        {"price_variance": EvidenceTemplate("price_variance", "Custom {invoice_id}", ["invoice_id"], [])}
    )

    rendered = engine.render("price_variance", {"invoice_id": "INV-2"}, "hold", 0.1)

    assert rendered.text == "Custom INV-2"


def test_factors_used_are_actual_s2p_factors() -> None:
    allowed = set(S2PDomainConfig.factors)

    for factors in S2P_FACTOR_MAP.values():
        assert set(factors).issubset(allowed)


def test_formatting_error_does_not_crash() -> None:
    engine = S2PEvidenceEngine(
        {"price_variance": EvidenceTemplate("price_variance", "Bad {amount:.2f}", ["amount"], [])}
    )

    rendered = engine.render("price_variance", {"amount": "not-number"}, "hold", 0.1)

    assert rendered.text == "Bad 0.00"
