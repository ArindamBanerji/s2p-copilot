from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routers.s2p_enrichment import EnrichedSupplierResponse
from app.services.cohort_status import (
    STATE_VALUES,
    CohortStatusService,
    S2PCohortStatus,
    compute_state,
    evaluate_v7_gate,
)


client = TestClient(app)


def _records(
    *,
    provenance: str,
    treatment_n: int,
    control_n: int,
    treatment_positive: int | None = None,
    control_positive: int | None = None,
) -> list[dict]:
    treatment_positive = treatment_n if treatment_positive is None else treatment_positive
    control_positive = control_n if control_positive is None else control_positive
    records: list[dict] = []
    for index in range(treatment_n):
        records.append(
            {
                "decision_id": f"{provenance}-t-{index}",
                "provenance": provenance,
                "metadata": {"enrichment_shown": True},
                "actual_action": "auto_approve" if index < treatment_positive else "override",
            }
        )
    for index in range(control_n):
        records.append(
            {
                "decision_id": f"{provenance}-c-{index}",
                "provenance": provenance,
                "metadata": {"enrichment_shown": False},
                "actual_action": "auto_approve" if index < control_positive else "override",
            }
        )
    return records


def _artifact(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "validated": True,
                "experiments": [
                    {
                        "name": "buyer_oracle_known_lift",
                        "injected_lift": 0.08,
                        "recovered_lift": 0.079,
                        "pass": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_t1_sample_only_no_lift():
    status = CohortStatusService(
        decision_records=_records(provenance="sample", treatment_n=30, control_n=30)
    ).get_status()

    assert status["state"] == "INSTRUMENT_VALIDATED"
    assert status["real"]["magnitude"] is None
    assert status["real"]["status"] == "pending"


def test_t2_lift_query_filters_provenance():
    source = inspect.getsource(S2PCohortStatus._compute_real_lift)

    assert "provenance" in source
    assert "REAL_PROVENANCE" in source
    assert "sample" in source
    assert "oracle" in source


def test_t3_one_real_below_k():
    status = CohortStatusService(
        decision_records=_records(provenance="real", treatment_n=1, control_n=0)
    ).get_status()

    assert status["state"] == "ACCUMULATING"
    assert status["real"]["magnitude"] is None
    assert status["real"]["treatment_n"] == 1


def test_t4_real_above_k_both_arms():
    status = CohortStatusService(
        decision_records=_records(
            provenance="real",
            treatment_n=30,
            control_n=30,
            treatment_positive=24,
            control_positive=12,
        )
        + _records(
            provenance="sample",
            treatment_n=100,
            control_n=100,
            treatment_positive=0,
            control_positive=100,
        )
    ).get_status()

    assert status["state"] == "MEASURED"
    assert status["real"]["magnitude"] == 0.4


def test_t5_instrument_present_at_every_state(tmp_path):
    artifact = _artifact(tmp_path / "buyer_oracle_plumb_results.json")
    cases = [
        _records(provenance="sample", treatment_n=5, control_n=5),
        _records(provenance="real", treatment_n=1, control_n=0),
        _records(provenance="real", treatment_n=30, control_n=30),
    ]

    for records in cases:
        status = CohortStatusService(
            decision_records=records,
            oracle_artifact_path=artifact,
        ).get_status()
        assert status["instrument"]["provenance"] == "oracle"
        assert status["instrument"]["validated"] is True
        assert status["instrument"]["experiments"]


def test_structure_never_moves_state():
    status = CohortStatusService(
        decision_records=_records(provenance="sample", treatment_n=250, control_n=250)
    ).get_status()

    assert status["state"] == "INSTRUMENT_VALIDATED"
    assert status["structure"]["present"] is True
    assert status["real"]["magnitude"] is None


def test_v7_gate_abstains_below_threshold():
    result = evaluate_v7_gate(
        {"real": {"treatment_n": 3, "control_n": 2, "threshold_k": 30}}
    )

    assert result["status"] == "awaiting_real_cohorts"
    assert result["magnitude"] is None


def test_v7_gate_rejects_non_real():
    try:
        evaluate_v7_gate(
            {
                "real": {"treatment_n": 30, "control_n": 30, "threshold_k": 30},
                "records": [{"provenance": "sample", "enrichment_shown": True}],
            }
        )
    except ValueError as exc:
        assert "real" in str(exc)
    else:
        raise AssertionError("non-real gate input must raise")


def test_endpoint_returns_200():
    response = client.get("/api/s2p/cohort-status")

    assert response.status_code == 200
    data = response.json()
    assert sorted(data.keys()) == ["instrument", "real", "state", "structure"]
    assert data["state"] in STATE_VALUES


def test_enrichment_shown_flag_recorded():
    response = EnrichedSupplierResponse(
        supplier_id="SUP-001",
        namespace="s2p_supplier_enrichment",
        metrics={},
    )

    assert response.enrichment_shown is True


def test_oracle_artifact_missing_graceful(tmp_path):
    status = CohortStatusService(oracle_artifact_path=tmp_path / "missing.json").get_status()

    assert status["instrument"]["validated"] is False
    assert status["instrument"]["experiments"] == []
    assert status["state"] == "INSTRUMENT_VALIDATED"


def test_state_machine_values():
    assert STATE_VALUES == {"INSTRUMENT_VALIDATED", "ACCUMULATING", "MEASURED"}
    assert compute_state(0, 0, 30) == "INSTRUMENT_VALIDATED"
    assert compute_state(1, 0, 30) == "ACCUMULATING"
    assert compute_state(30, 30, 30) == "MEASURED"
