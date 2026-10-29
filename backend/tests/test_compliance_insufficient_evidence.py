"""Tests for ComplianceScreener insufficient-evidence flagging."""

from __future__ import annotations

from app.services.compliance_screener import ComplianceScreener


class TestInsufficientEvidence:
    """Absent compliance attributes must NOT result in clearance."""

    def test_empty_transaction_not_cleared(self) -> None:
        """Transaction with no compliance fields is NOT cleared."""
        screener = ComplianceScreener()
        result = screener.screen(
            {"transaction_id": "T-EMPTY", "supplier_id": "S-001"},
            {},
        )
        assert result["cleared"] is False, "Empty transaction must not be cleared"
        assert result["risk_level"] != "low", "Empty transaction must not be low risk"
        regulation_names = {f["regulation"] for f in result["flags"]}
        assert "UFLPA" in regulation_names, "Missing UFLPA evidence must be flagged"
        assert "CSDDD" in regulation_names, "Missing CSDDD evidence must be flagged"
        assert "SCOPE3" in regulation_names, "Missing SCOPE3 evidence must be flagged"

    def test_insufficient_evidence_severity(self) -> None:
        """Insufficient-evidence flags use the correct severity."""
        screener = ComplianceScreener()
        result = screener.screen(
            {"transaction_id": "T-EMPTY", "supplier_id": "S-001"},
            {},
        )
        for flag in result["flags"]:
            assert flag["severity"] == "insufficient_evidence"

    def test_partial_evidence_flags_missing_only(self) -> None:
        """When some regulations have evidence, only missing ones are flagged."""
        screener = ComplianceScreener()
        result = screener.screen(
            {"transaction_id": "T-PARTIAL", "supplier_id": "S-001"},
            {"country_of_origin": "germany", "labor_practices": "audited"},
        )
        regulation_names = {f["regulation"] for f in result["flags"]}
        # UFLPA has evidence (country_of_origin present) — should NOT be flagged
        assert "UFLPA" not in regulation_names
        # CSDDD has evidence (labor_practices present) — should NOT be flagged
        assert "CSDDD" not in regulation_names
        # SCOPE3 has NO evidence — should be flagged
        assert "SCOPE3" in regulation_names

    def test_violation_not_double_flagged(self) -> None:
        """A regulation with an active violation is not also flagged for insufficient evidence."""
        screener = ComplianceScreener()
        result = screener.screen(
            {"transaction_id": "T-SANC", "supplier_id": "S-001"},
            {"sanctioned": True},
        )
        uflpa_flags = [f for f in result["flags"] if f["regulation"] == "UFLPA"]
        assert len(uflpa_flags) == 1, "UFLPA should appear exactly once"
        assert uflpa_flags[0]["severity"] == "critical"

    def test_full_evidence_still_cleared(self) -> None:
        """Transaction with full clean evidence IS cleared."""
        screener = ComplianceScreener()
        result = screener.screen(
            {"transaction_id": "T-FULL", "supplier_id": "S-001"},
            {
                "country_of_origin": "germany",
                "tier2_supplier_risk": "low",
                "labor_practices": "audited",
                "environmental_due_diligence": True,
                "carbon_footprint": 50.0,
                "logistics_emissions": 10.0,
                "supply_chain_ghg": 5.0,
            },
        )
        assert result["cleared"] is True, "Full clean evidence should be cleared"
        assert result["risk_level"] == "low"

    def test_report_with_insufficient_evidence(self) -> None:
        """Report reflects insufficient-evidence flags correctly."""
        screener = ComplianceScreener()
        checks = [
            screener.screen({"transaction_id": "T-1"}, {}),
            screener.screen(
                {"transaction_id": "T-2"},
                {
                    "country_of_origin": "germany",
                    "labor_practices": "audited",
                    "carbon_footprint": 50.0,
                },
            ),
        ]
        report = screener.report(checks, {"status": "GREEN", "note": "test"})
        assert report["flagged_count"] >= 1, "At least T-1 is flagged"
        assert report["total_screened"] == 2
