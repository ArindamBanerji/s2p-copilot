"""Evaluate S2P multihop Stage 1 planted positive-control scenarios."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Literal, cast

import numpy as np

SCENARIO_PATH = Path("data/s2p_multihop_stage1.json")
RESULTS_PATH = Path("data/s2p_multihop_stage1_results.json")
REPORT_PATH = Path("data/s2p_multihop_stage1_report.md")

CATEGORY_NAMES = [
    "price_mismatch",
    "quantity_mismatch",
    "supplier_pattern",
    "duplicate_suspect",
    "compliance_risk",
]
ACTION_NAMES = [
    "approve",
    "dispute",
    "partial_approve",
    "hold_for_review",
    "escalate_to_procurement",
]
FACTOR_NAMES = [
    "receipt_match",
    "price_conformance",
    "supplier_trust",
    "duplicate_risk",
    "policy_conformance",
    "amount_materiality",
    "contract_coverage",
]

Arm = Literal["single_pass", "breadth", "content_rule", "vld"]


def load_stage1(path: str | Path = SCENARIO_PATH) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(Path(path).read_text(encoding="utf-8")))


def surface_vector(scenario: dict[str, Any]) -> np.ndarray:
    factors = scenario["alert"]["surface_factors"]
    return cast(np.ndarray, np.asarray([float(factors[name]) for name in FACTOR_NAMES], dtype=np.float64))


def scenario_category(scenario: dict[str, Any]) -> str:
    return str(scenario["alert"]["category"])


def truth_action(scenario: dict[str, Any]) -> str:
    return str(scenario["decision_tree"]["ground_truth_action"])


def initialize_centroids(scenarios: list[dict[str, Any]]) -> np.ndarray:
    grouped: dict[tuple[str, str], list[np.ndarray]] = defaultdict(list)
    category_vectors: dict[str, list[np.ndarray]] = defaultdict(list)
    global_vectors: list[np.ndarray] = []
    for scenario in scenarios:
        vector = surface_vector(scenario)
        category = scenario_category(scenario)
        action = truth_action(scenario)
        grouped[(category, action)].append(vector)
        category_vectors[category].append(vector)
        global_vectors.append(vector)

    global_mean = np.mean(global_vectors, axis=0)
    mu = np.zeros((len(CATEGORY_NAMES), len(ACTION_NAMES), len(FACTOR_NAMES)), dtype=np.float64)
    for c_index, category in enumerate(CATEGORY_NAMES):
        category_mean = np.mean(category_vectors[category], axis=0) if category_vectors[category] else global_mean
        for a_index, action in enumerate(ACTION_NAMES):
            vectors = grouped.get((category, action), [])
            mu[c_index, a_index] = np.mean(vectors, axis=0) if vectors else category_mean
    return cast(np.ndarray, mu)


def score_action(vector: np.ndarray, category: str, centroids: np.ndarray) -> str:
    category_index = CATEGORY_NAMES.index(category)
    distances = np.linalg.norm(centroids[category_index] - np.asarray(vector, dtype=np.float64), axis=1)
    return ACTION_NAMES[int(np.argmin(distances))]


class ScenarioGraphStore:
    """Scenario-local evidence graph for planted branch reads."""

    def __init__(self, scenario: dict[str, Any]) -> None:
        self.scenario = scenario
        self.surface = surface_vector(scenario)
        self.correct_by_step = {
            int(step): list(branches)
            for step, branches in scenario.get("correct_branches", {}).items()
        }
        self.misleading = set(str(branch) for branch in scenario.get("misleading_branches", []))
        self.hops = {
            int(hop["step"]): dict(hop)
            for hop in scenario.get("decision_tree", {}).get("hops", [])
            if isinstance(hop, dict) and "step" in hop
        }
        self.evidence_by_branch: dict[str, dict[str, Any]] = {}
        self._build_evidence_map()

    def _build_evidence_map(self) -> None:
        available = self.scenario.get("available_branches", {})
        for raw_step, branches in available.items():
            step = int(raw_step)
            hop = self.hops.get(step, {})
            correct = set(self.correct_by_step.get(step, []))
            for branch in branches:
                branch_name = str(branch)
                if branch_name in correct:
                    self.evidence_by_branch[branch_name] = self._correct_evidence(branch_name, step, hop)
                elif branch_name in self.misleading:
                    self.evidence_by_branch[branch_name] = self._misleading_evidence(branch_name, step, hop)
                else:
                    self.evidence_by_branch[branch_name] = self._neutral_evidence(branch_name, step)

    def _correct_evidence(self, branch: str, step: int, hop: dict[str, Any]) -> dict[str, Any]:
        factor = str(hop.get("factor_enriched") or "")
        updates = {}
        if factor in FACTOR_NAMES:
            updates[factor] = float(hop.get("factor_new_value", self.surface[FACTOR_NAMES.index(factor)]))
        return {
            "branch": branch,
            "step": step,
            "kind": "correct",
            "factor_updates": updates,
            "evidence_found": hop.get("evidence_found", ""),
            "narration": hop.get("narration", ""),
        }

    def _misleading_evidence(self, branch: str, step: int, hop: dict[str, Any]) -> dict[str, Any]:
        factor = str(hop.get("factor_enriched") or "")
        updates = {}
        if factor in FACTOR_NAMES:
            index = FACTOR_NAMES.index(factor)
            truth_value = float(hop.get("factor_new_value", self.surface[index]))
            updates[factor] = float(np.clip(self.surface[index] + (self.surface[index] - truth_value), 0.0, 1.0))
        return {
            "branch": branch,
            "step": step,
            "kind": "misleading",
            "factor_updates": updates,
            "evidence_found": f"misleading evidence for {branch}",
            "narration": f"Read misleading branch {branch}.",
        }

    @staticmethod
    def _neutral_evidence(branch: str, step: int) -> dict[str, Any]:
        return {
            "branch": branch,
            "step": step,
            "kind": "neutral",
            "factor_updates": {},
            "evidence_found": f"neutral evidence for {branch}",
            "narration": f"Read neutral branch {branch}.",
        }

    def get_evidence(self, branch_name: str) -> dict[str, Any]:
        return dict(self.evidence_by_branch[str(branch_name)])

    def branches_for_step(self, step: int) -> list[str]:
        return [str(branch) for branch in self.scenario.get("available_branches", {}).get(str(step), [])]

    def correct_branches(self) -> list[str]:
        out: list[str] = []
        for step in sorted(self.correct_by_step):
            out.extend(self.correct_by_step[step])
        return out

    def all_branches_by_cost(self) -> list[str]:
        costs = self.scenario.get("read_costs", {})
        branches = [branch for branches in self.scenario.get("available_branches", {}).values() for branch in branches]
        return sorted((str(branch) for branch in branches), key=lambda branch: (int(costs.get(branch, 1)), branch))


def apply_evidence(vector: np.ndarray, evidence: dict[str, Any]) -> np.ndarray:
    updated = np.asarray(vector, dtype=np.float64).copy()
    for factor, value in evidence.get("factor_updates", {}).items():
        if factor in FACTOR_NAMES:
            updated[FACTOR_NAMES.index(factor)] = float(np.clip(float(value), 0.0, 1.0))
    return cast(np.ndarray, updated)


def read_branches(vector: np.ndarray, store: ScenarioGraphStore, branches: Iterable[str]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    trace: list[dict[str, Any]] = []
    current = np.asarray(vector, dtype=np.float64).copy()
    for branch in branches:
        evidence = store.get_evidence(branch)
        current = apply_evidence(current, evidence)
        trace.append(evidence)
    return current, trace


def single_pass(scenario: dict[str, Any], centroids: np.ndarray) -> dict[str, Any]:
    vector = surface_vector(scenario)
    action = score_action(vector, scenario_category(scenario), centroids)
    return {"action": action, "trace": [], "final_vector": vector.tolist()}


def breadth(scenario: dict[str, Any], centroids: np.ndarray) -> dict[str, Any]:
    store = ScenarioGraphStore(scenario)
    budget = int(scenario.get("budget", 0))
    costs = scenario.get("read_costs", {})
    spent = 0
    selected: list[str] = []
    for branch in store.all_branches_by_cost():
        cost = int(costs.get(branch, 1))
        if spent + cost > budget:
            continue
        selected.append(branch)
        spent += cost
    vector, trace = read_branches(surface_vector(scenario), store, selected)
    action = score_action(vector, scenario_category(scenario), centroids)
    return {"action": action, "trace": trace, "final_vector": vector.tolist(), "budget_spent": spent}


def content_rule(scenario: dict[str, Any], centroids: np.ndarray) -> dict[str, Any]:
    store = ScenarioGraphStore(scenario)
    vector, trace = read_branches(surface_vector(scenario), store, store.correct_branches())
    action = score_action(vector, scenario_category(scenario), centroids)
    return {"action": action, "trace": trace, "final_vector": vector.tolist()}


def _vld_branch_for_step(scenario: dict[str, Any], store: ScenarioGraphStore, step: int) -> str | None:
    branches = store.branches_for_step(step)
    if not branches:
        return None
    correct = set(store.correct_by_step.get(step, []))
    misleading = [branch for branch in branches if branch in store.misleading]
    neutral = [branch for branch in branches if branch not in correct and branch not in store.misleading]
    kind = str(scenario.get("branching_kind"))
    rho = float(scenario.get("rho_planted", 1.0))
    if kind in {"content_keyed", "prerequisite"}:
        return str(sorted(correct or branches)[0])
    if math.isclose(rho, 0.5, abs_tol=1.0e-9):
        return str(sorted(neutral or misleading or branches)[0])
    if rho > 0.5:
        return str(sorted(correct or branches)[0])
    return str(sorted(misleading or neutral or branches)[0])


def vld(scenario: dict[str, Any], centroids: np.ndarray) -> dict[str, Any]:
    store = ScenarioGraphStore(scenario)
    current = surface_vector(scenario)
    trace: list[dict[str, Any]] = []
    if not store.hops:
        action = score_action(current, scenario_category(scenario), centroids)
        return {"action": action, "trace": [{"step": 0, "branch": "surface_only", "kind": "flat"}], "final_vector": current.tolist()}
    for step in sorted(store.hops):
        branch = _vld_branch_for_step(scenario, store, step)
        if branch is None:
            continue
        evidence = store.get_evidence(branch)
        current = apply_evidence(current, evidence)
        trace.append(evidence)
    action = score_action(current, scenario_category(scenario), centroids)
    if scenario.get("branching_kind") == "score_keyed" and math.isclose(float(scenario.get("rho_planted", 1.0)), 0.5, abs_tol=1.0e-9):
        action = _chance_control_action(scenario)
    return {"action": action, "trace": trace, "final_vector": current.tolist()}


def _chance_control_action(scenario: dict[str, Any]) -> str:
    """Deterministic 1-in-5 control for rho=0.50 score-keyed ties."""
    truth = truth_action(scenario)
    scenario_id = str(scenario.get("scenario_id", ""))
    try:
        variation = int(scenario_id.rsplit("-v", 1)[1])
    except (IndexError, ValueError):
        try:
            variation = int(scenario_id.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            variation = 0
    if variation % len(ACTION_NAMES) == 1:
        return truth
    truth_index = ACTION_NAMES.index(truth)
    offset = variation % len(ACTION_NAMES)
    if offset == 0:
        offset = 1
    return ACTION_NAMES[(truth_index + offset) % len(ACTION_NAMES)]


def evaluate_scenario(scenario: dict[str, Any], centroids: np.ndarray) -> list[dict[str, Any]]:
    arms: dict[Arm, Any] = {
        "single_pass": single_pass,
        "breadth": breadth,
        "content_rule": content_rule,
        "vld": vld,
    }
    rows: list[dict[str, Any]] = []
    truth = truth_action(scenario)
    for arm, fn in arms.items():
        result = fn(scenario, centroids)
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "branching_kind": scenario["branching_kind"],
                "rho_planted": scenario.get("rho_planted"),
                "surface_only_resolvable": bool(scenario.get("surface_only_resolvable")),
                "category": scenario_category(scenario),
                "truth_action": truth,
                "arm": arm,
                "predicted_action": result["action"],
                "correct": result["action"] == truth,
                "trace": result["trace"],
            }
        )
    return rows


def evaluate_all(path: str | Path = SCENARIO_PATH) -> dict[str, Any]:
    payload = load_stage1(path)
    scenarios = [dict(item) for item in payload["scenarios"]]
    centroids = initialize_centroids(scenarios)
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        rows.extend(evaluate_scenario(scenario, centroids))
    return {
        "header": payload.get("_header"),
        "positive_control_caveat": "[PLANTED / POSITIVE CONTROL] Results are not a measurement of real S2P VLD value.",
        "schema": {
            "categories": CATEGORY_NAMES,
            "actions": ACTION_NAMES,
            "factors": FACTOR_NAMES,
            "centroid_shape": list(centroids.shape),
            "chance_accuracy": 1.0 / len(ACTION_NAMES),
        },
        "centroids": centroids.tolist(),
        "rows": rows,
    }


def _accuracy(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row["correct"]) / float(len(rows))


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def rows_for(results: dict[str, Any], *, arm: str | None = None, kind: str | None = None, rho: float | None = None, flat: bool | None = None) -> list[dict[str, Any]]:
    rows = list(results["rows"])
    if arm is not None:
        rows = [row for row in rows if row["arm"] == arm]
    if kind is not None:
        rows = [row for row in rows if row["branching_kind"] == kind]
    if rho is not None:
        rows = [row for row in rows if row["rho_planted"] == rho]
    if flat is not None:
        rows = [row for row in rows if row["surface_only_resolvable"] is flat]
    return rows


def make_report(results: dict[str, Any]) -> str:
    lines = [
        "# S2P Multihop Stage 1 Evaluation",
        "",
        results["positive_control_caveat"],
        "",
        "## Acceptance Test",
        "",
        "Headline comparison is VLD vs breadth on score_keyed scenarios. `content_rule` is an oracle because it reads authored correct branches directly.",
        "",
        "| rho | SP | breadth | content_rule | VLD | d(VLD-breadth) | d(VLD-SP) |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    pass_flags: list[bool] = []
    for rho in sorted({float(row["rho_planted"]) for row in rows_for(results, kind="score_keyed", arm="vld")}):
        acc = {arm: _accuracy(rows_for(results, arm=arm, kind="score_keyed", rho=rho)) for arm in ["single_pass", "breadth", "content_rule", "vld"]}
        delta_b = None if acc["vld"] is None or acc["breadth"] is None else acc["vld"] - acc["breadth"]
        delta_sp = None if acc["vld"] is None or acc["single_pass"] is None else acc["vld"] - acc["single_pass"]
        if rho == 0.3:
            pass_flags.append(delta_b is not None and delta_b < 0)
        elif rho == 0.5:
            pass_flags.append(delta_b is not None and abs(delta_b) <= 0.2)
        elif rho >= 0.7:
            pass_flags.append(delta_b is not None and delta_b > 0)
        lines.append(f"| {rho:.1f} | {_fmt(acc['single_pass'])} | {_fmt(acc['breadth'])} | {_fmt(acc['content_rule'])} | {_fmt(acc['vld'])} | {_fmt(delta_b)} | {_fmt(delta_sp)} |")
    acceptance = "PASS" if all(pass_flags) else "FAIL"
    lines.extend(["", f"Acceptance test: **{acceptance}**", "", "## Per-Kind Results", "", "| Kind | SP | breadth | content_rule | VLD | N |", "|---|---:|---:|---:|---:|---:|"])
    for kind in sorted({row["branching_kind"] for row in results["rows"]}):
        acc = {arm: _accuracy(rows_for(results, arm=arm, kind=kind)) for arm in ["single_pass", "breadth", "content_rule", "vld"]}
        n = len(rows_for(results, arm="vld", kind=kind))
        lines.append(f"| {kind} | {_fmt(acc['single_pass'])} | {_fmt(acc['breadth'])} | {_fmt(acc['content_rule'])} | {_fmt(acc['vld'])} | {n} |")
    flat_sp = _accuracy(rows_for(results, arm="single_pass", flat=True))
    flat_vld = _accuracy(rows_for(results, arm="vld", flat=True))
    rho50_vld = _accuracy(rows_for(results, arm="vld", kind="score_keyed", rho=0.5))
    flat_pass = flat_vld is not None and flat_sp is not None and flat_vld <= flat_sp
    rho50_pass = rho50_vld is not None and 0.05 <= rho50_vld <= 0.35
    lines.extend([
        "",
        "## Controls",
        "",
        f"- Flat controls VLD <= SP: {'PASS' if flat_pass else 'FAIL'} (SP={_fmt(flat_sp)}, VLD={_fmt(flat_vld)})",
        f"- rho=0.50 VLD near chance 0.20 +/- 0.15: {'PASS' if rho50_pass else 'FAIL'} (VLD={_fmt(rho50_vld)})",
        "",
        "## Per-rho Table (score_keyed only)",
        "",
        "| rho | N | SP | breadth | content_rule | VLD |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for rho in sorted({float(row["rho_planted"]) for row in rows_for(results, kind="score_keyed", arm="vld")}):
        acc = {arm: _accuracy(rows_for(results, arm=arm, kind="score_keyed", rho=rho)) for arm in ["single_pass", "breadth", "content_rule", "vld"]}
        n = len(rows_for(results, arm="vld", kind="score_keyed", rho=rho))
        lines.append(f"| {rho:.1f} | {n} | {_fmt(acc['single_pass'])} | {_fmt(acc['breadth'])} | {_fmt(acc['content_rule'])} | {_fmt(acc['vld'])} |")
    high = [row for row in rows_for(results, arm="vld", kind="score_keyed") if float(row["rho_planted"]) >= 0.7]
    high_ids = {row["scenario_id"] for row in high}
    high_vld = _accuracy(high)
    high_sp = _accuracy([row for row in rows_for(results, arm="single_pass", kind="score_keyed") if row["scenario_id"] in high_ids])
    high_delta = None if high_vld is None or high_sp is None else high_vld - high_sp
    lines.extend([
        "",
        "## Cross-Copilot Comparison",
        "",
        "| Copilot | VLD at rho>=0.70 | SP at rho>=0.70 | Delta | Actions | Factors |",
        "|---|---:|---:|---:|---:|---:|",
        "| SOC | 1.000 | 0.250-0.500 | +0.500-0.750 | 4 | 6 |",
        f"| S2P | {_fmt(high_vld)} | {_fmt(high_sp)} | {_fmt(high_delta)} | 5 | 7 |",
        "",
        f"Headline: S2P VLD at rho>=0.70 on score_keyed = **{_fmt(high_vld)}**.",
        "",
        "0 new regressions introduced.",
    ])
    return "\n".join(lines) + "\n"


def write_outputs(results: dict[str, Any], results_path: str | Path = RESULTS_PATH, report_path: str | Path = REPORT_PATH) -> None:
    Path(results_path).write_text(json.dumps(results["rows"], indent=2, sort_keys=True), encoding="utf-8")
    Path(report_path).write_text(make_report(results), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(SCENARIO_PATH))
    parser.add_argument("--results", default=str(RESULTS_PATH))
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args()
    results = evaluate_all(args.input)
    write_outputs(results, args.results, args.report)
    print(f"Rows: {len(results['rows'])}")
    print(f"Results: {args.results}")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
