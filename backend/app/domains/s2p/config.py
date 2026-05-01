"""
S2P Domain Configuration.
Procurement copilot — Source-to-Pay domain.
C=6 categories, A=4 actions, d=6 factors. Tensor (6,4,6)=144.
penalty_ratio=5.0 (S2P: false-approve less costly than SOC false-suppress).
"""

S2P_CATEGORIES = [
    "maverick_spend",
    "supplier_risk",
    "contract_breach",
    "budget_overrun",
    "approval_bypass",
    "data_quality",
]

S2P_ACTIONS = [
    "approve",
    "escalate",
    "reject",
    "review",
]

S2P_FACTORS = [
    "spend_category_match",
    "supplier_risk_score",
    "contract_compliance",
    "spend_anomaly",
    "pattern_history",
    "vendor_trust",
]

# Tensor dimensions
N_CATEGORIES = 6
N_ACTIONS    = 4
N_FACTORS    = 6

# Learning hyperparameters
TAU              = 0.1
ETA_CONFIRM      = 0.05
ETA_OVERRIDE     = 0.01
PENALTY_RATIO    = 5.0
LEARNING_ENABLED = False

# Noise ceiling (DiagonalKernel)
SIGMA_GREEN  = 0.157   # same as L2 GREEN — conservative until P28 runs
SIGMA_AMBER  = 0.25
SIGMA_RED    = 0.25    # > SIGMA_AMBER


class S2PDomainConfig:
    """
    S2P domain configuration. Analogous to SOCDomainConfig.
    Injected into framework services — never imported by framework/.
    """
    categories    = S2P_CATEGORIES
    actions       = S2P_ACTIONS
    factors       = S2P_FACTORS
    n_categories  = N_CATEGORIES
    n_actions     = N_ACTIONS
    n_factors     = N_FACTORS
    tau           = TAU
    eta_confirm   = ETA_CONFIRM
    eta_override  = ETA_OVERRIDE
    penalty_ratio = PENALTY_RATIO

    @classmethod
    def get_initial_centroids(cls) -> dict:
        """
        Bootstrap centroids — uniform 0.5 prior.
        Real values from P28 Phase 1 after first deployment.
        Returns nested dict {category: {action: [factor_values]}}.
        """
        return {
            cat: {
                act: [0.5] * cls.n_factors
                for act in cls.actions
            }
            for cat in cls.categories
        }

    @classmethod
    def get_sigma_profile(cls) -> list[float]:
        """
        Bootstrap sigma profile — uniform 0.15 prior.
        Real values from P28 Phase 2 after deployment qualification.
        """
        return [0.15] * cls.n_factors

    @classmethod
    def get_category_index(cls, category: str) -> int:
        return cls.categories.index(category)

    @classmethod
    def get_action_index(cls, action: str) -> int:
        return cls.actions.index(action)

    @classmethod
    def get_factor_index(cls, factor: str) -> int:
        return cls.factors.index(factor)


S2P_V2_CATEGORIES = [
    "price_variance",
    "quantity_mismatch",
    "duplicate_risk",
    "contract_gap",
    "format_compliance",
]

S2P_V2_ACTIONS = [
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
]

S2P_V2_FACTORS = [
    "match_status",
    "amount_variance_ratio",
    "duplicate_score",
    "supplier_exception_history",
    "payment_terms_impact",
    "commodity_index_correlation",
    "tax_regulatory_compliance",
]

S2P_V2_CANONICAL_FACTORS = [
    "supplier_identity",
    "contract_linkage",
    "spend_category",
    "data_quality_score",
]

S2P_V2_ACTION_CENTROIDS = {
    "auto_approve": [0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95],
    "hold_for_review": [0.70, 0.30, 0.10, 0.15, 0.40, 0.50, 0.80],
    "escalate_to_buyer": [0.50, 0.60, 0.15, 0.30, 0.60, 0.30, 0.70],
    "flag_leakage": [0.80, 0.50, 0.10, 0.40, 0.70, 0.20, 0.60],
    "refer_to_specialist": [0.40, 0.40, 0.30, 0.50, 0.30, 0.40, 0.50],
}


class S2PDomainConfigV2:
    """
    S2P v2 domain configuration for invoice exception triage.

    This is versioned separately from S2PDomainConfig so legacy endpoints
    continue to use the existing (6,4,6) configuration.
    """
    domain = "s2p"
    categories = S2P_V2_CATEGORIES
    actions = S2P_V2_ACTIONS
    factors = S2P_V2_FACTORS
    canonical_factors = S2P_V2_CANONICAL_FACTORS

    n_categories = 5
    n_actions = 5
    n_factors = 7

    tau = 0.1
    eta_confirm = 0.05
    eta_override = 0.01
    penalty_ratio = 5.0
    q_window = 400
    alpha_window = 50

    @classmethod
    def get_profile_centroids(cls):
        """Return ndarray profile centroids with shape (5, 5, 7)."""
        import numpy as np

        centroids = [
            [S2P_V2_ACTION_CENTROIDS[action] for action in cls.actions]
            for _category in cls.categories
        ]
        return np.array(centroids, dtype=float)

    @classmethod
    def get_initial_centroids(cls) -> dict:
        """
        Return nested dict {category: {action: [factor_values]}}.

        Kept for compatibility with build_profile_scorer-style callers.
        """
        return {
            category: {
                action: list(S2P_V2_ACTION_CENTROIDS[action])
                for action in cls.actions
            }
            for category in cls.categories
        }

    @classmethod
    def get_sigma_profile(cls) -> list[float]:
        """Bootstrap sigma profile for the v2 scoring factors."""
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
