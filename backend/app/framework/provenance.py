"""
ProvenanceService — factor provenance and decision audit trail (Phase 6).

Provides a human-readable explanation of why each factor has its computed value,
which graph nodes were consulted, and the computation method used.

Factor explainers are registered in _FACTOR_EXPLAINERS; the caller supplies
factor_names and resolve_category so this module has zero SOC-domain coupling.

Reference: docs/project_status_and_plan_v3_part2.md Phase 6
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FactorProvenance:
    factor_name: str
    factor_value: float
    computation_method: str
    graph_nodes_consulted: List[str] = field(default_factory=list)
    explanation: str = ""
    confidence: Optional[float] = None


@dataclass
class DecisionProvenance:
    decision_id: str
    factors: List[FactorProvenance]
    total_nodes_consulted: int = 0
    category: str = ""
    action: str = ""


# ---------------------------------------------------------------------------
# Per-factor explainers
# ---------------------------------------------------------------------------

def _explain_travel_match(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Graph traversal: (User)-[:HAS_TRAVEL]->(TravelRecord) "
        "WHERE destination = source_location"
    )
    nodes = ["User", "TravelRecord"]
    if value >= 0.7:
        explanation = (
            f"Travel record found matching source location (score={value:.2f}). "
            "High travel match — likely business travel explains the anomaly."
        )
    elif value == 0.5:
        explanation = (
            "No travel records found for this user/location pair. "
            "Neutral prior applied (score=0.50)."
        )
    else:
        explanation = (
            f"Partial or low travel match (score={value:.2f}). "
            "Limited corroboration from travel records."
        )
    return method, explanation, nodes


def _explain_asset_criticality(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Graph traversal: (Alert)-[:DETECTED_ON]->(Asset)-[:STORES]->(DataClass)"
    )
    nodes = ["Alert", "Asset", "DataClass"]
    if value >= 0.9:
        explanation = (
            f"Critical asset detected (score={value:.2f}). "
            "CRITICAL criticality level with sensitive data classification."
        )
    elif value >= 0.7:
        explanation = (
            f"High-criticality asset (score={value:.2f}). "
            "HIGH criticality; sensitive data may be in scope."
        )
    elif value == 0.5:
        explanation = (
            "Asset criticality undetermined or medium (score=0.50). "
            "Default or MEDIUM criticality applied."
        )
    else:
        explanation = (
            f"Low-criticality asset (score={value:.2f}). "
            "LOW criticality — reduced blast radius if action is wrong."
        )
    return method, explanation, nodes


def _explain_threat_intel_enrichment(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Graph traversal: (ThreatIntel)-[:ASSOCIATED_WITH]->(Alert)"
    )
    nodes = ["ThreatIntel", "Alert"]
    if value >= 0.85:
        explanation = (
            f"High-confidence IOC match (score={value:.2f}). "
            "Multiple threat intel sources corroborate; corroboration boost applied."
        )
    elif value >= 0.6:
        explanation = (
            f"Medium-severity IOC match (score={value:.2f}). "
            "Threat intelligence match found in at least one feed."
        )
    elif value > 0.0:
        explanation = (
            f"Low-severity IOC association (score={value:.2f}). "
            "Low-confidence threat intel entry — treat as contextual signal."
        )
    else:
        explanation = (
            "No threat intelligence match (score=0.00). "
            "Alert source/destination not present in any IOC feed."
        )
    return method, explanation, nodes


def _explain_pattern_history(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Graph traversal: (Decision)-[:DECIDED_ON]->(Alert) "
        "WHERE outcome IS NOT NULL — last 100 decisions for this alert_type"
    )
    nodes = ["Decision", "Alert"]
    if value >= 0.8:
        explanation = (
            f"Strong historical accuracy (score={value:.2f}). "
            "High prior decision accuracy for this alert type builds confidence."
        )
    elif value >= 0.6:
        explanation = (
            f"Moderate historical accuracy (score={value:.2f}). "
            "Some pattern history available; accuracy above random."
        )
    elif value == 0.5:
        explanation = (
            "Insufficient history (<5 resolved decisions). "
            "Neutral prior applied (score=0.50)."
        )
    else:
        explanation = (
            f"Low historical accuracy (score={value:.2f}). "
            "Prior decisions for this alert type had high error rate."
        )
    return method, explanation, nodes


def _explain_time_anomaly(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Alert property read: business_hours_login, weekend_login [TD-014]. "
        "Future: (User)-[:ACTIVE_AT]->(TimeSlot) traversal."
    )
    nodes = ["Alert"]
    if value >= 1.0:
        explanation = (
            "Weekend login detected (score=1.00). "
            "Activity outside normal business schedule — high time anomaly."
        )
    elif value >= 0.7:
        explanation = (
            f"After-hours activity (score={value:.2f}). "
            "Login or activity outside standard business hours."
        )
    elif value <= 0.0:
        explanation = (
            "Activity during business hours (score=0.00). "
            "No time-based anomaly detected."
        )
    else:
        explanation = (
            f"Moderate time anomaly (score={value:.2f}). "
            "Partially outside normal hours or ambiguous time signal."
        )
    return method, explanation, nodes


def _explain_device_trust(value: float) -> Tuple[str, str, List[str]]:
    method = (
        "Alert property read: mfa_completed, device_fingerprint_match, vpn [TD-015]. "
        "Future: (Alert)-[:USES_DEVICE]->(Device) traversal."
    )
    nodes = ["Alert"]
    if value <= 0.0:
        explanation = (
            "Fully trusted device (score=0.00). "
            "MFA completed, device fingerprint matched, VPN active."
        )
    elif value <= 0.34:
        explanation = (
            f"Mostly trusted device (score={value:.2f}). "
            "One trust signal missing (MFA, fingerprint, or VPN)."
        )
    elif value <= 0.67:
        explanation = (
            f"Partially trusted device (score={value:.2f}). "
            "Two trust signals missing — verify device before acting."
        )
    else:
        explanation = (
            f"Untrusted device (score={value:.2f}). "
            "Multiple trust signals absent — MFA, fingerprint, and/or VPN missing."
        )
    return method, explanation, nodes


_FACTOR_EXPLAINERS = {
    "travel_match":             _explain_travel_match,
    "asset_criticality":        _explain_asset_criticality,
    "threat_intel_enrichment":  _explain_threat_intel_enrichment,
    "pattern_history":          _explain_pattern_history,
    "time_anomaly":             _explain_time_anomaly,
    "device_trust":             _explain_device_trust,
}


# ---------------------------------------------------------------------------
# ProvenanceService
# ---------------------------------------------------------------------------

class ProvenanceService:
    """Builds factor provenance records for a decision."""

    @staticmethod
    def build_provenance(
        decision_id: str,
        factor_names: List[str],
        factor_values: List[float],
        category: str = "",
        action: str = "",
    ) -> DecisionProvenance:
        """
        Build provenance for a decision.

        Parameters
        ----------
        decision_id   : UUID string of the Decision node
        factor_names  : ordered list of factor names
        factor_values : ordered list of factor values (0.0–1.0)
        category      : alert category (e.g. "credential_access")
        action        : selected action (e.g. "escalate")

        Returns
        -------
        DecisionProvenance with one FactorProvenance per factor
        """
        factors: List[FactorProvenance] = []
        all_nodes: set = set()

        for name, value in zip(factor_names, factor_values):
            explainer = _FACTOR_EXPLAINERS.get(name)
            if explainer:
                method, explanation, nodes = explainer(float(value))
            else:
                method = "Unknown computation method"
                explanation = f"Factor {name!r} value={value:.2f} — no explainer registered"
                nodes = []

            all_nodes.update(nodes)
            factors.append(FactorProvenance(
                factor_name=name,
                factor_value=round(float(value), 4),
                computation_method=method,
                graph_nodes_consulted=list(nodes),
                explanation=explanation,
            ))

        return DecisionProvenance(
            decision_id=decision_id,
            factors=factors,
            total_nodes_consulted=len(all_nodes),
            category=category,
            action=action,
        )

    @staticmethod
    async def get_provenance_from_graph(
        decision_id: str,
        neo4j_service: Any,
        factor_names: Optional[List[str]] = None,
        resolve_category: Optional[Any] = None,
    ) -> Optional[dict]:
        """
        Retrieve a stored decision's factor vector from Neo4j and rebuild provenance.

        Parameters
        ----------
        decision_id      : UUID of the Decision node
        neo4j_service    : db client with run_query()
        factor_names     : ordered factor name list; pass SOC_FACTORS for SOC copilot
        resolve_category : callable(alert_type) -> category str; pass
                           resolve_alert_category for SOC copilot

        Returns None if decision not found.
        """
        _factor_names = factor_names if factor_names is not None else []
        _resolve = resolve_category if resolve_category is not None else (lambda x: x)

        try:
            results = await neo4j_service.run_query(
                """
                MATCH (d:Decision {id: $id})-[:DECIDED_ON]->(a:Alert)
                RETURN d.factor_vector AS fv,
                       d.action        AS action,
                       a.alert_type    AS alert_type
                """,
                {"id": decision_id},
            )
        except Exception as exc:
            log.warning("[PROVENANCE] query failed for decision=%r: %s", decision_id, exc)
            return None

        if not results:
            return None

        r = results[0]
        fv         = r.get("fv") or []
        action     = r.get("action") or ""
        alert_type = r.get("alert_type") or ""
        category   = _resolve(alert_type) if alert_type else ""

        prov = ProvenanceService.build_provenance(
            decision_id=decision_id,
            factor_names=_factor_names,
            factor_values=[float(v) for v in fv],
            category=category,
            action=action,
        )

        return {
            "decision_id":           prov.decision_id,
            "category":              prov.category,
            "action":                prov.action,
            "total_nodes_consulted": prov.total_nodes_consulted,
            "factors": [
                {
                    "factor_name":           fp.factor_name,
                    "factor_value":          fp.factor_value,
                    "computation_method":    fp.computation_method,
                    "graph_nodes_consulted": fp.graph_nodes_consulted,
                    "explanation":           fp.explanation,
                }
                for fp in prov.factors
            ],
        }
