"""S2P compliance screening endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request

from copilot_sdk.backend.conservation_utils import compute_conservation_metrics

from app.models.responses import GenericResponse
from app.services.compliance_screener import ComplianceScreener
from app.services.s2p_preview_data import merge_decisions

router = APIRouter(prefix="/api/s2p/compliance", tags=["s2p-compliance"])


_COMPLIANCE_FIELDS = (
    "country_of_origin",
    "environmental_due_diligence",
    "carbon_footprint",
    "sanctioned",
    "labor_practices",
    "scope3_status",
    "logistics_emissions",
    "supply_chain_ghg",
    "tier2_supplier_risk",
)


def _graph_transactions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for row in rows:
        transaction: dict[str, Any] = {
            "transaction_id": str(
                row.get("invoice_id")
                or row.get("source_invoice_id")
                or row.get("entity_id")
                or row.get("decision_id")
                or "UNKNOWN"
            ),
            "supplier_id": str(row.get("supplier_id") or "UNKNOWN"),
        }
        amount = row.get("amount", row.get("total_amount"))
        if amount is not None:
            transaction["amount"] = amount
        for field in _COMPLIANCE_FIELDS:
            if row.get(field) is not None:
                transaction[field] = row[field]
        verified_correct = row.get("is_correct", row.get("correct"))
        if isinstance(verified_correct, bool):
            transaction["verified_correct"] = verified_correct
        transactions.append(transaction)
    return transactions


def _graph_suppliers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suppliers: dict[str, dict[str, Any]] = {}
    for row in rows:
        supplier_id = str(row.get("supplier_id") or "")
        if not supplier_id:
            continue
        supplier = suppliers.setdefault(supplier_id, {"supplier_id": supplier_id})
        supplier_name = row.get("supplier_name") or row.get("supplier")
        if supplier_name:
            supplier["supplier_name"] = str(supplier_name)
        for field in _COMPLIANCE_FIELDS:
            if row.get(field) is not None:
                supplier[field] = row[field]
    return list(suppliers.values())


@router.post("/screen", response_model=GenericResponse)
def screen_compliance(payload: dict[str, Any]) -> dict[str, Any]:
    transaction_value = payload.get("transaction")
    transaction = cast(dict[str, Any], transaction_value) if isinstance(transaction_value, dict) else payload
    supplier_value = payload.get("supplier")
    supplier = cast(dict[str, Any], supplier_value) if isinstance(supplier_value, dict) else {}
    return cast(dict[str, Any], ComplianceScreener().screen(transaction, supplier))


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
def compliance_report(request: Request) -> dict[str, Any]:
    graph_store = getattr(request.app.state, "graph_store", None)
    if graph_store is None:
        raise HTTPException(status_code=503, detail="S2P graph store is unavailable")

    rows = merge_decisions(
        graph_store.get_all_decisions("s2p"),
        graph_store.get_verified_decisions("s2p"),
    )
    transactions = _graph_transactions(rows)
    suppliers = _graph_suppliers(rows)
    screener = ComplianceScreener()
    supplier_by_id = {str(row["supplier_id"]): row for row in suppliers}
    checks = []
    for transaction in transactions:
        check = screener.screen(
            transaction,
            supplier_by_id.get(str(transaction.get("supplier_id")), {}),
        )
        if "verified_correct" in transaction:
            check["verified_correct"] = transaction["verified_correct"]
        checks.append(check)

    metrics = compute_conservation_metrics(graph_store, domain="s2p")
    conservation_proof = {
        "status": str(metrics["status"]),
        "note": f"Computed from {int(metrics['V'])} verified S2P outcomes",
    }
    report = screener.report(checks, conservation_proof)
    report["provenance"] = "learned"
    return cast(dict[str, Any], report)
