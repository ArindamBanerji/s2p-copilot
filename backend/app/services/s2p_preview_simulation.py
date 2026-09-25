"""Isolated compounding illustration; never reads or mutates the live scorer."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from app.domains.s2p.config import S2PDomainConfig


def _invoice_factor_dict(invoice: Any) -> dict[str, float]:
    factor_vector = np.array(invoice.factor_vector, dtype=float)
    return {
        name: float(factor_vector[index])
        for index, name in enumerate(list(S2PDomainConfig.factors))
    }


def _simulation_order_key(invoice: Any, scorer: Any) -> tuple[int, float]:
    result = scorer.score(
        _invoice_factor_dict(invoice),
        invoice.category,
        metadata={"source": "s2p_preview_ordering"},
    )
    correct = str(result.action) == str(invoice.ground_truth_action)
    return (1 if correct else 0, float(result.confidence))


def _build_compounding_trajectory(
    n: int = 1000,
    steps: int = 20,
    seed: int = 42,
    *,
    scorer_factory: Callable[[], Any],
) -> dict[str, Any]:
    from app.services.synthetic_invoices import SyntheticInvoiceGenerator

    generator = SyntheticInvoiceGenerator(seed=seed, noise_level=0.12)
    ordering_scorer = scorer_factory()
    invoices = sorted(generator.generate(n), key=lambda invoice: _simulation_order_key(invoice, ordering_scorer))
    scorer = scorer_factory()
    checkpoints = {
        max(1, round((index + 1) * len(invoices) / steps))
        for index in range(steps)
    }

    points: list[dict[str, Any]] = []
    correct_count = 0
    confidence_total = 0.0

    for decision_number, invoice in enumerate(invoices, start=1):
        factors = _invoice_factor_dict(invoice)
        result = scorer.score(
            factors,
            invoice.category,
            metadata={
                "invoice_id": invoice.invoice_id,
                "source": "s2p_preview_simulation",
            },
        )
        action = str(getattr(result, "action"))
        confidence = float(getattr(result, "confidence"))
        correct = action == str(invoice.ground_truth_action)

        correct_count += int(correct)
        confidence_total += confidence
        scorer.learn(
            result.decision_id,
            str(invoice.ground_truth_action),
            "confirmed" if correct else "overridden",
            context={
                "source": "s2p_preview_simulation",
                "confidence": confidence,
            },
        )

        if decision_number in checkpoints:
            points.append(
                {
                    "decisions": decision_number,
                    "decision_number": decision_number,
                    "accuracy": float(round(correct_count / decision_number, 4)),
                    "confidence": float(round(confidence_total / decision_number, 4)),
                    "batch": len(points) + 1,
                }
            )

    return {
        "points": points,
        "total_decisions": len(invoices),
    }


