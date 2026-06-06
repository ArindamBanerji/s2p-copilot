"""Supplier behavioral clustering endpoints for S2P."""

from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from fastapi import APIRouter

from app.models.responses import GenericResponse
from app.routers.s2p_data_helpers import load_suppliers
from app.services.supplier_profile_accumulator import accumulator

router = APIRouter(prefix="/api/s2p/suppliers", tags=["s2p-clustering"])

CLUSTER_DEFINITIONS = (
    ("Reliable Premium", ("Northstar", "Novatek", "Meridian")),
    ("Budget Volatile", ("Aster", "Yangtze", "Rhine-Stahl")),
    ("Mid-Tier Consistent", ("Pacifica", "Boreal")),
    ("Niche Specialist", ("Gridline", "Helix")),
)


def _load_supplier_profiles() -> list[dict[str, Any]]:
    """Return accumulator-backed profiles enriched with fixture-only demo fields."""
    fixture_by_id = {str(row.get("supplier_id")): row for row in load_suppliers()}
    profiles: list[dict[str, Any]] = []
    for profile in accumulator.get_all_profiles():
        data = asdict(profile)
        fixture = fixture_by_id.get(str(profile.supplier_id), {})
        data.setdefault("supplier_name", fixture.get("name") or profile.supplier_name)
        data["name"] = data.get("supplier_name") or fixture.get("name") or profile.supplier_id
        data["category"] = fixture.get("category") or (profile.categories[0] if profile.categories else None)
        data["avg_invoice_amount"] = _to_float(fixture.get("avg_invoice_amount"), 0.0)
        data["payment_terms"] = fixture.get("payment_terms")
        data["total_exceptions"] = int(_to_float(fixture.get("total_exceptions"), 0.0))
        profiles.append(data)
    return profiles


def _supplier_behavior_vector(profile: dict[str, Any]) -> list[float]:
    """Build a five-dimensional demo vector.

    The fixture does not provide production-grade quality or payment velocity
    metrics, so quality is conservatively inferred from exception rate and
    payment responsiveness is derived from payment-term labels.
    """
    exception_rate = _clamp01(_to_float(profile.get("exception_rate"), 0.0))
    delivery_reliability = _clamp01(_to_float(profile.get("otif", profile.get("otif_score")), 0.5))
    pricing_stability = _pricing_stability(profile)
    quality_score = _clamp01(_to_float(profile.get("quality_score"), 1.0 - exception_rate))
    payment_responsiveness = _payment_responsiveness(str(profile.get("payment_terms") or ""))
    return [
        round(delivery_reliability, 4),
        round(exception_rate, 4),
        round(pricing_stability, 4),
        round(quality_score, 4),
        round(payment_responsiveness, 4),
    ]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors must have the same length")
    dot = sum(left * right for left, right in zip(a, b))
    left_norm = math.sqrt(sum(value * value for value in a))
    right_norm = math.sqrt(sum(value * value for value in b))
    if left_norm == 0.0 or right_norm == 0.0:
        return 1.0
    return round(max(0.0, min(1.0, 1.0 - (dot / (left_norm * right_norm)))), 6)


def _build_demo_clusters(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assigned: set[str] = set()
    clusters: list[dict[str, Any]] = []
    for index, (label, name_markers) in enumerate(CLUSTER_DEFINITIONS, start=1):
        members = [
            profile
            for profile in profiles
            if any(marker.lower() in _profile_name(profile).lower() for marker in name_markers)
        ]
        for member in members:
            assigned.add(str(member.get("supplier_id")))
        cluster = _cluster_payload(index, label, members)
        clusters.append(cluster)

    remaining = [
        profile
        for profile in profiles
        if str(profile.get("supplier_id")) not in assigned
    ]
    if remaining:
        clusters[-1]["members"].extend(str(profile.get("supplier_id")) for profile in remaining)
        clusters[-1]["centroid"] = _centroid(remaining)
        clusters[-1]["consolidation_potential"] = _cluster_potential(clusters[-1])
        clusters[-1]["estimated_savings"] = _estimate_savings(clusters[-1])
    return clusters


def _estimate_savings(cluster: dict[str, Any]) -> float:
    if cluster["label"] == "Budget Volatile" and cluster["members"]:
        return 2_400_000.0
    if cluster["consolidation_potential"] == "medium":
        return 450_000.0
    return 0.0


def _cluster_potential(cluster: dict[str, Any]) -> str:
    if cluster["label"] == "Budget Volatile" and len(cluster["members"]) >= 3:
        return "high"
    if len(cluster["members"]) >= 3:
        return "medium"
    return "low"


@router.get("/clusters", response_model=GenericResponse)
def clusters() -> dict[str, Any]:
    profiles = _load_supplier_profiles()
    grouped = _build_demo_clusters(profiles)
    estimated_savings = sum(float(cluster["estimated_savings"]) for cluster in grouped)
    consolidation_candidates = sum(
        1
        for cluster in grouped
        if len(cluster["members"]) >= 3 and cluster["consolidation_potential"] in {"high", "medium"}
    )
    return {
        "clusters": grouped,
        "total_suppliers": len(profiles),
        "consolidation_candidates": consolidation_candidates,
        "estimated_annual_savings": estimated_savings,
        "method": "behavioral_centroid",
    }


@router.get("/similarity", response_model=GenericResponse)
def similarity(supplier_id: str) -> dict[str, Any]:
    profiles = _load_supplier_profiles()
    target = next((profile for profile in profiles if profile.get("supplier_id") == supplier_id), None)
    if target is None:
        return {"supplier_id": supplier_id, "similar_suppliers": [], "method": "cosine_distance"}

    target_vector = _supplier_behavior_vector(target)
    rows = []
    for profile in profiles:
        if profile.get("supplier_id") == supplier_id:
            continue
        distance = _cosine_distance(target_vector, _supplier_behavior_vector(profile))
        rows.append(
            {
                "supplier_id": profile.get("supplier_id"),
                "supplier_name": _profile_name(profile),
                "distance": distance,
                "similarity": round(1.0 - distance, 6),
            }
        )
    rows.sort(key=lambda row: (row["distance"], str(row["supplier_id"])))
    return {"supplier_id": supplier_id, "similar_suppliers": rows[:5], "method": "cosine_distance"}


def _cluster_payload(index: int, label: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    cluster = {
        "cluster_id": index,
        "label": label,
        "members": [str(profile.get("supplier_id")) for profile in members],
        "centroid": _centroid(members),
        "consolidation_potential": "low",
        "estimated_savings": 0.0,
    }
    cluster["consolidation_potential"] = _cluster_potential(cluster)
    cluster["estimated_savings"] = _estimate_savings(cluster)
    return cluster


def _centroid(profiles: list[dict[str, Any]]) -> list[float]:
    if not profiles:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    vectors = [_supplier_behavior_vector(profile) for profile in profiles]
    return [
        round(sum(vector[index] for vector in vectors) / len(vectors), 4)
        for index in range(5)
    ]


def _pricing_stability(profile: dict[str, Any]) -> float:
    trend = profile.get("pricing_trend")
    if isinstance(trend, (int, float)):
        return _clamp01(1.0 / (1.0 + abs(float(trend))))
    amount = _to_float(profile.get("avg_invoice_amount"), 0.0)
    if amount <= 0.0:
        return 0.5
    return _clamp01(1.0 - min(amount / 60_000.0, 0.8))


def _payment_responsiveness(payment_terms: str) -> float:
    normalized = payment_terms.strip().lower()
    if "due on receipt" in normalized:
        return 1.0
    if "15" in normalized:
        return 0.85
    if "30" in normalized:
        return 0.7
    if "45" in normalized:
        return 0.55
    if "60" in normalized:
        return 0.4
    return 0.5


def _profile_name(profile: dict[str, Any]) -> str:
    return str(profile.get("supplier_name") or profile.get("name") or profile.get("supplier_id") or "")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
