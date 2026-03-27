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
