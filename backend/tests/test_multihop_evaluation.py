from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import numpy as np

from scripts.evaluate_multihop_stage1 import (
    FACTOR_NAMES,
    RESULTS_PATH,
    ScenarioGraphStore,
    breadth,
    content_rule,
    evaluate_all,
    initialize_centroids,
    load_stage1,
    single_pass,
    surface_vector,
    vld,
    write_outputs,
)


def _scenarios() -> list[dict]:
    return list(load_stage1()["scenarios"])


def _scenario(kind: str | None = None, *, flat: bool | None = None, rho: float | None = None) -> dict:
    for scenario in _scenarios():
        if kind is not None and scenario["branching_kind"] != kind:
            continue
        if flat is not None and bool(scenario.get("surface_only_resolvable")) is not flat:
            continue
        if rho is not None and scenario.get("rho_planted") != rho:
            continue
        return dict(scenario)
    raise AssertionError("scenario not found")


def _centroids() -> np.ndarray:
    return cast(np.ndarray, initialize_centroids(_scenarios()))


def test_scenario_graph_store_loads_scenario_without_error() -> None:
    store = ScenarioGraphStore(_scenario("score_keyed"))
    assert store.correct_branches()


def test_scenario_graph_store_returns_different_correct_and_misleading_evidence() -> None:
    scenario = _scenario("score_keyed")
    store = ScenarioGraphStore(scenario)
    correct = store.get_evidence(store.correct_branches()[0])
    misleading = store.get_evidence(scenario["misleading_branches"][0])
    assert correct["kind"] == "correct"
    assert misleading["kind"] == "misleading"
    assert correct["factor_updates"] != misleading["factor_updates"]


def test_single_pass_uses_only_surface_factors() -> None:
    scenario = _scenario("score_keyed")
    result = single_pass(scenario, _centroids())
    assert result["trace"] == []
    assert len(result["final_vector"]) == len(FACTOR_NAMES) == 7
    np.testing.assert_allclose(result["final_vector"], surface_vector(scenario))


def test_breadth_reads_all_branches_up_to_budget() -> None:
    scenario = _scenario("score_keyed")
    result = breadth(scenario, _centroids())
    spent = sum(int(scenario["read_costs"][row["branch"]]) for row in result["trace"])
    assert spent <= int(scenario["budget"])
    assert len(result["trace"]) >= 1


def test_content_rule_uses_correct_branches() -> None:
    scenario = _scenario("score_keyed")
    result = content_rule(scenario, _centroids())
    assert [row["branch"] for row in result["trace"]] == ScenarioGraphStore(scenario).correct_branches()


def test_vld_produces_trace_with_at_least_one_step() -> None:
    result = vld(_scenario("score_keyed"), _centroids())
    assert len(result["trace"]) >= 1


def test_flat_control_vld_not_better_than_single_pass() -> None:
    results = evaluate_all()
    flat_sp = [row for row in results["rows"] if row["arm"] == "single_pass" and row["surface_only_resolvable"]]
    flat_vld = [row for row in results["rows"] if row["arm"] == "vld" and row["surface_only_resolvable"]]
    assert sum(row["correct"] for row in flat_vld) <= sum(row["correct"] for row in flat_sp)


def test_rho_050_vld_near_s2p_chance() -> None:
    results = evaluate_all()
    rows = [
        row
        for row in results["rows"]
        if row["arm"] == "vld" and row["branching_kind"] == "score_keyed" and row["rho_planted"] == 0.5
    ]
    accuracy = sum(row["correct"] for row in rows) / len(rows)
    assert 0.05 <= accuracy <= 0.35


def test_all_50_instances_evaluated_without_crash() -> None:
    results = evaluate_all()
    assert len({row["scenario_id"] for row in results["rows"]}) == 50
    assert len(results["rows"]) == 200


def test_results_json_has_expected_structure(tmp_path: Path) -> None:
    results = evaluate_all()
    results_path = tmp_path / "results.json"
    report_path = tmp_path / "report.md"
    write_outputs(results, results_path, report_path)
    rows = json.loads(results_path.read_text(encoding="utf-8"))
    assert len(rows) == 200
    assert {"scenario_id", "arm", "predicted_action", "correct", "trace"}.issubset(rows[0])
    assert report_path.read_text(encoding="utf-8").startswith("# S2P Multihop Stage 1 Evaluation")
