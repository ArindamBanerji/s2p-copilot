#!/usr/bin/env python3
"""
S2P Copilot -- Procurement Approval Worked Example
Demonstrates same GAE engine as SOC Copilot, different domain.

Actual API: POST /api/s2p/score
  Required: event_id, category, amount, supplier_id, supplier_risk_rating, ...
  Actions:  approve | escalate | reject | review

scenario factors are mapped to S2P API fields (see _to_api_payload).
"""
import json
import sys
from pathlib import Path

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

BACKEND_URL = "http://127.0.0.1:8000"
SCENARIOS_FILE = Path(__file__).parent / "scenarios.json"

# S2P actions that map to "defer" in scenario ground truth
DEFER_ACTIONS = {"escalate", "review"}

# action_map: S2P action → scenario expected action space
ACTION_MAP = {
    "approve":  "approve",
    "escalate": "defer",
    "reject":   "reject",
    "review":   "defer",
}


def _to_api_payload(s: dict) -> dict:
    """
    Map 8-factor scenario to actual POST /api/s2p/score request.

    Mapping rationale:
      supplier_risk_rating  <- financial_health (higher=less risky)
      category              <- dominant risk signal
      vendor_decisions      <- 100 if track_record>0.5 else 20
      vendor_approvals      <- decisions * track_record
      approved_categories   <- [category] when process_adherence >= 0.7
      historical_spend_mean <- 90% of contract_value
      historical_spend_std  <- 10% of contract_value
    """
    f = s["factors"]
    amount = float(s["contract_value_usd"])

    # Category: pick dominant risk signal
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

    # Vendor trust from track_record
    track = f["track_record"]
    vendor_decisions = 100 if track > 0.50 else 20
    vendor_approvals = int(vendor_decisions * track)

    # Contract: approved if process_adherence looks compliant
    contract_id = f"{s['scenario_id']}-C" if f["process_adherence"] >= 0.70 else None
    approved_categories = [category] if contract_id else []

    return {
        "event_id":               s["scenario_id"],
        "category":               category,
        "amount":                 amount,
        "supplier_id":            f"SUP-{s['scenario_id']}",
        "supplier_risk_rating":   f["financial_health"],
        "contract_id":            contract_id,
        "approved_categories":    approved_categories,
        "historical_spend_mean":  amount * 0.90,
        "historical_spend_std":   max(amount * 0.10, 1.0),
        "vendor_decisions":       vendor_decisions,
        "vendor_approvals":       vendor_approvals,
    }


def run_scenarios():
    scenarios = json.loads(SCENARIOS_FILE.read_text())

    print("\n" + "=" * 70)
    print("  S2P Copilot -- Procurement Approval Worked Example")
    print("  Same GAE engine . Different domain . One conservation law")
    print("=" * 70)
    print(f"\n{'ID':<8} {'Supplier':<20} {'Expected':<10} "
          f"{'Got':<10} {'Conf':>6} {'OK':>4}")
    print("-" * 70)

    correct = 0
    total = len(scenarios)

    if not _HAS_HTTPX:
        print("  [ERROR] httpx not installed. Run: pip install httpx")
        return 0, total

    with httpx.Client(timeout=30.0) as client:
        for s in scenarios:
            try:
                payload = _to_api_payload(s)
                resp = client.post(f"{BACKEND_URL}/api/s2p/score", json=payload)
                resp.raise_for_status()
                data = resp.json()

                action = data.get("action", "unknown")
                confidence = data.get("confidence", 0.0)
                mapped = ACTION_MAP.get(action, action)
                is_correct = mapped == s["expected_action"]
                if is_correct:
                    correct += 1

                status = "OK" if is_correct else "XX"
                print(f"{s['scenario_id']:<8} {s['supplier'][:18]:<20} "
                      f"{s['expected_action']:<10} {action:<10} "
                      f"{confidence:>5.1%} {status:>4}")

            except Exception as e:
                print(f"{s['scenario_id']:<8} {s['supplier'][:18]:<20} "
                      f"ERROR: {e}")

    print("-" * 70)
    print(f"\n  Results: {correct}/{total} correct "
          f"({correct/total:.0%} accuracy)")
    print(f"  Baseline (random 4 actions): {total//4}/{total} "
          f"({1/4:.0%})")
    print(f"\n  The same ProfileScorer, conservation law, and IKS")
    print(f"  that power SOC alert triage power this decision.")
    print("=" * 70 + "\n")

    return correct, total


if __name__ == "__main__":
    correct, total = run_scenarios()
    sys.exit(0 if correct >= 3 else 1)
