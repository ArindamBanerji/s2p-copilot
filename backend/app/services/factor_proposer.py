"""Advisory factor replacement proposals for S2P."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FactorRecommendation:
    factor_name: str
    current_dk_weight: float
    signal_contribution_pct: float
    outcome_correlation: float
    verdict: str
    replacement_suggestion: str | None
    estimated_impact_pp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FactorProposer:
    """Analyze factor contribution and propose advisory replacements."""

    def __init__(self, dk_weights: dict[str, float], factor_stats: dict[str, dict[str, float]]):
        self.dk_weights = {str(key): float(value) for key, value in dict(dk_weights or {}).items()}
        self.factor_stats = {
            str(factor): dict(stats or {})
            for factor, stats in dict(factor_stats or {}).items()
        }

    def analyze(self) -> list[FactorRecommendation]:
        if not self.dk_weights:
            return []
        raw = []
        for factor, weight in self.dk_weights.items():
            stats = self.factor_stats.get(factor, {})
            variance = max(float(stats.get("variance", 0.0) or 0.0), 0.0)
            corr = float(stats.get("outcome_corr", 0.0) or 0.0)
            raw.append((factor, weight, variance, corr, max(weight, 0.0) * variance))

        total = sum(item[4] for item in raw) or 1.0
        recommendations = [
            self._recommend(factor, weight, corr, contribution / total * 100.0)
            for factor, weight, _variance, corr, contribution in raw
        ]
        return sorted(recommendations, key=lambda item: item.signal_contribution_pct, reverse=True)

    def propose_replacement(self, weak_factor: str, candidates: list[str]) -> dict[str, Any]:
        factor = str(weak_factor)
        if factor not in self.dk_weights:
            raise KeyError(f"Unknown factor: {factor}")
        replacement = _domain_replacement(factor, candidates)
        return {
            "factor": factor,
            "replacement": replacement,
            "estimated_pp": 4.0,
            "rationale": (
                f"{factor} contributes weak signal. "
                f"Dry-run {replacement} before changing the production factor set."
            ),
            "advisory": True,
        }

    def _recommend(
        self,
        factor: str,
        weight: float,
        corr: float,
        contribution_pct: float,
    ) -> FactorRecommendation:
        low_weight = weight < 0.08
        low_corr = abs(corr) < 0.10
        if low_weight and low_corr:
            verdict = "replace_candidate"
            replacement = _domain_replacement(factor, [])
            impact = 4.0
        elif weight >= 0.18 or abs(corr) >= 0.35:
            verdict = "keep"
            replacement = None
            impact = 0.0
        else:
            verdict = "review"
            replacement = None
            impact = 1.0
        return FactorRecommendation(
            factor_name=factor,
            current_dk_weight=round(float(weight), 6),
            signal_contribution_pct=round(float(contribution_pct), 3),
            outcome_correlation=round(float(corr), 6),
            verdict=verdict,
            replacement_suggestion=replacement,
            estimated_impact_pp=impact,
        )


def _domain_replacement(factor: str, candidates: list[str]) -> str:
    preferred = {
        "tax_regulatory_compliance": "tariff_exposure",
        "commodity_index_correlation": "supplier_geo_exposure",
        "payment_terms_impact": "cash_discount_capture",
        "supplier_exception_history": "supplier_otif_trend",
        "environmental_risk": "tariff_exposure",
    }
    if factor in preferred:
        return preferred[factor]
    return next((candidate for candidate in candidates if candidate != factor), "tariff_exposure")
