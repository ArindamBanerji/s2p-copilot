from __future__ import annotations

import json
from pathlib import Path

from app.oracle.pipeline_test import (
    exp1_known_lift,
    exp2_zero_lift,
    exp3_floor_power,
    exp4_gate_rejects,
    exp5_conditional_coverage,
    run_all_experiments,
)


def test_exp1_known_lift() -> None:
    result = exp1_known_lift()

    assert result["pass"] is True
    assert 0.055 <= result["measured"] <= 0.105


def test_exp2_zero_lift() -> None:
    result = exp2_zero_lift()

    assert result["pass"] is True
    assert abs(result["measured"]) <= 0.025


def test_exp3_floor_power() -> None:
    result = exp3_floor_power()

    assert result["pass"] is True
    assert result["n_per_arm"] == 588


def test_exp4_gate_rejects() -> None:
    result = exp4_gate_rejects()

    assert result["pass"] is True
    assert result["gate_rejected"] is True
    assert result["accuracy_delta"] < 0.0


def test_exp5_conditional_coverage() -> None:
    result = exp5_conditional_coverage()

    assert result["pass"] is True
    assert result["enriched_pct"] == 0.6
    assert 0.075 <= result["effective_holdout_pct"] <= 0.105


def test_pipeline_results_artifact_shape(tmp_path: Path) -> None:
    results = {
        "status": "SDK_EXTRACTION_READY",
        "experiments": run_all_experiments(),
    }
    artifact = tmp_path / "buyer_oracle_plumb_results.json"

    artifact.write_text(json.dumps(results, indent=2), encoding="utf-8")
    loaded = json.loads(artifact.read_text(encoding="utf-8"))

    assert loaded["status"] == "SDK_EXTRACTION_READY"
    assert set(loaded["experiments"]) == {"exp1", "exp2", "exp3", "exp4", "exp5"}
    assert all(result["pass"] is True for result in loaded["experiments"].values())
