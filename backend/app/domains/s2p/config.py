"""
S2P Domain Configuration.
Procurement copilot - Source-to-Pay invoice exception domain.
C=5 categories, A=5 actions, d=8 factors. Tensor (5,5,8)=200.
penalty_ratio=5.0 (S2P: false-approve less costly than SOC false-suppress).
"""

try:
    from domain_config import S2PDomainConfigV2 as _SharedS2PDomainConfigV2
except ImportError:
    _SharedS2PDomainConfigV2 = None


def _shared_or(name: str, fallback):
    if _SharedS2PDomainConfigV2 is None:
        return fallback
    return list(getattr(_SharedS2PDomainConfigV2, name))


S2P_CATEGORIES = [
    "price_variance",
    "quantity_mismatch",
    "duplicate_risk",
    "contract_gap",
    "format_compliance",
]
S2P_CATEGORIES = _shared_or("categories", S2P_CATEGORIES)

S2P_ACTIONS = [
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
]
S2P_ACTIONS = _shared_or("actions", S2P_ACTIONS)

S2P_FACTORS = [
    "match_status",
    "amount_variance_ratio",
    "duplicate_score",
    "supplier_exception_history",
    "payment_terms_impact",
    "commodity_index_correlation",
    "tax_regulatory_compliance",
    "environmental_risk",
]
S2P_FACTORS = _shared_or("factors", S2P_FACTORS)

S2P_REASON_CODES = [
    "wrong_category",
    "wrong_action",
    "missing_context",
    "system_correct_but_override_policy",
    "novel_situation",
]

S2P_EVIDENCE_TEMPLATES = {
    "price_variance": "{variance_pct}% price delta. {commodity} moved {commodity_delta}% in {lookback} days. Contract {ref} {allows_blocks} pass-through up to {threshold}%. {within_exceeds} bounds. -> {action}. Confidence: {score}.",
    "quantity_mismatch": "Invoice qty {inv_qty} vs PO {po_qty} (Delta {delta}). GR confirms {gr_qty} received. {match_status}. -> {action}.",
    "duplicate_risk": "Invoice {invoice_id} from {supplier}. Similar: {match_id} dated {match_date}, amount {match_amt} (similarity {similarity}%). {verdict}. -> {action}.",
    "contract_gap": "PO {po_id}. Contract {ref} covers {scope}. {covered_pct}% covered. Gap: {gap_items}. -> {action}.",
    "format_compliance": "Invoice from {supplier} fails {n_rules} format rules. Issues: {issues}. Historical compliance: {compliance_pct}%. -> {action}.",
}

S2P_CANONICAL_FACTORS = [
    "supplier_identity",
    "contract_linkage",
    "spend_category",
    "data_quality_score",
]

S2P_ACTION_CENTROIDS = {
    "auto_approve": [0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95, 0.50],
    "hold_for_review": [0.70, 0.30, 0.10, 0.15, 0.40, 0.50, 0.80, 0.50],
    "escalate_to_buyer": [0.50, 0.60, 0.15, 0.30, 0.60, 0.30, 0.70, 0.50],
    "flag_leakage": [0.80, 0.50, 0.10, 0.40, 0.70, 0.20, 0.60, 0.50],
    "refer_to_specialist": [0.40, 0.40, 0.30, 0.50, 0.30, 0.40, 0.50, 0.50],
}

# Tensor dimensions
N_CATEGORIES = 5
N_ACTIONS = 5
N_FACTORS = 8

# Learning hyperparameters
TAU = getattr(_SharedS2PDomainConfigV2, "tau", 0.1)
ETA_CONFIRM = getattr(_SharedS2PDomainConfigV2, "eta_confirm", 0.05)
ETA_OVERRIDE = getattr(_SharedS2PDomainConfigV2, "eta_override", 0.01)
PENALTY_RATIO = getattr(_SharedS2PDomainConfigV2, "penalty_ratio", 5.0)
LEARNING_ENABLED = False

# Noise ceiling (DiagonalKernel)
SIGMA_GREEN = 0.157
SIGMA_AMBER = 0.25
SIGMA_RED = 0.25


class S2PDomainConfig:
    """
    Canonical S2P domain configuration for invoice exception triage.
    """
    domain = "s2p"
    categories = S2P_CATEGORIES
    actions = S2P_ACTIONS
    factors = S2P_FACTORS
    canonical_factors = S2P_CANONICAL_FACTORS
    reason_codes = S2P_REASON_CODES
    evidence_templates = S2P_EVIDENCE_TEMPLATES

    n_categories = N_CATEGORIES
    n_actions = N_ACTIONS
    n_factors = N_FACTORS

    tau = TAU
    eta_confirm = ETA_CONFIRM
    eta_override = ETA_OVERRIDE
    penalty_ratio = PENALTY_RATIO
    q_window = 400
    alpha_window = 50

    @classmethod
    def get_categories(cls) -> list[str]:
        return list(cls.categories)

    @classmethod
    def get_actions(cls) -> list[str]:
        return list(cls.actions)

    @classmethod
    def get_factors(cls) -> list[str]:
        return list(cls.factors)

    @classmethod
    def get_profile_centroids(cls):
        """Return ndarray profile centroids with shape (5, 5, 8)."""
        import numpy as np

        centroids = [
            [S2P_ACTION_CENTROIDS[action] for action in cls.actions]
            for _category in cls.categories
        ]
        return np.array(centroids, dtype=float)

    @classmethod
    def get_initial_centroids(cls) -> dict:
        """Return nested dict {category: {action: [factor_values]}}."""
        return {
            category: {
                action: list(S2P_ACTION_CENTROIDS[action])
                for action in cls.actions
            }
            for category in cls.categories
        }

    @classmethod
    def get_sigma_profile(cls) -> list[float]:
        """Bootstrap sigma profile for the canonical scoring factors."""
        return [0.15] * cls.n_factors

    @classmethod
    def get_calibration_profile(cls):
        """
        Build a GAE CalibrationProfile using the actual constructor fields.

        eta_override, q_window, and alpha_window are preserved in extensions
        because they are not CalibrationProfile constructor fields.
        """
        from gae import CalibrationProfile

        return CalibrationProfile(
            learning_rate=cls.eta_confirm,
            penalty_ratio=cls.penalty_ratio,
            temperature=cls.tau,
            extensions={
                "eta": cls.eta_confirm,
                "eta_neg": cls.eta_confirm,
                "eta_override": cls.eta_override,
                "q_window": cls.q_window,
                "alpha_window": cls.alpha_window,
            },
        )

    @classmethod
    def get_category_index(cls, category: str) -> int:
        return cls.categories.index(category)

    @classmethod
    def get_action_index(cls, action: str) -> int:
        return cls.actions.index(action)

    @classmethod
    def get_factor_index(cls, factor: str) -> int:
        return cls.factors.index(factor)


# Temporary canonical alias for existing preview/synthetic invoice imports.
# This does not preserve the removed legacy tensor.
S2PDomainConfigV2 = S2PDomainConfig
