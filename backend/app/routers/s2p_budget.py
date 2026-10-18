"""Read-only adaptive investigation budget allocation endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from copilot_sdk.scoring.budget_policy import AdaptiveBudgetPolicy


def create_budget_allocation_router() -> APIRouter:
    router = APIRouter(prefix="/api/s2p", tags=["budget"])
    policy = AdaptiveBudgetPolicy(min_reads=1, max_reads=4, default_reads=2)

    @router.get("/budget/allocation")
    def get_budget_allocation() -> dict[str, object]:
        strips = []
        for confidence, category, difficulty in [
            (0.95, "duplicate_invoice", "easy"), (0.80, "price_variance", "medium"),
            (0.60, "contract_gap", "hard"), (0.40, "supplier_exception", "hard"),
            (0.25, "tax_compliance", "hard"),
        ]:
            reads = policy.allocate(confidence, category, verified_count=100)
            strips.append({"category": category, "difficulty": difficulty, "confidence": confidence,
                           "read_count": reads, "budget_pct": reads / 14.0})
        return {"budget_strips": strips, "total_budget": 14}

    return router
