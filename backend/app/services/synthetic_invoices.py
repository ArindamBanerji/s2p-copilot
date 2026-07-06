"""K3 demo-population fixture (Rule 67).

All generated output carries provenance="sample".

NEVER use in a metric, score, par, or claim (F-26).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np


@dataclass
class SyntheticInvoice:
    invoice_id: str
    supplier_id: str
    supplier_name: str
    category: str
    category_index: int
    ground_truth_action: str
    ground_truth_action_index: int
    factors: Dict[str, float]
    factor_vector: List[float]
    amount: float
    po_reference: str
    variance_pct: float
    provenance: str = "sample"
    confidence: Optional[float] = None
    recommended_action: Optional[str] = None


@dataclass
class SyntheticSupplier:
    supplier_id: str
    supplier_name: str
    region: str
    otif_q1_q2: float
    otif_q3: float
    exception_rate_baseline: float
    exception_rate_current: float
    lead_time_contractual: int
    lead_time_actual_q4: int
    financial_health_trend: str
    format_compliance_pct: float
    total_invoices_ytd: int


class SyntheticInvoiceGenerator:
    """Generate deterministic S2P v2 invoice fixtures around profile centroids."""

    CATEGORY_WEIGHTS = np.array([0.30, 0.15, 0.10, 0.20, 0.25], dtype=float)
    ACTION_WEIGHTS = {
        "price_variance": [0.40, 0.25, 0.10, 0.15, 0.10],
        "quantity_mismatch": [0.20, 0.30, 0.20, 0.10, 0.20],
        "duplicate_risk": [0.10, 0.20, 0.15, 0.35, 0.20],
        "contract_gap": [0.15, 0.25, 0.25, 0.15, 0.20],
        "format_compliance": [0.35, 0.25, 0.10, 0.10, 0.20],
    }

    def __init__(self, seed: int = 42, noise_level: float = 0.08):
        self.seed = seed
        self.noise_level = noise_level
        self.rng = np.random.default_rng(seed)
        self._load_config()
        self.suppliers = self._create_supplier_pool()

    def _load_config(self) -> None:
        from app.domains.s2p.config import S2PDomainConfigV2

        self.config = S2PDomainConfigV2
        self.categories = self._config_sequence("categories", "get_categories")
        self.actions = self._config_sequence("actions", "get_actions")
        self.factors = self._config_sequence("factors", "get_factors")
        self.centroids = self.config.get_profile_centroids()
        expected_shape = (
            self.config.n_categories,
            self.config.n_actions,
            self.config.n_factors,
        )
        if self.centroids.shape != expected_shape:
            raise ValueError(f"Expected v2 centroids shape {expected_shape}, got {self.centroids.shape}")
        self.n_categories, self.n_actions, self.n_factors = self.centroids.shape

    def _config_sequence(self, attr_name: str, method_name: str) -> List[str]:
        method = getattr(self.config, method_name, None)
        if callable(method):
            return list(method())
        return list(getattr(self.config, attr_name))

    def _create_supplier_pool(self) -> List[SyntheticSupplier]:
        supplier_rows = [
            ("SUP-001", "Chen-Lin Mfg", "APAC", 0.94, 0.72, 0.03, 0.11, 14, 21, "declining", 0.92, 1200),
            ("SUP-002", "Hartwell Corp", "NA", 0.97, 0.96, 0.02, 0.02, 7, 8, "stable", 0.98, 3400),
            ("SUP-003", "Rhine-Stahl GmbH", "EU", 0.91, 0.88, 0.05, 0.04, 21, 24, "stable", 0.95, 800),
            ("SUP-004", "Pacifica Logistics", "APAC", 0.89, 0.65, 0.07, 0.15, 28, 42, "declining", 0.88, 600),
            ("SUP-005", "NovaTech Solutions", "NA", 0.96, 0.95, 0.01, 0.01, 5, 6, "improving", 0.99, 4200),
            ("SUP-006", "Meridian Supply Co", "LATAM", 0.85, 0.80, 0.08, 0.09, 18, 22, "stable", 0.90, 950),
            ("SUP-007", "Boreal Industries", "EU", 0.93, 0.91, 0.04, 0.03, 10, 12, "improving", 0.96, 2100),
            ("SUP-008", "Sahara Trading", "MEA", 0.82, 0.70, 0.10, 0.18, 35, 50, "declining", 0.82, 400),
            ("SUP-009", "Summit Electronics", "NA", 0.98, 0.97, 0.01, 0.02, 4, 5, "stable", 0.97, 5600),
            ("SUP-010", "Yangtze Materials", "APAC", 0.90, 0.78, 0.06, 0.12, 16, 25, "declining", 0.85, 1500),
        ]
        return [SyntheticSupplier(*row) for row in supplier_rows]

    def generate(self, n: int = 50, noise_level: Optional[float] = None) -> List[SyntheticInvoice]:
        effective_noise = self.noise_level if noise_level is None else noise_level
        invoices: List[SyntheticInvoice] = []

        for idx in range(n):
            category_index = int(self.rng.choice(self.n_categories, p=self.CATEGORY_WEIGHTS))
            category = self.categories[category_index]
            action_weights = np.array(self.ACTION_WEIGHTS[category], dtype=float)
            ground_truth_action_index = int(self.rng.choice(self.n_actions, p=action_weights))
            ground_truth_action = self.actions[ground_truth_action_index]

            centroid = self.centroids[category_index, ground_truth_action_index]
            noise = self.rng.normal(loc=0.0, scale=effective_noise, size=self.n_factors)
            factor_array = np.clip(centroid + noise, 0.0, 1.0)
            factor_vector = [round(float(value), 6) for value in factor_array.tolist()]
            factors = {
                factor_name: factor_vector[factor_index]
                for factor_index, factor_name in enumerate(self.factors)
            }

            supplier = self.suppliers[int(self.rng.integers(0, len(self.suppliers)))]
            amount = round(float(self.rng.uniform(500.0, 50000.0)), 2)

            invoices.append(
                SyntheticInvoice(
                    invoice_id=f"INV-{idx + 1:05d}",
                    supplier_id=supplier.supplier_id,
                    supplier_name=supplier.supplier_name,
                    category=category,
                    category_index=category_index,
                    ground_truth_action=ground_truth_action,
                    ground_truth_action_index=ground_truth_action_index,
                    factors=factors,
                    factor_vector=factor_vector,
                    amount=amount,
                    po_reference=f"PO-{int(self.rng.integers(0, 100000)):05d}",
                    variance_pct=round(factor_vector[1] * 100.0, 4),
                )
            )

        return invoices

    def generate_supplier_fixture(self) -> List[Dict]:
        return [self._supplier_to_fixture(supplier) for supplier in self.suppliers]

    def _supplier_to_fixture(self, supplier: SyntheticSupplier) -> Dict:
        return {
            "supplier_id": supplier.supplier_id,
            "supplier_name": supplier.supplier_name,
            "region": supplier.region,
            "otif": {
                "q1_q2": supplier.otif_q1_q2,
                "q3": supplier.otif_q3,
            },
            "exception_rate": {
                "baseline": supplier.exception_rate_baseline,
                "current": supplier.exception_rate_current,
            },
            "lead_time": {
                "contractual": supplier.lead_time_contractual,
                "actual_q4": supplier.lead_time_actual_q4,
            },
            "financial_health_trend": supplier.financial_health_trend,
            "format_compliance_pct": supplier.format_compliance_pct,
            "total_invoices_ytd": supplier.total_invoices_ytd,
            "provenance": "sample",
        }

    def export_as_scoring_input(self, invoices: List[SyntheticInvoice]) -> List[Dict]:
        return [
            {
                "invoice_id": invoice.invoice_id,
                "supplier_id": invoice.supplier_id,
                "supplier_name": invoice.supplier_name,
                "category": invoice.category,
                "category_index": invoice.category_index,
                "factor_vector": list(invoice.factor_vector),
                "amount": invoice.amount,
                "po_reference": invoice.po_reference,
                "variance_pct": invoice.variance_pct,
                "ground_truth_action": invoice.ground_truth_action,
                "ground_truth_action_index": invoice.ground_truth_action_index,
                "provenance": invoice.provenance,
            }
            for invoice in invoices
        ]

    def export_full_invoice_dicts(self, invoices: List[SyntheticInvoice]) -> List[Dict]:
        return [asdict(invoice) for invoice in invoices]
