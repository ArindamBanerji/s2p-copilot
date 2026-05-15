import json
from pathlib import Path

from app.domains.s2p.evolution import S2PEvolutionService


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "s2p_evolution_fixture.json"


def test_context_selector_selects_category_variant():
    service = S2PEvolutionService(scorer=None)

    variant = service.select_variant("auto_approve_threshold_sweep", "price_variance")

    assert variant["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"


def test_context_selector_records_failed_variant():
    service = S2PEvolutionService(scorer=None)
    before = service.select_variant("auto_approve_threshold_sweep", "price_variance")

    service.selector.record_failure("price_variance", before["variant_id"])
    after = service.select_variant("auto_approve_threshold_sweep", "price_variance")

    assert after["variant_id"] != before["variant_id"]


def test_fixture_loads_from_data_dir():
    service = S2PEvolutionService(scorer=None)

    assert service.fixture_path == FIXTURE
    assert service.fixture["version"] == "gap_h1_v1"
    assert service.get_promoted()["source"] == "fixture"


def test_fixture_schema_has_required_fields():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    for key in (
        "version",
        "conservation_status",
        "conservation_pass",
        "rule_templates",
        "variants",
        "shadow_results",
        "promoted",
    ):
        assert key in data
    assert data["conservation_status"] == "GREEN"
    assert data["conservation_pass"] is True
    assert len(data["shadow_results"]["auto_approve_threshold_sweep:price_variance:0.91"]) >= 3
