"""Read-only S2P centroid explorer service."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, cast

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader


DOMAIN = "s2p"
SUPPLIER_ENRICHMENT_NAMESPACE = "s2p_supplier_metrics"


class CentroidExplorerError(ValueError):
    """Expected centroid explorer error suitable for router translation."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CentroidCell:
    category: str
    category_index: int
    action: str
    action_index: int
    factor_names: list[str]
    centroid_vector: list[float]
    source: str = "scorer_centroid"
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactorContribution:
    factor_name: str
    factor_index: int
    factor_value: float
    centroid_value: float
    distance: float
    dk_weight: float | None
    dk_status: str
    weighted_distance: float
    direction: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CentroidExplanation:
    decision_id: str
    category: str
    recommended_action: str
    closest_action: str
    closest_matches_recommendation: bool
    factor_names: list[str]
    factor_contributions: list[FactorContribution]
    centroid_distances: dict[str, float]
    summary: str
    dk_status: str
    p39_evidence: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["factor_contributions"] = [
            contribution.to_dict() for contribution in self.factor_contributions
        ]
        return payload


@dataclass(frozen=True)
class DriftResponse:
    category: str
    action: str
    supported: bool
    reason: str
    points: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class S2PCentroidExplorerService:
    """Build read-only centroid explorer payloads from public scorer/store APIs."""

    def __init__(
        self,
        *,
        scorer: Any,
        graph_store: Any | None = None,
        reader: S2PGraphReader | None = None,
        preset: type[S2PDomainConfig] = S2PDomainConfig,
    ) -> None:
        self.scorer = scorer
        self.graph_store = graph_store or getattr(scorer, "graph_store", None)
        self.reader = reader or S2PGraphReader(store=self.graph_store)
        self.preset = preset

    def get_all_centroid_cells(self) -> list[CentroidCell]:
        return [
            self.get_centroid_cell(category, action)
            for category in self.preset.categories
            for action in self.preset.actions
        ]

    def get_centroid_cell(self, category: str, action: str) -> CentroidCell:
        category_index = _index_or_error(self.preset.categories, category, "category")
        action_index = _index_or_error(self.preset.actions, action, "action")
        vector = _read_public_centroid(
            self.scorer,
            category,
            action,
            expected_len=self.preset.n_factors,
        )
        return CentroidCell(
            category=category,
            category_index=category_index,
            action=action,
            action_index=action_index,
            factor_names=list(self.preset.factors),
            centroid_vector=vector,
        )

    def explain_decision(self, decision_id: str) -> CentroidExplanation:
        decision = self._get_decision(decision_id)
        if decision is None:
            raise CentroidExplorerError(f"Decision not found: {decision_id}", status_code=404)
        return explain_decision(
            decision,
            self.scorer,
            self.preset,
            dk_weights=_call_or_none(getattr(self.scorer, "get_dk_weights", None)),
            p39_evidence=self._p39_evidence(decision),
        )

    def get_centroid_drift(self, category: str, action: str, *, limit: int = 50) -> DriftResponse:
        _index_or_error(self.preset.categories, category, "category")
        _index_or_error(self.preset.actions, action, "action")
        reader = getattr(self.graph_store, "get_centroid_checkpoints", None)
        if not callable(reader):
            return DriftResponse(
                category=category,
                action=action,
                supported=False,
                reason="centroid_history_unavailable",
                points=[],
            )
        try:
            checkpoints = reader(DOMAIN, category=category, limit=max(int(limit), 0))
        except Exception:
            return DriftResponse(
                category=category,
                action=action,
                supported=False,
                reason="centroid_history_unavailable",
                points=[],
            )
        if not isinstance(checkpoints, list) or not checkpoints:
            return DriftResponse(
                category=category,
                action=action,
                supported=True,
                reason="no centroid checkpoint history for category/action",
                points=[],
            )
        points = [
            point
            for checkpoint in checkpoints
            if (point := _checkpoint_point(checkpoint, self.preset, category, action)) is not None
        ]
        if not points:
            return DriftResponse(
                category=category,
                action=action,
                supported=False,
                reason="centroid_history_unavailable",
                points=[],
            )
        return DriftResponse(category=category, action=action, supported=True, reason="", points=points)

    def _get_decision(self, decision_id: str) -> dict[str, Any] | None:
        result = self.reader.get_decision(str(decision_id))
        return result if isinstance(result, dict) else None

    def _p39_evidence(self, decision: dict[str, Any]) -> dict[str, Any]:
        supplier_id = _supplier_id(decision)
        if not supplier_id:
            return {}
        reader = getattr(self.graph_store, "read_entity_enrichment", None)
        if not callable(reader):
            return {}
        try:
            values = reader(
                domain=DOMAIN,
                entity_type="Supplier",
                entity_id=supplier_id,
                namespace=SUPPLIER_ENRICHMENT_NAMESPACE,
            )
        except Exception:
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(name): _serialize_provenanced(value)
            for name, value in sorted(values.items())
            if hasattr(value, "source") and hasattr(value, "provenance_tier")
        }


def get_all_centroid_cells(
    scorer: Any,
    preset: type[S2PDomainConfig] = S2PDomainConfig,
) -> list[CentroidCell]:
    return S2PCentroidExplorerService(scorer=scorer, preset=preset).get_all_centroid_cells()


def get_centroid_cell(
    scorer: Any,
    preset: type[S2PDomainConfig],
    category: str,
    action: str,
) -> CentroidCell:
    return S2PCentroidExplorerService(scorer=scorer, preset=preset).get_centroid_cell(category, action)


def explain_decision(
    decision: dict[str, Any],
    scorer: Any,
    preset: type[S2PDomainConfig] = S2PDomainConfig,
    *,
    dk_weights: Any = None,
    p39_evidence: dict[str, Any] | None = None,
) -> CentroidExplanation:
    decision_id = str(decision.get("decision_id") or "")
    category = str(decision.get("category") or _metadata(decision).get("category") or "")
    _index_or_error(preset.categories, category, "category")
    recommended_action = str(
        decision.get("recommended_action")
        or decision.get("action")
        or _metadata(decision).get("recommended_action")
        or ""
    )
    _index_or_error(preset.actions, recommended_action, "action")
    factor_vector = _factor_vector(decision, preset)

    action_centroids = {
        action: _read_public_centroid(scorer, category, action, expected_len=preset.n_factors)
        for action in preset.actions
    }
    centroid_distances = {
        action: _rounded(_l2_distance(factor_vector, centroid))
        for action, centroid in action_centroids.items()
    }
    closest_action = min(
        preset.actions,
        key=lambda action: (centroid_distances[action], action),
    )
    closest_centroid = action_centroids[closest_action]
    category_index = list(preset.categories).index(category)
    dk_row = _dk_row(dk_weights, category_index, preset.n_factors)
    dk_status = "available" if dk_row is not None else "learning"
    display_weights = dk_row if dk_row is not None else [1.0 / preset.n_factors] * preset.n_factors

    contributions = [
        _factor_contribution(
            factor_name=factor_name,
            factor_index=index,
            factor_value=float(factor_vector[index]),
            centroid_value=float(closest_centroid[index]),
            dk_weight=dk_row[index] if dk_row is not None else None,
            display_weight=float(display_weights[index]),
            dk_status=dk_status,
        )
        for index, factor_name in enumerate(preset.factors)
    ]
    contributions.sort(key=lambda item: (-item.weighted_distance, item.factor_name))
    matches = closest_action == recommended_action
    return CentroidExplanation(
        decision_id=decision_id,
        category=category,
        recommended_action=recommended_action,
        closest_action=closest_action,
        closest_matches_recommendation=matches,
        factor_names=list(preset.factors),
        factor_contributions=contributions,
        centroid_distances=centroid_distances,
        summary=_summary(
            closest_action=closest_action,
            recommended_action=recommended_action,
            matches=matches,
            contributions=contributions,
        ),
        dk_status=dk_status,
        p39_evidence=dict(p39_evidence or {}),
    )


def get_centroid_drift(
    graph_store: Any,
    preset: type[S2PDomainConfig],
    category: str,
    action: str,
    *,
    limit: int = 50,
) -> DriftResponse:
    return S2PCentroidExplorerService(
        scorer=None,
        graph_store=graph_store,
        preset=preset,
    ).get_centroid_drift(category, action, limit=limit)


def _read_public_centroid(
    scorer: Any,
    category: str,
    action: str,
    *,
    expected_len: int,
) -> list[float]:
    reader = getattr(scorer, "get_centroid", None)
    if not callable(reader):
        raise CentroidExplorerError("Public scorer.get_centroid is unavailable", status_code=503)
    try:
        raw = reader(category, action)
    except ValueError as exc:
        raise CentroidExplorerError(str(exc), status_code=404) from exc
    except Exception as exc:
        raise CentroidExplorerError("Centroid read failed", status_code=503) from exc
    if raw is None:
        raise CentroidExplorerError("Centroid unavailable", status_code=503)
    try:
        vector = [float(value) for value in list(raw)]
    except (TypeError, ValueError) as exc:
        raise CentroidExplorerError("Centroid vector is not numeric", status_code=503) from exc
    vector = _pad_legacy_s2p_vector(vector, expected_len)
    if len(vector) != expected_len:
        raise CentroidExplorerError(
            f"Centroid vector length {len(vector)} != {expected_len}",
            status_code=503,
        )
    if not all(math.isfinite(value) for value in vector):
        raise CentroidExplorerError("Centroid vector contains non-finite values", status_code=503)
    return list(vector)


def _factor_vector(decision: dict[str, Any], preset: type[S2PDomainConfig]) -> list[float]:
    raw = decision.get("factor_vector")
    if raw is None:
        raw = _metadata(decision).get("factor_vector")
    if raw is None:
        raise CentroidExplorerError("Stored decision has no factor_vector", status_code=422)
    try:
        vector = [float(value) for value in list(raw)]
    except (TypeError, ValueError) as exc:
        raise CentroidExplorerError("Stored decision factor_vector is not numeric", status_code=422) from exc
    vector = _pad_legacy_s2p_vector(vector, preset.n_factors)
    if len(vector) != preset.n_factors:
        raise CentroidExplorerError(
            f"Stored decision factor_vector length {len(vector)} != {preset.n_factors}",
            status_code=422,
        )
    if not all(math.isfinite(value) for value in vector):
        raise CentroidExplorerError("Stored decision factor_vector contains non-finite values", status_code=422)
    return vector


def _pad_legacy_s2p_vector(vector: list[float], expected_len: int) -> list[float]:
    if expected_len == 8 and len(vector) == 7:
        return [*vector, 0.5]
    return vector


def _factor_contribution(
    *,
    factor_name: str,
    factor_index: int,
    factor_value: float,
    centroid_value: float,
    dk_weight: float | None,
    display_weight: float,
    dk_status: str,
) -> FactorContribution:
    distance = abs(factor_value - centroid_value)
    return FactorContribution(
        factor_name=factor_name,
        factor_index=factor_index,
        factor_value=_rounded(factor_value),
        centroid_value=_rounded(centroid_value),
        distance=_rounded(distance),
        dk_weight=_rounded(dk_weight) if dk_weight is not None else None,
        dk_status=dk_status,
        weighted_distance=_rounded(distance * display_weight),
        direction=_direction(factor_value, centroid_value),
    )


def _dk_row(dk_weights: Any, category_index: int, expected_len: int) -> list[float] | None:
    if dk_weights is None or isinstance(dk_weights, (str, bytes, dict)):
        return None
    try:
        rows = list(dk_weights)
    except TypeError:
        return None
    if not rows:
        return None
    row: Any
    if all(isinstance(item, list) for item in rows):
        if category_index < 0 or category_index >= len(rows):
            return None
        row = rows[category_index]
    else:
        row = rows
    try:
        values = [float(value) for value in list(row)]
    except (TypeError, ValueError):
        return None
    values = _pad_legacy_s2p_vector(values, expected_len)
    if len(values) != expected_len or not all(math.isfinite(value) for value in values):
        return None
    return values


def _checkpoint_point(
    checkpoint: Any,
    preset: type[S2PDomainConfig],
    category: str,
    action: str,
) -> dict[str, Any] | None:
    if not isinstance(checkpoint, dict):
        return None
    if checkpoint.get("category") not in (None, "", category):
        return None
    centroids = checkpoint.get("centroids")
    category_index = list(preset.categories).index(category)
    action_index = list(preset.actions).index(action)
    vector = _centroid_from_checkpoint(centroids, category_index, action_index, action)
    if vector is None:
        return None
    return {
        "checkpoint_id": checkpoint.get("checkpoint_id") or checkpoint.get("id"),
        "decision_id": checkpoint.get("decision_id"),
        "category": category,
        "action": action,
        "centroid_vector": [_rounded(value) for value in vector],
        "created_at": checkpoint.get("created_at"),
        "checkpoint_time": checkpoint.get("checkpoint_time"),
        "source": "graph_store.get_centroid_checkpoints",
    }


def _centroid_from_checkpoint(
    centroids: Any,
    category_index: int,
    action_index: int,
    action: str,
) -> list[float] | None:
    if hasattr(centroids, "tolist"):
        centroids = centroids.tolist()
    if isinstance(centroids, dict):
        raw = centroids.get(action)
        if raw is None:
            return None
    elif isinstance(centroids, list):
        try:
            raw = centroids[category_index][action_index]
        except (IndexError, TypeError):
            return None
    else:
        return None
    try:
        values = [float(value) for value in list(raw)]
    except (TypeError, ValueError):
        return None
    return values if values else None


def _summary(
    *,
    closest_action: str,
    recommended_action: str,
    matches: bool,
    contributions: list[FactorContribution],
) -> str:
    top = ", ".join(item.factor_name for item in contributions[:3]) or "none"
    if matches:
        return (
            f"This decision is closest to {closest_action} in learned centroid space. "
            f"Top distance contributors are {top}."
        )
    return (
        f"The learned centroid comparison is closest to {closest_action}, while the scorer "
        f"recommended {recommended_action}. Treat this as explanatory context, not a "
        f"replacement for the scorer decision. Top distance contributors are {top}."
    )


def _supplier_id(decision: dict[str, Any]) -> str:
    metadata = _metadata(decision)
    raw_factors = decision.get("factors")
    factors: dict[str, Any] = (
        cast(dict[str, Any], raw_factors) if isinstance(raw_factors, dict) else {}
    )
    for key in ("supplier_id", "supplierId", "vendor_id"):
        value = metadata.get(key) or factors.get(key) or decision.get(key)
        if value:
            return str(value)
    return ""


def _metadata(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    return cast(dict[str, Any], metadata) if isinstance(metadata, dict) else {}


def _serialize_provenanced(value: Any) -> dict[str, Any]:
    return {
        "value": getattr(value, "value", None),
        "source": getattr(value, "source", ""),
        "provenance_tier": getattr(value, "provenance_tier", ""),
        "source_count": getattr(value, "source_count", 0),
        "factor_eligible": getattr(value, "factor_eligible", False),
        "provenance_label": getattr(value, "provenance_label", ""),
        "measured": getattr(value, "measured", False),
        "verified": getattr(value, "verified", False),
        "computed_at": getattr(value, "computed_at", ""),
        "warnings": list(getattr(value, "warnings", []) or []),
    }


def _call_or_none(function: Any, *args: Any, **kwargs: Any) -> Any:
    if not callable(function):
        return None
    try:
        return function(*args, **kwargs)
    except Exception:
        return None


def _index_or_error(values: list[str], value: str, label: str) -> int:
    try:
        return list(values).index(value)
    except ValueError as exc:
        raise CentroidExplorerError(f"Unknown {label}: {value}", status_code=404) from exc


def _l2_distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right, strict=True)))


def _direction(factor_value: float, centroid_value: float) -> str:
    if math.isclose(factor_value, centroid_value, rel_tol=1e-9, abs_tol=1e-9):
        return "at_centroid"
    return "above_centroid" if factor_value > centroid_value else "below_centroid"


def _rounded(value: float | None) -> float:
    return round(float(value or 0.0), 6)
