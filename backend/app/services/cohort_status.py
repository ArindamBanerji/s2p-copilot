"""S2P cohort day-zero state machine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from copilot_sdk.substantiation.cohort_day_zero import (
    STATES,
    BaseCohortDayZeroState,
    compute_state,
    evaluate_v7_gate as _sdk_evaluate_v7_gate,
)

from app.graph.s2p_graph_reader import S2PGraphReader


STATE_VALUES = frozenset(STATES)
REAL_PROVENANCE = "real"
SAMPLE_PROVENANCE = "sample"
ORACLE_PROVENANCE = "oracle"
TREATMENT_FLAG = "enrichment_shown"

POSITIVE_ACTIONS = frozenset(
    {
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
        "confirm",
        "confirmed",
        "accept",
        "accepted",
        "approve",
        "approved",
    }
)
NEGATIVE_ACTIONS = frozenset({"override", "overridden", "reject", "rejected", "dismiss", "dismissed"})


def evaluate_v7_gate(cohort_status: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper around the SDK v7.0 gate."""

    real = cohort_status.get("real", {})
    threshold_k = int(
        real.get("threshold_k", cohort_status.get("threshold_k", S2PCohortStatus.THRESHOLD_K))
    )
    gate_input = dict(real)
    gate_input["provenance"] = REAL_PROVENANCE
    records = cohort_status.get("records") or real.get("records")
    if records is not None:
        gate_input["records"] = records
    return cast(dict[str, Any], _sdk_evaluate_v7_gate(gate_input, threshold_k))


class S2PCohortStatus(BaseCohortDayZeroState):
    """S2P buyer-oracle cohort day-zero status."""

    DOMAIN = "s2p"
    THRESHOLD_K = 30

    def __init__(
        self,
        graph_store: Any | None = None,
        reader: S2PGraphReader | None = None,
        oracle_artifact_path: str | Path | None = None,
        decision_records: list[dict[str, Any]] | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._reader = reader or (
            S2PGraphReader(store=graph_store) if graph_store is not None else None
        )
        self._decision_records = decision_records
        self._oracle_artifact_path = (
            Path(oracle_artifact_path)
            if oracle_artifact_path is not None
            else Path(__file__).resolve().parents[2] / "buyer_oracle_plumb_results.json"
        )

    def _load_instrument(self) -> dict[str, Any]:
        result = {
            "validated": False,
            "provenance": ORACLE_PROVENANCE,
            "source_artifact": str(self._oracle_artifact_path),
            "experiments": [],
        }
        if not self._oracle_artifact_path.exists():
            return result
        try:
            data = json.loads(self._oracle_artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return result
        experiments = _extract_experiments(data)
        result["experiments"] = experiments
        explicit_validated = data.get("validated") if isinstance(data, dict) else False
        result["validated"] = bool(explicit_validated) or (
            bool(experiments) and all(bool(exp.get("pass")) for exp in experiments)
        )
        return result

    def _count_real_cohorts(self) -> dict[str, Any]:
        treatment_n, control_n = _count_arms(self._records_with_provenance(REAL_PROVENANCE))
        return {"treatment_n": treatment_n, "control_n": control_n}

    def _count_structure_cohorts(self) -> dict[str, Any]:
        sample_records = self._records_with_provenance(SAMPLE_PROVENANCE)
        treatment_n, control_n = _count_arms(sample_records)
        total = treatment_n + control_n
        split_balanced = None
        if total:
            split_balanced = abs(treatment_n - control_n) <= max(1, int(total * 0.1))
        return {
            "present": total > 0,
            "treatment_n": treatment_n,
            "control_n": control_n,
            "split_balanced": split_balanced,
            "join_ok": True if total else None,
            "provenance": SAMPLE_PROVENANCE,
        }

    def _compute_real_lift(self) -> float:
        """Compute lift from provenance=='real' decisions only."""

        records = self._records_with_provenance(REAL_PROVENANCE)
        counts = {"treatment": 0, "control": 0}
        positives = {"treatment": 0, "control": 0}
        for record in records:
            provenance = _record_provenance(record)
            if provenance != REAL_PROVENANCE:
                raise ValueError("sample/oracle cohorts are forbidden in real magnitude")
            arm = _cohort_arm(record)
            if arm not in counts:
                continue
            counts[arm] += 1
            if _is_positive_outcome(record):
                positives[arm] += 1
        if counts["treatment"] == 0 or counts["control"] == 0:
            return 0.0
        return round(
            positives["treatment"] / counts["treatment"]
            - positives["control"] / counts["control"],
            6,
        )

    def _records_with_provenance(self, provenance: str) -> list[dict[str, Any]]:
        return [
            record
            for record in self._read_decisions()
            if _record_provenance(record) == provenance
        ]

    def _read_decisions(self) -> list[dict[str, Any]]:
        if self._decision_records is not None:
            return [dict(record) for record in self._decision_records]
        if self._reader is None:
            return []
        return [dict(record) for record in self._reader.get_verified_decisions()]


def _extract_experiments(data: Any) -> list[dict[str, Any]]:
    raw = data.get("experiments") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raw = []
    experiments: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        experiments.append(
            {
                "name": str(item.get("name") or item.get("experiment") or f"experiment_{index + 1}"),
                "injected_lift": _nullable_float(item.get("injected_lift", item.get("expected_lift"))),
                "recovered_lift": _nullable_float(item.get("recovered_lift", item.get("measured_lift"))),
                "pass": bool(item.get("pass", item.get("passed", False))),
            }
        )
    return experiments


def _count_arms(records: list[dict[str, Any]]) -> tuple[int, int]:
    treatment_n = 0
    control_n = 0
    for record in records:
        arm = _cohort_arm(record)
        if arm == "treatment":
            treatment_n += 1
        elif arm == "control":
            control_n += 1
    return treatment_n, control_n


def _record_provenance(record: dict[str, Any]) -> str:
    value = _nested_value(record, "provenance", "provenance_tier")
    return str(value).casefold() if value is not None else ""


def _cohort_arm(record: dict[str, Any]) -> str | None:
    explicit = _nested_value(record, "holdout_group", "cohort", "cohort_group")
    if explicit is not None:
        normalized = str(explicit).casefold()
        if normalized in {"treatment", "shown", TREATMENT_FLAG}:
            return "treatment"
        if normalized in {"control", "suppressed"}:
            return "control"
    shown = _nested_value(record, TREATMENT_FLAG, "supplier_shown", "treatment", "shown")
    if shown is None:
        return None
    return "treatment" if _truthy(shown) else "control"


def _is_positive_outcome(record: dict[str, Any]) -> bool:
    for key in ("actual_action", "analyst_action", "action", "outcome"):
        action = _nested_value(record, key)
        if action is None:
            continue
        normalized = str(action).casefold()
        if normalized in POSITIVE_ACTIONS:
            return True
        if normalized in NEGATIVE_ACTIONS:
            return False
    value = _nested_value(record, "is_correct", "correct")
    return bool(value) if value is not None else False


def _nested_value(record: dict[str, Any], *keys: str) -> Any:
    containers: list[Any] = [record]
    for container_key in ("metadata", "context", "outcome_metadata", "factors"):
        nested = record.get(container_key)
        if isinstance(nested, dict):
            containers.append(nested)
            if isinstance(nested.get("context"), dict):
                containers.append(nested["context"])
            if isinstance(nested.get("metadata"), dict):
                containers.append(nested["metadata"])
    context_json = record.get("context_json")
    if isinstance(context_json, str):
        try:
            parsed = json.loads(context_json)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            containers.append(parsed)
    for container in containers:
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    return container[key]
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.casefold() in {"1", "true", "yes", "y", "shown", "treatment"}
    return bool(value)


def _nullable_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


CohortStatusService = S2PCohortStatus
