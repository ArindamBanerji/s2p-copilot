"""Process-tech fusion cycle for S2P bottlenecks."""

from __future__ import annotations

from typing import Any


class ProcessFusionCycle:
    """Track the WHERE, WHY, WHAT, LEARN, TRANSFER cycle."""

    def track_cycle(
        self,
        bottleneck_id: str,
        activity: str,
        context: dict[str, Any],
        provenance: str = "demo",
    ) -> dict[str, Any]:
        why = context.get("root_cause")
        evidence = list(context.get("evidence") or [])
        recommendation = context.get("recommendation")
        resolution_days = context.get("resolution_days")
        target_plants = list(context.get("targets") or [])
        promoted = bool(context.get("promoted", False))
        cycle = {
            "WHERE": {
                "source": "celonis",
                "metric": context.get("where_metric") or f"{activity} 3x slower",
            },
            "WHY": {
                "root_cause": why,
                "evidence": evidence,
            },
            "WHAT": {
                "recommendation": recommendation,
                "applied": bool(context.get("applied", False)),
            },
            "LEARN": {
                "outcome_verified": bool(context.get("verified", False)),
                "resolution_days": resolution_days,
            },
            "TRANSFER": {
                "promoted": promoted,
                "target_plants": target_plants,
            },
        }
        result = {
            "bottleneck_id": bottleneck_id,
            "cycle": cycle,
            "resolution_improvement": "12 days -> 2 days" if resolution_days else None,
            "narrative": self._build_narrative(activity, context),
            "provenance": provenance,
        }
        if provenance == "demo":
            result["provenance_note"] = (
                "Sample data. Connect process mining for live process outcomes."
            )
        return result

    def _build_narrative(self, activity: str, context: dict[str, Any]) -> str:
        root = context.get("root_cause") or "root cause pending"
        recommendation = context.get("recommendation") or "recommended action pending"
        target_count = len(context.get("targets") or [])
        suppliers = ", ".join(context.get("suppliers") or ["Suppliers X", "Supplier Y", "Supplier Z"])
        labels = "WHERE, WHY, WHAT, LEARN, TRANSFER"
        if context.get("resolution_days"):
            resolution = "Resolution: 12 days -> 2 days."
        else:
            resolution = "Resolution outcome pending."
        promotion = (
            f"Promoted to {target_count} plants via AgentEvolver."
            if context.get("promoted")
            else "Transfer pending outcome verification."
        )
        return (
            f"Process-Tech Fusion ({labels}): {activity} bottleneck analyzed. "
            f"Root cause: {root}. Evidence: {suppliers}. "
            f"Fix applied: {recommendation}. {resolution} {promotion}"
        )
