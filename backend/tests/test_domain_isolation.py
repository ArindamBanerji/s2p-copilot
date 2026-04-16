"""
tests/test_domain_isolation.py — S2P domain isolation tests.

Enforces the multi-copilot principle: a procurement copilot must be
buildable by registering a DomainConfig — not by forking SOC.
S2P must have zero SOC dependencies and correct tensor dimensions.

Actual S2P tensor: (6, 4, 6) = 144 values.
  N_CATEGORIES=6, N_ACTIONS=4, N_FACTORS=6, PENALTY_RATIO=5.0
Note: CLAUDE.md spec describes (5,5,8) with A=5; actual impl uses (6,4,6)
with A=4. Tests verify the REAL code, not the unimplemented spec.

Run from backend/:
    pytest tests/test_domain_isolation.py -v
"""

import ast
import importlib
import pathlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================================
# TestNoSOCImports
# ============================================================================

class TestNoSOCImports:
    """S2P modules must have zero SOC domain imports."""

    def test_s2p_modules_load_without_soc_in_sys_modules(self):
        """Import all S2P modules; verify domains.soc is absent from sys.modules."""
        modules = [
            "app.domains.s2p.config",
            "app.domains.s2p.factors",
            "app.domains.s2p.scorer",
            "app.domains.s2p.graph",
        ]
        for m in modules:
            importlib.import_module(m)

        soc_modules = [k for k in sys.modules if "domains.soc" in k]
        assert soc_modules == [], (
            f"SOC modules leaked into sys.modules after S2P import: {soc_modules}"
        )

    def test_s2p_config_has_no_soc_constants(self):
        """S2P config source must not contain SOC-specific constant names."""
        config_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "domains" / "s2p" / "config.py"
        source = config_path.read_text(encoding="utf-8")

        soc_tokens = [
            "SOC_",
            "credential_access",
            "lateral_movement",
            "data_exfiltration",
            "privilege_escalation",
        ]
        for token in soc_tokens:
            assert token not in source, (
                f"SOC constant '{token}' found in S2P config — isolation violated"
            )

    def test_s2p_source_files_have_no_soc_imports(self):
        """No .py file under app/domains/s2p/ may import from the soc domain."""
        s2p_dir = pathlib.Path("app/domains/s2p")
        violations = []

        for py_file in sorted(s2p_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    if "soc" in module.lower():
                        violations.append(f"{py_file.name}: imports {module}")
                    for alias in getattr(node, "names", []):
                        if "soc" in (alias.name or "").lower():
                            violations.append(
                                f"{py_file.name}: imports name '{alias.name}'"
                            )

        assert violations == [], (
            f"SOC import violations in app/domains/s2p/: {violations}"
        )


# ============================================================================
# TestTensorDimensions
# ============================================================================

class TestTensorDimensions:
    """S2P tensor dimensions must match actual config constants."""

    def test_s2p_tensor_shape_is_6_4_6(self):
        """
        S2P tensor is (N_CATEGORIES=6, N_ACTIONS=4, N_FACTORS=6) = 144 values.
        This is the implemented shape — distinct from any generic/SOC config.
        """
        from app.domains.s2p.config import S2PDomainConfig

        assert S2PDomainConfig.n_categories == 6
        assert S2PDomainConfig.n_actions == 4
        assert S2PDomainConfig.n_factors == 6
        total = (
            S2PDomainConfig.n_categories
            * S2PDomainConfig.n_actions
            * S2PDomainConfig.n_factors
        )
        assert total == 144

    def test_s2p_has_4_actions_not_soc_count(self):
        """
        S2P defines 4 actions: approve, escalate, reject, review.
        (CLAUDE.md spec targets A=5; current implementation uses A=4.)
        The test anchors the real count so regressions are caught.
        """
        from app.domains.s2p.config import S2PDomainConfig, S2P_ACTIONS

        assert S2PDomainConfig.n_actions == 4
        assert len(S2P_ACTIONS) == 4
        # Verify the exact procurement verbs — no security verbs
        for action in S2P_ACTIONS:
            assert action in {"approve", "escalate", "reject", "review"}, (
                f"Unexpected S2P action '{action}'"
            )

    def test_s2p_penalty_ratio_is_5_not_soc(self):
        """S2P penalty_ratio=5.0; SOC uses 20.0 — must never drift to SOC value."""
        from app.domains.s2p.config import S2PDomainConfig, PENALTY_RATIO

        assert PENALTY_RATIO == 5.0
        assert S2PDomainConfig.penalty_ratio == 5.0
        assert S2PDomainConfig.penalty_ratio != 20.0  # SOC value

    def test_s2p_categories_are_procurement_not_security(self):
        """S2P categories are procurement domain; SOC categories must be absent."""
        from app.domains.s2p.config import S2P_CATEGORIES

        # Required S2P procurement categories
        required = {
            "maverick_spend",
            "supplier_risk",
            "contract_breach",
            "budget_overrun",
            "approval_bypass",
            "data_quality",
        }
        assert required == set(S2P_CATEGORIES), (
            f"S2P categories deviated from expected set: {set(S2P_CATEGORIES)}"
        )

        # SOC security categories must never appear
        soc_categories = [
            "credential_access",
            "lateral_movement",
            "data_exfiltration",
            "privilege_escalation",
            "persistence",
            "defense_evasion",
        ]
        for soc_cat in soc_categories:
            assert soc_cat not in S2P_CATEGORIES, (
                f"SOC category '{soc_cat}' found in S2P_CATEGORIES"
            )


# ============================================================================
# TestS2PScoring
# ============================================================================

class TestS2PScoring:
    """S2P scorer wiring tests using actual (6,4,6) configuration."""

    def setup_method(self):
        from app.domains.s2p.scorer import reset_scorer
        reset_scorer()

    def test_build_profile_scorer_with_s2p_params_succeeds(self):
        """build_profile_scorer with S2P DomainConfig params returns a ProfileScorer."""
        from gae import build_profile_scorer, KernelType, ProfileScorer
        from app.domains.s2p.config import S2PDomainConfig

        scorer = build_profile_scorer(
            categories=S2PDomainConfig.categories,
            actions=S2PDomainConfig.actions,
            centroids=S2PDomainConfig.get_initial_centroids(),
            n_factors=S2PDomainConfig.n_factors,
            kernel=KernelType.L2,
        )
        assert scorer is not None
        assert isinstance(scorer, ProfileScorer)

    def test_centroids_shape_matches_s2p_config(self):
        """Scorer mu (centroids) shape == (n_categories, n_actions, n_factors)."""
        from app.domains.s2p.scorer import get_scorer
        from app.domains.s2p.config import S2PDomainConfig

        scorer = get_scorer()
        expected_shape = (
            S2PDomainConfig.n_categories,
            S2PDomainConfig.n_actions,
            S2PDomainConfig.n_factors,
        )
        assert scorer.mu.shape == expected_shape, (
            f"Centroid shape {scorer.mu.shape} != expected {expected_shape}"
        )

    def test_score_output_length_matches_n_actions(self):
        """score_event returns one probability per action (len == n_actions == 4)."""
        from app.domains.s2p.scorer import score_event
        from app.domains.s2p.config import S2PDomainConfig

        factor_vector = [0.5] * S2PDomainConfig.n_factors
        result = score_event(factor_vector, S2PDomainConfig.categories[0])
        assert len(result["probabilities"]) == S2PDomainConfig.n_actions


# ============================================================================
# TestS2PIndependence
# ============================================================================

class TestS2PIndependence:
    """S2P scorer must be dimensionally and semantically independent."""

    def setup_method(self):
        from app.domains.s2p.scorer import reset_scorer
        reset_scorer()

    def test_s2p_scorer_shape_differs_from_other_domain_scorer(self):
        """
        A scorer built from S2P DomainConfig has a different mu shape than
        a scorer built from a hypothetical other-domain config.
        Demonstrates that DomainConfig drives scorer isolation.
        """
        from gae import build_profile_scorer, KernelType
        from app.domains.s2p.scorer import get_scorer
        from app.domains.s2p.config import S2PDomainConfig

        s2p_scorer = get_scorer()
        s2p_shape = s2p_scorer.mu.shape  # (6, 4, 6)

        # Hypothetical fraud/SOC domain: 3 categories, 5 actions, 4 factors
        other_categories = ["fraud_type_a", "fraud_type_b", "fraud_type_c"]
        other_actions = ["block", "flag", "allow", "review", "escalate"]  # 5
        other_n_factors = 4  # 4 ≠ 6
        other_centroids = {
            c: {a: [0.5] * other_n_factors for a in other_actions}
            for c in other_categories
        }
        other_scorer = build_profile_scorer(
            categories=other_categories,
            actions=other_actions,
            centroids=other_centroids,
            n_factors=other_n_factors,
            kernel=KernelType.L2,
        )

        assert s2p_shape != other_scorer.mu.shape, (
            f"S2P scorer shape {s2p_shape} matches other-domain scorer shape "
            f"{other_scorer.mu.shape} — DomainConfig isolation may be broken"
        )

    def test_s2p_config_values_are_procurement_specific(self):
        """
        S2P config uses procurement domain verbs and factor names exclusively.
        No security domain vocabulary (suppress, investigate, threat_intel, etc.).
        """
        from app.domains.s2p.config import S2P_ACTIONS, S2P_FACTORS

        soc_action_vocab = {"suppress", "investigate", "alert", "block", "contain"}
        s2p_action_set = set(S2P_ACTIONS)
        overlap = s2p_action_set & soc_action_vocab
        assert overlap == set(), (
            f"S2P actions overlap with SOC vocabulary: {overlap}"
        )

        soc_factor_vocab = {
            "threat_intel_enrichment",
            "travel_match",
            "time_anomaly",
            "beaconing_score",
            "data_volume_delta",
        }
        s2p_factor_set = set(S2P_FACTORS)
        overlap = s2p_factor_set & soc_factor_vocab
        assert overlap == set(), (
            f"S2P factors overlap with SOC vocabulary: {overlap}"
        )
