from __future__ import annotations

from copy import deepcopy
from typing import Any


class S2PShadowRunner:
    def run_batch(self, template: Any, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        copied_variant = deepcopy(variant)
        copied_decisions = deepcopy(decisions or [])
        evaluated = dict(template.evaluate_batch(copied_variant, copied_decisions))

        sample_size = int(evaluated.get("sample_size", 0) or 0)
        metric = float(evaluated.get("metric", 0.0) or 0.0)
        baseline = float(evaluated.get("baseline_metric", 0.0) or 0.0)
        better = sample_size > 0 and metric > baseline
        regression = sample_size > 0 and metric < baseline

        return {
            "variant_id": copied_variant.get("variant_id") or copied_variant.get("id"),
            "template_name": getattr(template, "name", copied_variant.get("template_name")),
            "better": better,
            "win": better,
            "accuracy": metric,
            "baseline_accuracy": baseline,
            "regression": regression,
            "sample_size": sample_size,
            "metric_name": evaluated.get("metric_name", getattr(template, "success_metric_name", "")),
            "details": evaluated,
        }
