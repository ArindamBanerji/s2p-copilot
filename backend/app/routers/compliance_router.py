"""S2P compliance screening endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter

from app.models.responses import GenericResponse
from app.services.compliance_screener import ComplianceScreener

router = APIRouter(prefix="/api/s2p/compliance", tags=["s2p-compliance"])


def _demo_transactions() -> list[dict[str, Any]]:
    return [
        {"transaction_id": "INV-2847", "supplier_id": "SUP-CHEN", "amount": 125_000.0},
        {"transaction_id": "INV-2848", "supplier_id": "SUP-CLEAR", "amount": 42_000.0},
    ]


def _demo_suppliers() -> list[dict[str, Any]]:
    return [
        {
            "supplier_id": "SUP-CHEN",
            "supplier_name": "Chen-Lin",
            "country_of_origin": "CN",
            "environmental_due_diligence": False,
            "carbon_footprint": 1_250.0,
        },
        {
            "supplier_id": "SUP-CLEAR",
            "supplier_name": "Meridian Components",
            "country_of_origin": "US",
            "environmental_due_diligence": True,
            "carbon_footprint": 430.0,
        },
    ]


@router.post("/screen", response_model=GenericResponse)
def screen_compliance(payload: dict[str, Any]) -> dict[str, Any]:
    transaction_value = payload.get("transaction")
    transaction = cast(dict[str, Any], transaction_value) if isinstance(transaction_value, dict) else payload
    supplier_value = payload.get("supplier")
    supplier = cast(dict[str, Any], supplier_value) if isinstance(supplier_value, dict) else {}
    return ComplianceScreener().screen(transaction, supplier)


@router.post("/batch", response_model=GenericResponse)
def batch_screen_compliance(payload: dict[str, Any]) -> dict[str, Any]:
    transactions = payload.get("transactions", [])
    suppliers = payload.get("suppliers", [])
    checks = ComplianceScreener().batch_screen(transactions, suppliers)
    return {
        "checks": checks,
        "total": len(checks),
        "narrative": (
            f"Batch compliance screening completed for {len(checks)} transactions. "
            "Tamper-evident audit hashes attached."
        ),
    }


@router.get("/report", response_model=GenericResponse)
def compliance_report() -> dict[str, Any]:
    screener = ComplianceScreener()
    checks = []
    for index in range(100):
        if index < 94:
            check = screener.screen(
                {"transaction_id": f"INV-HR-{index}", "supplier_id": "SUP-CHEN"},
                _demo_suppliers()[0],
            )
        else:
            check = screener.screen(
                {"transaction_id": f"INV-LR-{index}", "supplier_id": "SUP-CLEAR"},
                _demo_suppliers()[1],
            )
        check["verified_correct"] = index < 91
        checks.append(check)
    return screener.report(checks)
