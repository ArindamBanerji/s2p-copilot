"""Regression coverage for the deterministic S2P pending-work cohort."""

from __future__ import annotations

from collections import Counter
from typing import Any, NoReturn

import pytest

from scripts import preseed_s2p_demo as preseed


def _report() -> dict[str, Any]:
    return {
        "scored": [],
        "skipped_existing": [],
        "errors": [],
        "learning_errors": [],
        "investigation_errors": [],
        "limitations": [],
    }


def _stored_row(slot: int, *, verified: bool = False) -> dict[str, Any]:
    payload = preseed._pending_cohort_payload(slot)
    return {
        **payload,
        "decision_id": f"decision-{slot}",
        "status": "confirmed" if verified else "pending",
        "factor_vector": [payload[name] for name in preseed.PENDING_COHORT_FACTOR_NAMES],
        "metadata": {
            "invoice_id": payload["event_id"],
            "supplier_id": payload["supplier_id"],
            "amount": payload["amount"],
            "planted": True,
            "evidence_tier": "T_S",
        },
    }


def _install_harness(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def decision_rows(_api: str) -> list[dict[str, Any]]:
        return list(rows)

    def probe(_api: str, path: str) -> tuple[int, dict[str, Any], None]:
        if path == "/api/s2p/centroid/all":
            return 200, {"factors": list(preseed.PENDING_COHORT_FACTOR_NAMES)}, None
        pending = [row for row in rows if not preseed._cohort_row_verified(row)]
        exceptions = [
            {
                "invoice_id": preseed._decision_identity(row),
                "factors": {
                    name: row[name] for name in preseed.PENDING_COHORT_FACTOR_NAMES
                },
            }
            for row in pending[:50]
        ]
        return 200, {
            "total": len(pending),
            "showing": len(exceptions),
            "exceptions": exceptions,
        }, None

    def score(
        _api: str,
        payload: dict[str, Any],
        existing: dict[str, dict[str, Any]],
        report: dict[str, Any],
    ) -> dict[str, Any]:
        calls.append(dict(payload))
        slot = int(str(payload["event_id"]).rsplit("-", 1)[-1])
        row = _stored_row(slot)
        rows.append(row)
        existing[str(payload["event_id"])] = row
        report["scored"].append(payload["event_id"])
        return row

    def forbidden_learn(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise AssertionError("pending cohort must never call _learn")

    monkeypatch.setattr(preseed, "_decision_rows", decision_rows)
    monkeypatch.setattr(preseed, "_probe", probe)
    monkeypatch.setattr(preseed, "_score", score)
    monkeypatch.setattr(preseed, "_learn", forbidden_learn)
    monkeypatch.setattr(preseed.time, "sleep", lambda _seconds: None)
    return calls


def test_fresh_24_created(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh phase scores all 24 deterministic invoices and learns none."""
    rows: list[dict[str, Any]] = []
    calls = _install_harness(monkeypatch, rows)
    report = _report()

    preseed._seed_pending_cohort("http://test", {}, report)

    assert len(calls) == 24
    assert [call["event_id"] for call in calls] == [
        f"S2P-INV-PENDING-V1-{slot:04d}" for slot in range(1, 25)
    ]
    assert [call["category"] for call in calls[:4]] == list(preseed.PENDING_COHORT_CATEGORIES)
    assert all(set(preseed.PENDING_COHORT_FACTOR_NAMES) <= call.keys() for call in calls)
    assert report["pending_cohort"]["created_ids"] == [call["event_id"] for call in calls]
    assert report["pending_cohort"]["pending_count"] == 24
    assert report["pending_cohort"]["status"] == "ready"


def test_rerun_no_new_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rerun reuses all owned pending rows without issuing score requests."""
    rows = [_stored_row(slot) for slot in range(1, 25)]
    calls = _install_harness(monkeypatch, rows)
    report = _report()

    preseed._seed_pending_cohort("http://test", {}, report)

    assert calls == []
    assert len(report["pending_cohort"]["reused_pending_ids"]) == 24
    assert report["pending_cohort"]["pending_count"] == 24


def test_consumed_slots_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verified owned slots remain consumed while absent slots are planted."""
    rows = [_stored_row(slot, verified=True) for slot in range(1, 6)]
    calls = _install_harness(monkeypatch, rows)
    report = _report()

    preseed._seed_pending_cohort("http://test", {}, report)

    assert len(calls) == 19
    assert report["pending_cohort"]["consumed_ids"] == [
        f"S2P-INV-PENDING-V1-{slot:04d}" for slot in range(1, 6)
    ]
    assert report["pending_cohort"]["pending_count"] == 19


def test_skip_on_training_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reported training errors skip planting before inventory or scoring."""
    calls = _install_harness(monkeypatch, [])
    report = _report()
    report["errors"].append({"detail": "training failed"})

    preseed._seed_pending_cohort("http://test", {}, report)

    assert calls == []
    assert report["pending_cohort"]["status"] == "skipped_training_failed"


def test_below_minimum_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """One remaining pending slot records the explicit exhaustion limitation."""
    rows = [_stored_row(slot, verified=slot < 24) for slot in range(1, 25)]
    calls = _install_harness(monkeypatch, rows)
    report = _report()

    preseed._seed_pending_cohort("http://test", {}, report)

    assert calls == []
    assert report["pending_cohort"]["pending_count"] == 1
    assert any("pending cohort exhausted" in item for item in report["limitations"])


def test_collision_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign row in the reserved namespace is reported and never overwritten."""
    row = _stored_row(1)
    row["supplier_id"] = "FOREIGN-SUPPLIER"
    row["metadata"]["supplier_id"] = "FOREIGN-SUPPLIER"
    calls = _install_harness(monkeypatch, [row])
    report = _report()

    preseed._seed_pending_cohort("http://test", {}, report)

    assert "S2P-INV-PENDING-V1-0001" in report["pending_cohort"]["collision_ids"]
    assert all(call["event_id"] != "S2P-INV-PENDING-V1-0001" for call in calls)
    assert any("conflicting provenance" in item for item in report["limitations"])


def test_factor_values_correct() -> None:
    """Each category payload uses the exact eight-factor design constants."""
    for slot, category in enumerate(preseed.PENDING_COHORT_CATEGORIES, start=1):
        payload = preseed._pending_cohort_payload(slot)
        assert payload["category"] == category
        assert {name: payload[name] for name in preseed.PENDING_COHORT_FACTOR_NAMES} == (
            preseed.PENDING_COHORT_FACTORS[category]
        )


def test_no_outcome_fields_in_payload() -> None:
    """Score-only payloads contain no fields that make a row verified."""
    forbidden = {
        "actual_action", "outcome", "status", "correct", "is_correct", "verified",
        "ground_truth_action",
    }
    for slot in range(1, 25):
        assert forbidden.isdisjoint(preseed._pending_cohort_payload(slot))


def test_supplier_risk_rating_inverse() -> None:
    """Supplier risk preserves the intended exception-history factor."""
    for slot in range(1, 25):
        payload = preseed._pending_cohort_payload(slot)
        assert payload["supplier_risk_rating"] == 1.0 - payload["supplier_exception_history"]


def test_category_distribution() -> None:
    """The cohort has six rows per supported category and no format category."""
    categories = Counter(preseed._pending_cohort_payload(slot)["category"] for slot in range(1, 25))
    assert categories == Counter({category: 6 for category in preseed.PENDING_COHORT_CATEGORIES})
    assert categories["format_compliance"] == 0


def test_pending_cohort_payload_count_and_id_scheme() -> None:
    """All 24 reserved invoice and supplier identities are deterministic."""
    payloads = [preseed._pending_cohort_payload(slot) for slot in range(1, 25)]
    assert len(payloads) == 24
    assert [payload["event_id"] for payload in payloads] == [
        f"S2P-INV-PENDING-V1-{slot:04d}" for slot in range(1, 25)
    ]
    assert [payload["supplier_id"] for payload in payloads] == [
        f"SUP-S2P-PENDING-V1-{slot:04d}" for slot in range(1, 25)
    ]


def test_pending_cohort_amount_range() -> None:
    """Amounts cover the designed 1000-to-3875 range in 125 increments."""
    amounts = [preseed._pending_cohort_payload(slot)["amount"] for slot in range(1, 25)]
    assert amounts == [1000.0 + 125.0 * index for index in range(24)]
    assert (min(amounts), max(amounts)) == (1000.0, 3875.0)


def test_readiness_rejects_empty_queue_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTP 200 empty queue cannot make 24 inventory rows ready."""
    rows: list[dict[str, Any]] = []
    _install_harness(monkeypatch, rows)
    original_probe = preseed._probe

    def empty_queue_probe(api: str, path: str) -> tuple[int, dict[str, Any], None]:
        if path == "/api/s2p/centroid/all":
            return original_probe(api, path)
        return 200, {"total": 0, "showing": 0, "exceptions": []}, None

    monkeypatch.setattr(preseed, "_probe", empty_queue_probe)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    phase = report["pending_cohort"]
    assert phase["pending_count"] == 24
    assert phase["owned_pending_ids"] == []
    assert phase["status"] == "partial"
    assert any("owned_pending=0" in item for item in report["limitations"])


def test_readiness_rejects_malformed_score_200(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful status without an ID or gate evidence is an error, not a block."""
    rows: list[dict[str, Any]] = []
    _install_harness(monkeypatch, rows)

    def malformed_score(
        _api: str,
        payload: dict[str, Any],
        _existing: dict[str, dict[str, Any]],
        report: dict[str, Any],
    ) -> None:
        report["errors"].append({
            "event_id": payload["event_id"], "status": 200, "detail": {"status": "ok"},
        })
        return None

    monkeypatch.setattr(preseed, "_score", malformed_score)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    assert report["errors"]
    assert report["pending_cohort"]["blocked_ids"] == []
    assert report["pending_cohort"]["status"] == "partial"


def test_readiness_accepts_sufficient_owned_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twenty visible owned pending rows satisfy the eighteen-row acceptance floor."""
    rows = [_stored_row(slot, verified=slot > 20) for slot in range(1, 25)]
    _install_harness(monkeypatch, rows)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    phase = report["pending_cohort"]
    assert phase["pending_count"] == 20
    assert len(phase["owned_pending_ids"]) == 20
    assert phase["status"] == "ready"
    assert report["limitations"] == []


def test_duplicate_owned_identity_is_not_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two rows for one reserved identity are an ambiguity, never reusable work."""
    rows = [_stored_row(1), {**_stored_row(1), "decision_id": "decision-1-duplicate"}]
    calls = _install_harness(monkeypatch, rows)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    assert "S2P-INV-PENDING-V1-0001" in report["pending_cohort"]["collision_ids"]
    assert all(call["event_id"] != "S2P-INV-PENDING-V1-0001" for call in calls)
    assert any("duplicate decision rows" in item for item in report["limitations"])


def test_explicit_held_response_is_a_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit held evidence is classified as conservation blocking."""
    rows: list[dict[str, Any]] = []
    _install_harness(monkeypatch, rows)

    def held_score(
        _api: str,
        payload: dict[str, Any],
        _existing: dict[str, dict[str, Any]],
        report: dict[str, Any],
    ) -> None:
        report["errors"].append({
            "event_id": payload["event_id"], "status": 200, "detail": {"held": True},
        })
        return None

    monkeypatch.setattr(preseed, "_score", held_score)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    assert report["errors"] == []
    assert len(report["pending_cohort"]["blocked_ids"]) == 24
    assert report["pending_cohort"]["status"] == "partial"
    assert any("conservation blocked" in item for item in report["limitations"])


def test_readiness_retries_delayed_queue_projection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient empty materialized queue is retried before readiness succeeds."""
    rows: list[dict[str, Any]] = []
    _install_harness(monkeypatch, rows)
    original_probe = preseed._probe
    queue_calls = 0

    def delayed_probe(api: str, path: str) -> tuple[int, dict[str, Any], None]:
        nonlocal queue_calls
        if path == "/api/s2p/centroid/all":
            return original_probe(api, path)
        queue_calls += 1
        if queue_calls <= 2:
            return 200, {"total": 0, "showing": 0, "exceptions": []}, None
        return original_probe(api, path)

    monkeypatch.setattr(preseed, "_probe", delayed_probe)
    report = _report()
    preseed._seed_pending_cohort("http://test", {}, report)

    assert queue_calls == 3  # one pre-write snapshot, then two post-write probes
    assert report["pending_cohort"]["status"] == "ready"
    assert len(report["pending_cohort"]["owned_pending_ids"]) == 24
