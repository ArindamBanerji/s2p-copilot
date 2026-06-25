"""Optimizer export service for learned S2P parameters."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from app.domains.s2p.config import S2PDomainConfig
from app.routers.s2p_explorer import _centroids_from_scorer, _read_dk_weights

SCHEMA_VERSION = "1.0"
CONSUMER_COMPATIBILITY = ["gurobi", "or-tools", "aimms", "celonis"]
VALID_SECTIONS = {
    "centroids",
    "dk_weights",
    "supplier_profiles",
    "lead_time_distributions",
    "exception_likelihoods",
    "conservation_state",
}
SECTION_ALIASES = {
    "suppliers": "supplier_profiles",
    "supplier_profiles": "supplier_profiles",
    "centroids": "centroids",
    "dk_weights": "dk_weights",
    "lead_time_distributions": "lead_time_distributions",
    "exception_likelihoods": "exception_likelihoods",
    "conservation_state": "conservation_state",
}


class OptimizerExportService:
    """Structured export of learned S2P parameters."""

    def export(
        self,
        scorer: Any = None,
        profiles: list[dict[str, Any]] | None = None,
        sections: list[str] | None = None,
    ) -> dict[str, Any]:
        selected = self._selected_sections(sections)
        payload: dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "domain": "s2p",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tensor_shape": {
                "categories": S2PDomainConfig.n_categories,
                "actions": S2PDomainConfig.n_actions,
                "factors": S2PDomainConfig.n_factors,
            },
            "sections_available": sorted(VALID_SECTIONS),
            "consumer_compatibility": list(CONSUMER_COMPATIBILITY),
        }
        if "centroids" in selected:
            payload["centroids"] = self._centroids(scorer)
        if "dk_weights" in selected:
            weights = self._dk_weights(scorer)
            if weights is not None:
                payload["dk_weights"] = weights
            else:
                payload.setdefault("warnings", []).append("DK weights unavailable before trust-weight transition.")
        profile_rows = profiles or []
        if "supplier_profiles" in selected:
            payload["supplier_profiles"] = profile_rows
        if "lead_time_distributions" in selected:
            payload["lead_time_distributions"] = self._lead_time_distributions(profile_rows)
        if "exception_likelihoods" in selected:
            payload["exception_likelihoods"] = self._exception_likelihoods(profile_rows)
        if "conservation_state" in selected:
            payload["conservation_state"] = self._conservation_state(scorer)
        payload.update(self._compute_metadata(payload))
        payload["narrative"] = self._narrative(payload)
        return payload

    def to_json(self, export: dict[str, Any]) -> str:
        return json.dumps(export, sort_keys=True)

    def validate(self, export: dict[str, Any]) -> dict[str, Any]:
        required = [
            "centroids",
            "supplier_profiles",
            "lead_time_distributions",
            "exception_likelihoods",
            "conservation_state",
        ]
        missing = [section for section in required if section not in export]
        warnings = list(export.get("warnings", []))
        if "dk_weights" not in export:
            warnings.append("DK weights are optional before trust-weight transition.")
        return {
            "valid": not missing,
            "missing": missing,
            "warnings": warnings,
        }

    def schema_definition(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "type": "object",
            "required": ["version", "domain", "tensor_shape", "total_parameters"],
            "properties": {
                "version": {"type": "string"},
                "domain": {"const": "s2p"},
                "tensor_shape": {
                    "type": "object",
                    "properties": {
                        "categories": {"type": "integer"},
                        "actions": {"type": "integer"},
                        "factors": {"type": "integer"},
                    },
                },
                "centroids": {"type": "array"},
                "dk_weights": {"type": "array"},
                "supplier_profiles": {"type": "array"},
                "lead_time_distributions": {"type": "array"},
                "exception_likelihoods": {"type": "array"},
                "conservation_state": {"type": "object"},
            },
            "consumer_compatibility": list(CONSUMER_COMPATIBILITY),
        }

    def _selected_sections(self, sections: list[str] | None) -> set[str]:
        if not sections:
            return set(VALID_SECTIONS)
        selected = set()
        invalid = []
        for section in sections:
            key = section.strip()
            normalized = SECTION_ALIASES.get(key)
            if normalized is None:
                invalid.append(key)
            else:
                selected.add(normalized)
        if invalid:
            raise ValueError(f"Invalid sections: {sorted(invalid)}. Valid: {sorted(VALID_SECTIONS)}")
        return selected

    def _centroids(self, scorer: Any) -> list[list[list[float]]]:
        raw = self._raw_centroids(scorer)
        if raw is None:
            raw = S2PDomainConfig.get_profile_centroids()
        if hasattr(raw, "tolist"):
            raw = raw.tolist()
        return self._round_nested(raw)

    def _raw_centroids(self, scorer: Any) -> Any:
        if scorer is not None:
            try:
                return _centroids_from_scorer(scorer)
            except Exception:
                return None
        return None

    def _dk_weights(self, scorer: Any) -> list[list[float]] | None:
        flat = _read_dk_weights(scorer) if scorer is not None else None
        if not flat:
            return None
        flat = [round(float(value), 6) for value in flat]
        legacy_count = S2PDomainConfig.n_categories * (S2PDomainConfig.n_factors - 1)
        if S2PDomainConfig.n_factors == 8 and len(flat) == legacy_count:
            migrated: list[float] = []
            row_width = S2PDomainConfig.n_factors - 1
            for index in range(0, len(flat), row_width):
                migrated.extend([*flat[index : index + row_width], 1.0])
            flat = migrated
        if len(flat) == S2PDomainConfig.n_categories * S2PDomainConfig.n_factors:
            return [
                flat[index : index + S2PDomainConfig.n_factors]
                for index in range(0, len(flat), S2PDomainConfig.n_factors)
            ]
        if len(flat) == S2PDomainConfig.n_factors:
            return [list(flat) for _category in S2PDomainConfig.categories]
        return None

    def _lead_time_distributions(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "supplier_id": profile.get("supplier_id"),
                "avg_lead_time_days": profile.get("avg_lead_time_days"),
                "by_quarter": profile.get("lead_time_by_quarter", {}),
                "by_volume": profile.get("lead_time_by_volume", {}),
            }
            for profile in profiles
        ]

    def _exception_likelihoods(self, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "supplier_id": profile.get("supplier_id"),
                "exception_rate": profile.get("exception_rate", 0.0),
                "categories": profile.get("categories", []),
            }
            for profile in profiles
        ]

    def _conservation_state(self, scorer: Any) -> dict[str, Any]:
        state = getattr(scorer, "conservation_state", None)
        if callable(state):
            state = state()
        if isinstance(state, dict):
            return state
        return {"status": "unknown", "alpha": None, "q": None, "V": None}

    def _round_nested(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._round_nested(item) for item in value]
        return round(float(value), 6)

    def _compute_metadata(self, export: dict[str, Any]) -> dict[str, Any]:
        centroid_count = self._count_centroid_values(export.get("centroids"))
        dk_count = self._count_dk_values(export.get("dk_weights"))
        return {
            "centroid_count": centroid_count,
            "dk_count": dk_count,
            "dk_status": "available" if dk_count > 0 else "pre-transition (omitted)",
            "total_parameters": centroid_count + dk_count,
        }

    def _count_centroid_values(self, centroids: Any) -> int:
        if not isinstance(centroids, list):
            return 0
        count = 0
        stack = list(centroids)
        while stack:
            value = stack.pop()
            if isinstance(value, list):
                stack.extend(value)
            else:
                count += 1
        return count

    def _count_dk_values(self, weights: Any) -> int:
        return self._count_centroid_values(weights)

    def _narrative(self, export: dict[str, Any]) -> str:
        supplier_count = len(export.get("supplier_profiles", []))
        centroid_count = int(export.get("centroid_count") or 0)
        dk_count = int(export.get("dk_count") or 0)
        if dk_count > 0:
            parameter_text = f"{centroid_count} centroid values + {dk_count} DK weights"
        else:
            parameter_text = f"{centroid_count} centroid values. DK weights not yet available (pre-transition)"
        return (
            f"Export: {parameter_text} + {supplier_count} supplier profiles. "
            "8 risk dimensions including environmental_risk for climate disruption, "
            "resource scarcity, and sustainability compliance. "
            f"Schema v{SCHEMA_VERSION}. Compatible with Gurobi, OR-Tools, AIMMS, Celonis."
        )
