r"""Generate deterministic S2P Phase 0 synthetic fixtures.

Run from the repository root:
    python generators\s2p_synthetic.py
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path


SEED = 20260511

CATEGORIES = [
    "price_variance",
    "quantity_mismatch",
    "duplicate_risk",
    "contract_gap",
    "format_compliance",
]

ACTIONS = [
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
]

FACTORS = [
    "match_status",
    "amount_variance_ratio",
    "duplicate_score",
    "supplier_exception_history",
    "payment_terms_impact",
    "commodity_index_correlation",
    "tax_regulatory_compliance",
]

ACTION_CENTROIDS = {
    "auto_approve": [0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95],
    "hold_for_review": [0.70, 0.30, 0.10, 0.15, 0.40, 0.50, 0.80],
    "escalate_to_buyer": [0.50, 0.60, 0.15, 0.30, 0.60, 0.30, 0.70],
    "flag_leakage": [0.80, 0.50, 0.10, 0.40, 0.70, 0.20, 0.60],
    "refer_to_specialist": [0.40, 0.40, 0.30, 0.50, 0.30, 0.40, 0.50],
}

CATEGORY_TWEAKS = {
    "price_variance": [0.00, 0.02, 0.00, 0.00, 0.02, 0.04, 0.00],
    "quantity_mismatch": [-0.02, 0.00, 0.01, 0.02, 0.00, 0.00, -0.01],
    "duplicate_risk": [-0.05, 0.00, 0.12, 0.04, -0.02, 0.00, -0.02],
    "contract_gap": [-0.03, 0.00, 0.00, 0.00, 0.10, -0.02, -0.04],
    "format_compliance": [-0.04, 0.00, 0.02, 0.04, -0.03, -0.01, -0.10],
}

ANCHOR_CENTROIDS = {
    ("price_variance", "auto_approve"): [0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95],
    ("price_variance", "flag_leakage"): [0.90, 0.40, 0.05, 0.25, 0.70, 0.15, 0.85],
    ("duplicate_risk", "flag_leakage"): [0.85, 0.10, 0.90, 0.40, 0.30, 0.50, 0.80],
    ("contract_gap", "escalate_to_buyer"): [0.70, 0.15, 0.05, 0.15, 0.85, 0.20, 0.60],
}

DISTRIBUTION = {
    "price_variance": [
        ("auto_approve", 10),
        ("hold_for_review", 3),
        ("flag_leakage", 2),
    ],
    "quantity_mismatch": [
        ("auto_approve", 5),
        ("hold_for_review", 3),
        ("escalate_to_buyer", 2),
    ],
    "duplicate_risk": [
        ("auto_approve", 2),
        ("hold_for_review", 3),
        ("flag_leakage", 5),
    ],
    "contract_gap": [
        ("auto_approve", 2),
        ("escalate_to_buyer", 4),
        ("refer_to_specialist", 2),
    ],
    "format_compliance": [
        ("auto_approve", 4),
        ("hold_for_review", 2),
        ("escalate_to_buyer", 1),
    ],
}

SUPPLIERS = [
    {
        "supplier_id": "SUP-001",
        "name": "Aster Industrial Chemicals",
        "category": "industrial chemicals",
        "exception_rate": 0.12,
        "avg_invoice_amount": 18450.0,
        "payment_terms": "Net 45",
        "otif_score": 0.88,
        "total_invoices": 1240,
        "total_exceptions": 149,
        "recent_trend": "declining",
    },
    {
        "supplier_id": "SUP-002",
        "name": "Pacifica Logistics",
        "category": "logistics",
        "exception_rate": 0.09,
        "avg_invoice_amount": 9720.0,
        "payment_terms": "Net 30",
        "otif_score": 0.91,
        "total_invoices": 880,
        "total_exceptions": 79,
        "recent_trend": "stable",
    },
    {
        "supplier_id": "SUP-003",
        "name": "Northstar Packaging",
        "category": "packaging",
        "exception_rate": 0.04,
        "avg_invoice_amount": 4260.0,
        "payment_terms": "Net 30",
        "otif_score": 0.96,
        "total_invoices": 2120,
        "total_exceptions": 85,
        "recent_trend": "improving",
    },
    {
        "supplier_id": "SUP-004",
        "name": "Novatek IT Services",
        "category": "IT services",
        "exception_rate": 0.03,
        "avg_invoice_amount": 28300.0,
        "payment_terms": "Net 60",
        "otif_score": 0.98,
        "total_invoices": 530,
        "total_exceptions": 16,
        "recent_trend": "stable",
    },
    {
        "supplier_id": "SUP-005",
        "name": "Yangtze Raw Materials",
        "category": "raw materials",
        "exception_rate": 0.14,
        "avg_invoice_amount": 36750.0,
        "payment_terms": "Net 45",
        "otif_score": 0.84,
        "total_invoices": 760,
        "total_exceptions": 106,
        "recent_trend": "declining",
    },
    {
        "supplier_id": "SUP-006",
        "name": "Meridian Office Services",
        "category": "office services",
        "exception_rate": 0.02,
        "avg_invoice_amount": 1950.0,
        "payment_terms": "Net 15",
        "otif_score": 0.97,
        "total_invoices": 3180,
        "total_exceptions": 64,
        "recent_trend": "stable",
    },
    {
        "supplier_id": "SUP-007",
        "name": "Boreal Equipment Maintenance",
        "category": "equipment maintenance",
        "exception_rate": 0.08,
        "avg_invoice_amount": 14600.0,
        "payment_terms": "Net 30",
        "otif_score": 0.90,
        "total_invoices": 640,
        "total_exceptions": 51,
        "recent_trend": "improving",
    },
    {
        "supplier_id": "SUP-008",
        "name": "Gridline Utilities",
        "category": "utilities",
        "exception_rate": 0.05,
        "avg_invoice_amount": 22100.0,
        "payment_terms": "Due on receipt",
        "otif_score": 0.94,
        "total_invoices": 420,
        "total_exceptions": 21,
        "recent_trend": "stable",
    },
    {
        "supplier_id": "SUP-009",
        "name": "Rhine-Stahl Metals",
        "category": "metals",
        "exception_rate": 0.11,
        "avg_invoice_amount": 41200.0,
        "payment_terms": "Net 45",
        "otif_score": 0.86,
        "total_invoices": 590,
        "total_exceptions": 65,
        "recent_trend": "declining",
    },
    {
        "supplier_id": "SUP-010",
        "name": "Helix Lab Supplies",
        "category": "pharma/lab supplies",
        "exception_rate": 0.06,
        "avg_invoice_amount": 8350.0,
        "payment_terms": "Net 30",
        "otif_score": 0.93,
        "total_invoices": 1380,
        "total_exceptions": 83,
        "recent_trend": "improving",
    },
]

COMMODITIES = {
    "price_variance": ["resin", "steel coil", "cloud seats"],
    "quantity_mismatch": ["pallets", "chemicals", "maintenance kits"],
    "duplicate_risk": ["freight surcharge", "consulting hours", "lab consumables"],
    "contract_gap": ["indexed metals", "managed services", "spot logistics"],
    "format_compliance": ["utility charges", "office supplies", "regulated reagents"],
}


def _clip(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def build_centroids() -> dict[str, dict[str, list[float]]]:
    centroids: dict[str, dict[str, list[float]]] = {}
    for category in CATEGORIES:
        centroids[category] = {}
        for action in ACTIONS:
            if (category, action) in ANCHOR_CENTROIDS:
                centroids[category][action] = ANCHOR_CENTROIDS[(category, action)]
                continue
            base = ACTION_CENTROIDS[action]
            tweak = CATEGORY_TWEAKS[category]
            centroids[category][action] = [_clip(base[i] + tweak[i]) for i in range(len(FACTORS))]
    return centroids


def _invoice_plan() -> list[tuple[str, str]]:
    plan: list[tuple[str, str]] = []
    for category in CATEGORIES:
        for action, count in DISTRIBUTION[category]:
            plan.extend((category, action) for _ in range(count))
    return plan


def _factor_dict(category: str, action: str, index: int, rng: random.Random) -> dict[str, float]:
    centroid = build_centroids()[category][action]
    factors = []
    for value in centroid:
        jitter = rng.uniform(-0.025, 0.025)
        factors.append(_clip(value + jitter))

    # Keep duplicate cases visibly duplicate-like even after jitter.
    if category == "duplicate_risk":
        factors[2] = max(factors[2], 0.72 if action == "flag_leakage" else 0.45)
    if category == "price_variance":
        factors[1] = max(factors[1], 0.04)
    if index % 11 == 0:
        factors[3] = _clip(factors[3] + 0.08)

    return dict(zip(FACTORS, factors))


CONTRACTUAL_LEAD_TIME_BY_CATEGORY = {
    "price_variance": 14,
    "quantity_mismatch": 10,
    "duplicate_risk": 7,
    "contract_gap": 21,
    "format_compliance": 5,
}

INTENT_BY_CATEGORY = {
    "price_variance": "invoice_price_variance",
    "quantity_mismatch": "invoice_match_failure",
    "duplicate_risk": "invoice_duplicate_risk",
    "contract_gap": "contract_compliance_gap",
    "format_compliance": "format_compliance_issue",
}

SUPPLIER_LEAD_TIME_DELTA = {
    "SUP-001": 6,
    "SUP-002": 2,
    "SUP-003": 0,
    "SUP-004": 1,
    "SUP-005": 8,
    "SUP-006": 0,
    "SUP-007": 2,
    "SUP-008": 1,
    "SUP-009": 6,
    "SUP-010": 1,
}

Q4_DEGRADING_SUPPLIERS = {"SUP-001", "SUP-005", "SUP-009"}


def _quarter(day: date) -> str:
    return f"Q{((day.month - 1) // 3) + 1}"


def _volume_band(line_items: list[dict], amount: float) -> str:
    quantity = sum(float(item.get("quantity") or 0.0) for item in line_items)
    if quantity >= 500 or amount >= 30000:
        return "high"
    if quantity >= 150 or amount >= 10000:
        return "medium"
    return "low"


def _lead_time_metadata(
    *,
    index: int,
    supplier_id: str,
    category: str,
    invoice_date: date,
    line_items: list[dict],
    amount: float,
) -> dict:
    gr_date = invoice_date - timedelta(days=1 + ((index - 1) % 5))
    contractual = CONTRACTUAL_LEAD_TIME_BY_CATEGORY.get(category, 14) + ((index - 1) % 3 - 1)
    contractual = max(contractual, 1)
    actual = contractual + SUPPLIER_LEAD_TIME_DELTA.get(supplier_id, 2)
    if _quarter(gr_date) == "Q4" and supplier_id in Q4_DEGRADING_SUPPLIERS:
        actual += 5 + ((index - 1) % 3)
    actual += ((index - 1) % 3) - 1
    actual = max(actual, 0)
    po_date = gr_date - timedelta(days=actual)
    return {
        "po_date": po_date.isoformat(),
        "gr_date": gr_date.isoformat(),
        "contractual_lead_time_days": float(contractual),
        "season": _quarter(gr_date),
        "volume_band": _volume_band(line_items, amount),
    }


def _invoice_enrichment(index: int, category: str, amount: float) -> dict:
    verified = index <= 35
    return {
        "intent": INTENT_BY_CATEGORY[category],
        "amount_at_risk": round(amount, 2),
        "verified": verified,
        "amount_recovered": round(amount * 0.93, 2) if verified else None,
        "cycle_time_hours": 24.0 if index == 1 else round(0.5 + ((index * 37) % 4750) / 100.0, 2),
    }


def build_invoices() -> list[dict]:
    rng = random.Random(SEED)
    plan = _invoice_plan()
    rng.shuffle(plan)
    suppliers = SUPPLIERS[:]
    invoices = []
    first_invoice_date = date(2026, 1, 7)

    for index, (category, action) in enumerate(plan, start=1):
        supplier = suppliers[(index - 1) % len(suppliers)]
        amount = round(max(250.0, rng.gauss(supplier["avg_invoice_amount"], supplier["avg_invoice_amount"] * 0.18)), 2)
        invoice_date = first_invoice_date + timedelta(days=(index - 1) * 7)
        due_date = invoice_date + timedelta(days=30 if supplier["payment_terms"] != "Due on receipt" else 0)
        factors = _factor_dict(category, action, index, rng)
        commodity = rng.choice(COMMODITIES[category])
        line_count = 1 + (index % 4)
        line_items = [
            {
                "line": line_number,
                "description": f"{commodity} line {line_number}",
                "quantity": 10 + line_number * ((index % 7) + 1),
                "unit_price": round(amount / (line_count * (10 + line_number)), 2),
            }
            for line_number in range(1, line_count + 1)
        ]
        lead_time_metadata = _lead_time_metadata(
            index=index,
            supplier_id=supplier["supplier_id"],
            category=category,
            invoice_date=invoice_date,
            line_items=line_items,
            amount=amount,
        )

        invoices.append(
            {
                "invoice_id": f"S2P-INV-{index:04d}",
                "supplier_id": supplier["supplier_id"],
                "supplier_name": supplier["name"],
                "po_number": f"PO-{20260000 + index:08d}",
                "amount": amount,
                "currency": "USD",
                "category": category,
                "ground_truth_action": action,
                "factors": factors,
                "metadata": {
                    "invoice_date": invoice_date.isoformat(),
                    "due_date": due_date.isoformat(),
                    "line_items": line_items,
                    "commodity": commodity,
                    "contract_ref": f"CTR-{supplier['supplier_id'][-3:]}-{category[:3].upper()}",
                    **lead_time_metadata,
                },
                **_invoice_enrichment(index, category, amount),
            }
        )

    return invoices


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    write_json(data_dir / "s2p_demo_suppliers.json", SUPPLIERS)
    write_json(data_dir / "synthetic_invoices.json", build_invoices())
    write_json(data_dir / "s2p_initial_centroids.json", build_centroids())
    print(f"Wrote deterministic S2P fixtures to {data_dir}")


if __name__ == "__main__":
    main()
