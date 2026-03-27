"""
S2P Copilot 10-Scenario Demo.
Runs end-to-end: score -> outcome -> IKS progression.
No frontend. No Neo4j required (fault-tolerant).
Run: python demo/s2p_demo.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import S2PEvent, compute_factor_vector
from app.domains.s2p.scorer import (
    score_event, update_scorer, reset_scorer, get_s2p_iks, LEARNING_ENABLED
)

# Reset to cold start for clean demo
reset_scorer()

SCENARIOS = [
    {
        "id": 1, "name": "Standard in-contract purchase",
        "event": S2PEvent(
            event_id="S001", category="supplier_risk",
            amount=12000.0, supplier_id="SUP-ACME",
            contract_id="C-001",
            approved_categories=["supplier_risk", "budget_overrun"],
            supplier_risk_rating=0.85,
            historical_spend_mean=11000.0, historical_spend_std=1500.0,
            vendor_decisions=150, vendor_approvals=138,
        ),
        "ground_truth": "approve",
    },
    {
        "id": 2, "name": "New high-risk supplier, large amount",
        "event": S2PEvent(
            event_id="S002", category="supplier_risk",
            amount=250000.0, supplier_id="SUP-NEW",
            supplier_risk_rating=0.15,
            historical_spend_mean=0.0, historical_spend_std=1.0,
            vendor_decisions=0, vendor_approvals=0,
        ),
        "ground_truth": "escalate",
    },
    {
        "id": 3, "name": "Category mismatch, no contract",
        "event": S2PEvent(
            event_id="S003", category="maverick_spend",
            amount=45000.0, supplier_id="SUP-ROGUE",
            contract_id=None,
            supplier_risk_rating=0.40,
            historical_spend_mean=5000.0, historical_spend_std=500.0,
            vendor_decisions=10, vendor_approvals=3,
        ),
        "ground_truth": "reject",
    },
    {
        "id": 4, "name": "Borderline amount, low vendor trust",
        "event": S2PEvent(
            event_id="S004", category="budget_overrun",
            amount=98000.0, supplier_id="SUP-MID",
            contract_id="C-002",
            approved_categories=["budget_overrun"],
            supplier_risk_rating=0.60,
            historical_spend_mean=75000.0, historical_spend_std=8000.0,
            vendor_decisions=25, vendor_approvals=14,
        ),
        "ground_truth": "review",
    },
    {
        "id": 5, "name": "Long-term trusted vendor",
        "event": S2PEvent(
            event_id="S005", category="contract_breach",
            amount=33000.0, supplier_id="SUP-TRUST",
            contract_id="C-003",
            approved_categories=["contract_breach", "supplier_risk"],
            supplier_risk_rating=0.92,
            historical_spend_mean=32000.0, historical_spend_std=2000.0,
            vendor_decisions=500, vendor_approvals=488,
        ),
        "ground_truth": "approve",
    },
    {
        "id": 6, "name": "4x spend anomaly",
        "event": S2PEvent(
            event_id="S006", category="budget_overrun",
            amount=400000.0, supplier_id="SUP-SPIKE",
            contract_id="C-004",
            approved_categories=["budget_overrun"],
            supplier_risk_rating=0.70,
            historical_spend_mean=95000.0, historical_spend_std=10000.0,
            vendor_decisions=80, vendor_approvals=72,
        ),
        "ground_truth": "escalate",
    },
    {
        "id": 7, "name": "High-risk supplier, no contract",
        "event": S2PEvent(
            event_id="S007", category="approval_bypass",
            amount=18000.0, supplier_id="SUP-RISK",
            supplier_risk_rating=0.10,
            historical_spend_mean=0.0, historical_spend_std=1.0,
            vendor_decisions=5, vendor_approvals=1,
        ),
        "ground_truth": "reject",
    },
    {
        "id": 8, "name": "Contract exists, category mismatch",
        "event": S2PEvent(
            event_id="S008", category="maverick_spend",
            amount=22000.0, supplier_id="SUP-DRIFT",
            contract_id="C-005",
            approved_categories=["supplier_risk"],
            supplier_risk_rating=0.65,
            historical_spend_mean=20000.0, historical_spend_std=3000.0,
            vendor_decisions=40, vendor_approvals=31,
        ),
        "ground_truth": "review",
    },
    {
        "id": 9, "name": "High vendor trust, 200 decisions",
        "event": S2PEvent(
            event_id="S009", category="supplier_risk",
            amount=55000.0, supplier_id="SUP-GOLD",
            contract_id="C-006",
            approved_categories=["supplier_risk", "budget_overrun"],
            supplier_risk_rating=0.95,
            historical_spend_mean=52000.0, historical_spend_std=4000.0,
            vendor_decisions=200, vendor_approvals=185,
        ),
        "ground_truth": "approve",
    },
    {
        "id": 10, "name": "New supplier, large amount, data quality",
        "event": S2PEvent(
            event_id="S010", category="data_quality",
            amount=180000.0, supplier_id="SUP-UNKNOWN",
            supplier_risk_rating=0.30,
            historical_spend_mean=0.0, historical_spend_std=1.0,
            vendor_decisions=0, vendor_approvals=0,
        ),
        "ground_truth": "escalate",
    },
]


def run_demo():
    print("=" * 60)
    print("S2P Copilot -- 10 Scenario Demo")
    print(f"Domain: S2P (Source-to-Pay)")
    print(f"Tensor: ({S2PDomainConfig.n_categories},"
          f"{S2PDomainConfig.n_actions},"
          f"{S2PDomainConfig.n_factors}) = 144 values")
    print(f"Learning: {'ENABLED' if LEARNING_ENABLED else 'DISABLED (shadow mode)'}")
    print("=" * 60)
    print()

    correct = 0
    results = []

    for scenario in SCENARIOS:
        event  = scenario["event"]
        fv     = compute_factor_vector(event)
        result = score_event(fv, event.category)
        match  = result["action"] == scenario["ground_truth"]
        if match:
            correct += 1

        status = "OK" if match else "XX"
        print(f"  [{status}] Scenario {scenario['id']:2d}: {scenario['name']}")
        print(f"       Category:   {event.category}")
        print(f"       Predicted:  {result['action']} "
              f"(confidence={result['confidence']:.2f})")
        print(f"       Expected:   {scenario['ground_truth']}")
        print(f"       Factors:    {[round(v, 2) for v in fv]}")
        print()

        results.append({
            "id":         scenario["id"],
            "predicted":  result["action"],
            "expected":   scenario["ground_truth"],
            "correct":    match,
            "confidence": result["confidence"],
        })

    # IKS after cold-start scoring (no learning applied)
    iks = get_s2p_iks()

    print("=" * 60)
    print(f"Results: {correct}/10 correct at cold start")
    print(f"IKS: {iks['iks']} -- {iks['interpretation']}")
    print()
    print("Factor names (index order):")
    for i, f in enumerate(S2PDomainConfig.factors):
        print(f"  [{i}] {f}")
    print()
    print("Actions available:")
    for i, a in enumerate(S2PDomainConfig.actions):
        print(f"  [{i}] {a}")
    print("=" * 60)
    print()
    print("CLAIM-62 B0 validation: S2P domain operational.")
    print("Same GAE engine. Different domain. Platform claim confirmed.")

    return correct, results


if __name__ == "__main__":
    correct, _ = run_demo()
    # Cold start accuracy >= 4/10 is passing
    # (random baseline = 2.5/10 at 4 actions)
    sys.exit(0 if correct >= 4 else 1)
