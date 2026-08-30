"""Generate a deterministic, business-shaped S2P invoice demo dataset.

The generator intentionally writes separate 5,000-invoice outputs so the
small, legacy fixture used by the backend tests remains unchanged.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, TypedDict


DEFAULT_SEED = 42
DEFAULT_COUNT = 5_000
FACTOR_NAMES = (
    "amount_variance",
    "duplicate_score",
    "supplier_risk",
    "po_match_confidence",
    "tax_regulatory_compliance",
    "commodity_volatility",
    "historical_pattern",
    "environmental_risk",
)
CATEGORIES = (
    ("direct_materials", 0.40),
    ("indirect", 0.25),
    ("services", 0.15),
    ("logistics", 0.12),
    ("mro", 0.08),
)

# These are deliberately drawn from the repository's supplier fixture, while
# category affinities make supplier/category combinations look operationally
# plausible instead of uniformly random.
class Supplier(TypedDict):
    id: str
    name: str
    risk: float
    mean: float
    categories: tuple[str, ...]


SUPPLIERS: tuple[Supplier, ...] = (
    {"id": "SUP-001", "name": "Aster Industrial Chemicals", "risk": 0.68, "mean": 18_450, "categories": ("direct_materials",)},
    {"id": "SUP-002", "name": "Pacifica Logistics", "risk": 0.42, "mean": 9_720, "categories": ("logistics",)},
    {"id": "SUP-003", "name": "Northstar Packaging", "risk": 0.20, "mean": 4_260, "categories": ("direct_materials", "indirect")},
    {"id": "SUP-004", "name": "Novatek IT Services", "risk": 0.12, "mean": 28_300, "categories": ("services",)},
    {"id": "SUP-005", "name": "Yangtze Raw Materials", "risk": 0.76, "mean": 36_750, "categories": ("direct_materials",)},
    {"id": "SUP-006", "name": "Meridian Office Services", "risk": 0.10, "mean": 1_950, "categories": ("indirect", "services")},
    {"id": "SUP-007", "name": "Boreal Equipment Maintenance", "risk": 0.38, "mean": 14_600, "categories": ("mro",)},
    {"id": "SUP-008", "name": "Gridline Utilities", "risk": 0.28, "mean": 22_100, "categories": ("indirect", "services")},
    {"id": "SUP-009", "name": "Rhine-Stahl Metals", "risk": 0.63, "mean": 41_200, "categories": ("direct_materials", "mro")},
    {"id": "SUP-010", "name": "Helix Lab Supplies", "risk": 0.30, "mean": 8_350, "categories": ("mro", "indirect")},
)

CATEGORY_PROFILE: dict[str, dict[str, float]] = {
    "direct_materials": {"amount_variance": 0.25, "duplicate_score": 0.12, "po_match_confidence": 0.88, "tax_regulatory_compliance": 0.91, "commodity_volatility": 0.72, "historical_pattern": 0.75, "environmental_risk": 0.57},
    "indirect": {"amount_variance": 0.16, "duplicate_score": 0.19, "po_match_confidence": 0.82, "tax_regulatory_compliance": 0.94, "commodity_volatility": 0.34, "historical_pattern": 0.79, "environmental_risk": 0.31},
    "services": {"amount_variance": 0.21, "duplicate_score": 0.24, "po_match_confidence": 0.74, "tax_regulatory_compliance": 0.92, "commodity_volatility": 0.20, "historical_pattern": 0.73, "environmental_risk": 0.25},
    "logistics": {"amount_variance": 0.19, "duplicate_score": 0.14, "po_match_confidence": 0.86, "tax_regulatory_compliance": 0.90, "commodity_volatility": 0.55, "historical_pattern": 0.70, "environmental_risk": 0.62},
    "mro": {"amount_variance": 0.23, "duplicate_score": 0.21, "po_match_confidence": 0.80, "tax_regulatory_compliance": 0.89, "commodity_volatility": 0.48, "historical_pattern": 0.76, "environmental_risk": 0.44},
}

COMMODITIES = {
    "direct_materials": ("steel coil", "resin", "aluminum billet", "industrial chemicals"),
    "indirect": ("office supplies", "packaging", "utilities", "safety consumables"),
    "services": ("managed services", "consulting hours", "cloud capacity", "facilities services"),
    "logistics": ("ocean freight", "truckload freight", "pallets", "fuel surcharge"),
    "mro": ("maintenance kits", "bearings", "lab consumables", "replacement parts"),
}


def _clip(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _weighted_categories(count: int) -> list[str]:
    rows: list[str] = []
    for category, weight in CATEGORIES:
        rows.extend([category] * round(count * weight))
    return rows[:count]


def _supplier_for(category: str, index: int) -> Supplier:
    preferred = [supplier for supplier in SUPPLIERS if category in supplier["categories"]]
    pool = preferred or list(SUPPLIERS)
    return pool[index % len(pool)]


def _factor_values(category: str, supplier: Supplier, rng: random.Random) -> dict[str, float]:
    profile = CATEGORY_PROFILE[category]
    values = {
        "amount_variance": rng.betavariate(2.4, 7.5) * 0.75 + profile["amount_variance"] * 0.25,
        "duplicate_score": rng.betavariate(1.7, 8.0) * 0.75 + profile["duplicate_score"] * 0.25,
        "supplier_risk": rng.betavariate(2.5, 4.0) * 0.45 + supplier["risk"] * 0.55,
        "po_match_confidence": rng.betavariate(13.0, 2.0) * 0.65 + profile["po_match_confidence"] * 0.35,
        "tax_regulatory_compliance": rng.betavariate(16.0, 2.0) * 0.65 + profile["tax_regulatory_compliance"] * 0.35,
        "commodity_volatility": rng.betavariate(2.8, 3.5) * 0.65 + profile["commodity_volatility"] * 0.35,
        "historical_pattern": rng.betavariate(9.0, 2.5) * 0.70 + profile["historical_pattern"] * 0.30,
        "environmental_risk": rng.betavariate(2.2, 5.0) * 0.70 + profile["environmental_risk"] * 0.30,
    }
    return {name: _clip(values[name]) for name in FACTOR_NAMES}


def _select_event_indices(invoices: list[dict[str, Any]], factor: str, count: int, inverse: bool = False) -> list[int]:
    ranked = sorted(
        range(len(invoices)),
        key=lambda index: (invoices[index]["factors"][factor], -index),
        reverse=not inverse,
    )
    return ranked[:count]


def build_invoices(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    if count <= 0:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    categories = _weighted_categories(count)
    rng.shuffle(categories)
    start = date(2026, 1, 5)
    invoices: list[dict[str, Any]] = []
    for offset, category in enumerate(categories):
        supplier = _supplier_for(category, offset)
        amount = max(150.0, rng.lognormvariate(math.log(supplier["mean"]), 0.42))
        invoice_date = start + timedelta(days=offset % 365)
        factors = _factor_values(category, supplier, rng)
        commodity = rng.choice(COMMODITIES[category])
        invoices.append(
            {
                "id": f"INV-2026-{offset + 1:05d}",
                "invoice_id": f"INV-2026-{offset + 1:05d}",
                "supplier_id": supplier["id"],
                "supplier_name": supplier["name"],
                "category": category,
                "amount": round(amount, 2),
                "currency": "USD",
                "po_number": f"PO-2026-{10_000 + offset + 1:06d}",
                "date": invoice_date.isoformat(),
                "factors": factors,
                "events": [],
                "commodity": commodity,
                "provenance": "synthetic_demo",
            }
        )

    event_specs = (
        ("supplier_distress", "supplier_risk", round(count * 0.05), False, "Supplier financial or delivery health deteriorated."),
        ("commodity_spike", "commodity_volatility", round(count * 0.03), False, "Relevant commodity index moved sharply above its normal band."),
        ("compliance_flag", "tax_regulatory_compliance", round(count * 0.02), True, "Tax, regulatory, or documentation evidence requires review."),
    )
    for event_type, factor, event_count, inverse, description in event_specs:
        for index in _select_event_indices(invoices, factor, event_count, inverse):
            invoices[index]["events"].append(
                {
                    "type": event_type,
                    "severity": "high" if invoices[index]["factors"][factor] >= 0.75 else "medium",
                    "description": description,
                }
            )
    return invoices


def write_outputs(invoices: list[dict[str, Any]], data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "synthetic_invoices_5k.json").write_text(
        json.dumps(invoices, indent=2) + "\n", encoding="utf-8"
    )
    fieldnames = ["id", "invoice_id", "supplier_id", "supplier_name", "category", "amount", "currency", "po_number", "date", *FACTOR_NAMES, "events", "commodity", "provenance"]
    with (data_dir / "synthetic_invoices_5k.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for invoice in invoices:
            row = {key: invoice.get(key, "") for key in fieldnames}
            row.update(invoice["factors"])
            row["events"] = json.dumps(invoice["events"], separators=(",", ":"))
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    write_outputs(build_invoices(args.count, args.seed), repo_root / "data")
    print(f"Wrote {args.count:,} deterministic invoices to {repo_root / 'data'}")


if __name__ == "__main__":
    main()
