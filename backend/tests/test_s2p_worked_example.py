"""
tests/test_s2p_worked_example.py — Block 3.8 Procurement Approval worked example.

Actual API schema (POST /api/s2p/score):
  Request:  event_id, category, amount, supplier_id, supplier_risk_rating, ...
  Response: action, confidence, factor_vector, factor_names, probabilities, decision_id
  Actions:  approve | escalate | reject | review  (S2P domain)

scenarios.json uses a richer 8-factor format (financial_health, track_record, etc.)
that is mapped to the S2P API fields by _to_api_payload().

Run from backend/:
    pytest tests/test_s2p_worked_example.py -v
"""

import sys
import os
import json
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app
from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.scorer import reset_scorer

client = TestClient(app)

SCENARIOS_PATH = (
    Path(__file__).parent.parent.parent
    / "examples/procurement_approval/scenarios.json"
)

# S2P actions that map to "defer" in scenario ground truth
ACTION_MAP = {
    "approve":  "approve",
    "escalate": "defer",
    "reject":   "reject",
    "review":   "defer",
}

# Simple payloads for standalone tests (no scenarios.json needed)
_LOW_RISK_PAYLOAD = {
    "event_id": "PO-001",
    "category": "supplier_risk",
    "amount": 125000.0,
    "supplier_id": "SUP-PO-001",
    "supplier_risk_rating": 0.85,
    "contract_id": "PO-001-C",
    "approved_categories": ["supplier_risk"],
    "historical_spend_mean": 112500.0,
    "historical_spend_std": 12500.0,
    "vendor_decisions": 100,
    "vendor_approvals": 90,
}

_HIGH_RISK_PAYLOAD = {
    "event_id": "PO-008",
    "category": "supplier_risk",
    "amount": 1200000.0,
    "supplier_id": "SUP-PO-008",
    "supplier_risk_rating": 0.92,
    "contract_id": "PO-008-C",
    "approved_categories": ["supplier_risk"],
    "historical_spend_mean": 1080000.0,
    "historical_spend_std": 120000.0,
    "vendor_decisions": 100,
    "vendor_approvals": 97,
}

_COMPLIANCE_RISK_PAYLOAD = {
    "event_id": "PO-003",
    "category": "contract_breach",
    "amount": 2100000.0,
    "supplier_id": "SUP-PO-003",
    "supplier_risk_rating": 0.70,
    "historical_spend_mean": 1890000.0,
    "historical_spend_std": 210000.0,
    "vendor_decisions": 100,
    "vendor_approvals": 55,
}


def _to_api_payload(s: dict) -> dict:
    """Map 8-factor scenario to POST /api/s2p/score request."""
    f = s["factors"]
    amount = float(s["contract_value_usd"])

    if f["process_adherence"] < 0.50:
        category = "approval_bypass"
    elif f["compliance_score"] < 0.40 or f["geo_political_risk"] > 0.70:
        category = "contract_breach"
    elif f["concentration_risk"] > 0.80:
        category = "supplier_risk"
    elif f["financial_health"] < 0.40:
        category = "supplier_risk"
    else:
        category = "supplier_risk"

    track = f["track_record"]
    vendor_decisions = 100 if track > 0.50 else 20
    vendor_approvals = int(vendor_decisions * track)
    contract_id = f"{s['scenario_id']}-C" if f["process_adherence"] >= 0.70 else None

    return {
        "event_id":              s["scenario_id"],
        "category":              category,
        "amount":                amount,
        "supplier_id":           f"SUP-{s['scenario_id']}",
        "supplier_risk_rating":  f["financial_health"],
        "contract_id":           contract_id,
        "approved_categories":   [category] if contract_id else [],
        "historical_spend_mean": amount * 0.90,
        "historical_spend_std":  max(amount * 0.10, 1.0),
        "vendor_decisions":      vendor_decisions,
        "vendor_approvals":      vendor_approvals,
    }


def test_s2p_score_returns_recommendation():
    """POST /api/s2p/score returns a valid S2P action."""
    reset_scorer()
    resp = client.post("/api/s2p/score", json=_LOW_RISK_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert "action" in data
    assert data["action"] in S2PDomainConfig.actions


def test_s2p_score_confidence_in_range():
    """Confidence is between 0 and 1."""
    reset_scorer()
    resp = client.post("/api/s2p/score", json=_HIGH_RISK_PAYLOAD)
    assert resp.status_code == 200
    confidence = resp.json().get("confidence", -1)
    assert 0.0 <= confidence <= 1.0


def test_s2p_score_factor_breakdown_present():
    """Response includes factor_vector and factor_names."""
    reset_scorer()
    resp = client.post("/api/s2p/score", json=_COMPLIANCE_RISK_PAYLOAD)
    assert resp.status_code == 200
    data = resp.json()
    assert "factor_vector" in data
    assert "factor_names" in data
    assert len(data["factor_vector"]) == 6
    assert len(data["factor_names"]) == 6


def test_s2p_score_all_10_scenarios_return_valid_action():
    """All 10 procurement scenarios return a valid S2P action."""
    if not SCENARIOS_PATH.exists():
        pytest.skip("scenarios.json not found")

    reset_scorer()
    scenarios = json.loads(SCENARIOS_PATH.read_text())
    valid_actions = set(S2PDomainConfig.actions)

    for s in scenarios:
        resp = client.post("/api/s2p/score", json=_to_api_payload(s))
        assert resp.status_code == 200, f"{s['scenario_id']}: status {resp.status_code}"
        action = resp.json().get("action", "")
        assert action in valid_actions, \
            f"{s['scenario_id']}: invalid action '{action}'"
        assert "suppress" not in action
        assert "investigate" not in action


def test_s2p_score_above_random_baseline():
    """At least 3/10 scenarios correct — above 1/4 random baseline (4 actions)."""
    if not SCENARIOS_PATH.exists():
        pytest.skip("scenarios.json not found")

    reset_scorer()
    scenarios = json.loads(SCENARIOS_PATH.read_text())

    correct = 0
    for s in scenarios:
        resp = client.post("/api/s2p/score", json=_to_api_payload(s))
        if resp.status_code == 200:
            raw_action = resp.json().get("action", "")
            mapped = ACTION_MAP.get(raw_action, raw_action)
            if mapped == s["expected_action"]:
                correct += 1

    assert correct >= 3, \
        f"Only {correct}/10 correct — below random baseline of 3/10"
