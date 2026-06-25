from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer
from app.services.optimizer_export import OptimizerExportService

client = TestClient(app)


def test_export_complete():
    export = OptimizerExportService().export(profiles=[{"supplier_id": "SUP-1"}])

    assert {"centroids", "supplier_profiles", "lead_time_distributions", "exception_likelihoods", "conservation_state"} <= set(export)


def test_centroid_shape():
    export = OptimizerExportService().export()

    assert export["tensor_shape"] == {"categories": 5, "actions": 5, "factors": 7}
    assert len(export["centroids"]) == 5
    assert len(export["centroids"][0]) == 5
    assert len(export["centroids"][0][0]) == 7


def test_dk_weights_present():
    class Scorer:
        dk_weights = [0.5] * 35

    export = OptimizerExportService().export(scorer=Scorer())

    assert len(export["dk_weights"]) == 5
    assert len(export["dk_weights"][0]) == 7


def test_dk_weights_omitted():
    export = OptimizerExportService().export(scorer=object())

    assert "dk_weights" not in export
    assert any("DK weights unavailable" in warning for warning in export["warnings"])


def test_sections_filter():
    export = OptimizerExportService().export(sections=["centroids", "dk_weights"])

    assert "centroids" in export
    assert "supplier_profiles" not in export


def test_json_serializable():
    service = OptimizerExportService()
    export = service.export(profiles=[{"supplier_id": "SUP-1"}])

    assert json.loads(service.to_json(export))["domain"] == "s2p"


def test_validate_complete():
    service = OptimizerExportService()
    export = service.export(profiles=[{"supplier_id": "SUP-1"}])

    assert service.validate(export)["valid"] is True


def test_validate_missing():
    service = OptimizerExportService()
    export = service.export(sections=["centroids"])

    validation = service.validate(export)

    assert validation["valid"] is False
    assert "supplier_profiles" in validation["missing"]


def test_schema_endpoint():
    response = client.get("/api/s2p/optimizer/schema")

    assert response.status_code == 200
    assert response.json()["version"] == "1.0"


def test_narrative_present():
    response = client.get("/api/s2p/optimizer/export")

    assert response.status_code == 200
    assert "Compatible with Gurobi" in response.json()["narrative"]


def test_invalid_section_rejected():
    response = client.get("/api/s2p/optimizer/export", params={"sections": "bogus"})

    assert response.status_code == 400
    assert "Invalid sections" in response.json()["detail"]


def test_mixed_valid_invalid_rejected():
    response = client.get("/api/s2p/optimizer/export", params={"sections": "centroids,bogus"})

    assert response.status_code == 400
    assert "bogus" in response.json()["detail"]


def test_metadata_pre_transition():
    export = OptimizerExportService().export(scorer=object())

    assert export["centroid_count"] == 175
    assert export["dk_count"] == 0
    assert export["total_parameters"] == 175
    assert export["dk_status"].startswith("pre-transition")


def test_metadata_post_transition():
    class Scorer:
        dk_weights = [0.5] * 35

    export = OptimizerExportService().export(scorer=Scorer())

    assert export["centroid_count"] == 175
    assert export["dk_count"] == 35
    assert export["total_parameters"] == 210
    assert export["dk_status"] == "available"


def test_narrative_matches_metadata():
    pre = OptimizerExportService().export(scorer=object())
    post = OptimizerExportService().export(scorer=type("Scorer", (), {"dk_weights": [0.5] * 35})())

    assert "DK weights not yet available" in pre["narrative"]
    assert "35 DK weights" in post["narrative"]


def test_sections_available_deduplicated():
    export = OptimizerExportService().export()

    assert export["sections_available"] == sorted(set(export["sections_available"]))
