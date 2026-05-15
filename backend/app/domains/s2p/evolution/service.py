from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from copilot_sdk.evolution import (
    AutonomousPromotionGate,
    ContextAwareSelector,
    PromotionDecision,
    SelectionContext,
)

from app.domains.s2p.evolution.rule_templates import RuleTemplate, get_s2p_rules
from app.domains.s2p.evolution.shadow_runner import S2PShadowRunner


def _default_fixture_path() -> Path:
    return Path(__file__).resolve().parents[4] / "data" / "s2p_evolution_fixture.json"


class S2PEvolutionService:
    def __init__(self, scorer: Any, fixture_path: str | os.PathLike[str] | None = None):
        self.scorer = scorer
        self.rules: list[RuleTemplate] = get_s2p_rules()
        self.runner = S2PShadowRunner()
        self.selector = ContextAwareSelector()
        self.gate = AutonomousPromotionGate(min_shadow_batches=3, min_win_rate=0.7)
        self.fixture_path = Path(fixture_path or os.environ.get("S2P_EVOLUTION_FIXTURE") or _default_fixture_path())
        self.fixture = self._load_fixture(self.fixture_path)
        self.variants = self._load_variants()
        self.shadow_results = self._load_shadow_results()
        self.promoted = deepcopy(self.fixture.get("promoted") or {})

    def get_rules(self) -> list[dict[str, Any]]:
        fixture_rules = {
            str(item.get("name")): item
            for item in self.fixture.get("rule_templates", [])
            if isinstance(item, dict)
        }
        rows = []
        for rule in self.rules:
            fixture_row = dict(fixture_rules.get(rule.name) or {})
            rows.append(
                {
                    "name": rule.name,
                    "success_metric_name": rule.success_metric_name,
                    "applicable_categories": list(rule.applicable_categories),
                    "variant_count": len(self.get_variants(rule.name)),
                    **fixture_row,
                }
            )
        return rows

    def get_variants(self, template_name: str | None = None) -> list[dict[str, Any]]:
        variants = [deepcopy(item) for item in self.variants]
        if template_name:
            variants = [item for item in variants if item.get("template_name") == template_name]
        return variants

    def select_variant(self, template_name: str, category: str) -> dict[str, Any]:
        candidates = [
            item
            for item in self.get_variants(template_name)
            if item.get("category") == category or category in (item.get("categories") or [])
        ]
        if not candidates:
            raise ValueError(f"No variants for template={template_name!r}, category={category!r}")
        context = SelectionContext(
            category=category,
            recent_accuracy=self._recent_accuracy(candidates),
            conservation_phase=self._selector_phase(),
            decision_count=max(int(max((item.get("sample_size", 0) or 0) for item in candidates)), 10),
        )
        return deepcopy(self.selector.select(candidates, context))

    def run_shadow_batch(
        self,
        template_name: str,
        variant_id: str,
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rule = self._rule(template_name)
        variant = self._variant(variant_id)
        result = self.runner.run_batch(rule, variant, decisions)
        self.shadow_results.setdefault(variant_id, []).append(deepcopy(result))
        return result

    def evaluate_promotion(self, template_name: str, variant_id: str) -> PromotionDecision:
        variant = self._variant(variant_id)
        status = str(self.fixture.get("conservation_status") or "AMBER")
        if not bool(self.fixture.get("conservation_pass", False)):
            status = "AMBER" if status == "GREEN" else status
        return self.gate.evaluate(variant, status, self.get_shadow_results(variant_id).get("results", []))

    def get_promoted(self) -> dict[str, Any]:
        return deepcopy(self.promoted)

    def get_shadow_results(self, variant_id: str | None = None) -> dict[str, Any]:
        if variant_id:
            return {
                "variant_id": variant_id,
                "results": deepcopy(self.shadow_results.get(variant_id, [])),
            }
        return {
            "total_variants": len(self.shadow_results),
            "results": deepcopy(self.shadow_results),
        }

    def _load_fixture(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_variants(self) -> list[dict[str, Any]]:
        fixture_variants = self.fixture.get("variants")
        if isinstance(fixture_variants, list) and fixture_variants:
            return [deepcopy(item) for item in fixture_variants if isinstance(item, dict)]
        variants: list[dict[str, Any]] = []
        for rule in self.rules:
            variants.extend(rule.generate_variants())
        return variants

    def _load_shadow_results(self) -> dict[str, list[dict[str, Any]]]:
        raw = self.fixture.get("shadow_results")
        if not isinstance(raw, dict):
            return {}
        return {
            str(variant_id): [deepcopy(item) for item in rows if isinstance(item, dict)]
            for variant_id, rows in raw.items()
            if isinstance(rows, list)
        }

    def _rule(self, template_name: str) -> RuleTemplate:
        for rule in self.rules:
            if rule.name == template_name:
                return rule
        raise ValueError(f"Unknown rule template: {template_name}")

    def _variant(self, variant_id: str) -> dict[str, Any]:
        for variant in self.variants:
            if variant.get("variant_id") == variant_id or variant.get("id") == variant_id:
                return deepcopy(variant)
        raise ValueError(f"Unknown variant: {variant_id}")

    def _recent_accuracy(self, candidates: list[dict[str, Any]]) -> float:
        values = [float(item.get("win_rate", 0.0) or 0.0) for item in candidates if "win_rate" in item]
        if not values:
            return 0.0
        return sum(values) / len(values)

    def _selector_phase(self) -> str:
        status = str(self.fixture.get("conservation_status") or "AMBER").upper()
        if status == "GREEN":
            return "mature"
        return "early"
