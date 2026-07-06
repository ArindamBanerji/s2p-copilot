import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.routers import s2p as s2p_router
from app.services.supplier_profile_accumulator import (
    COMPUTED_THRESHOLD,
    TRAILING_WINDOW,
    TREND_MIN_POINTS,
    SupplierProfileAccumulator,
)


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SUPPLIER_FIXTURE = DATA_DIR / "s2p_demo_suppliers.json"
INVOICE_FIXTURE = DATA_DIR / "synthetic_invoices.json"


def make_accumulator() -> SupplierProfileAccumulator:
    return SupplierProfileAccumulator(fixture_path=str(SUPPLIER_FIXTURE))


def decision(
    supplier_id: str | None = "SUP-TEST",
    *,
    supplier_name: str = "Test Supplier",
    invoice_id: str = "INV-1",
    invoice_date: str | None = "2026-01-01",
    amount: float = 100.0,
    category: str = "price_variance",
    created_at: str | None = None,
) -> dict:
    metadata = {
        "invoice_id": invoice_id,
        "supplier_name": supplier_name,
        "invoice_date": invoice_date,
        "amount": amount,
    }
    if supplier_id is not None:
        metadata["supplier_id"] = supplier_id
    if created_at is not None:
        metadata["created_at"] = created_at
    return {
        "decision_id": f"decision-{invoice_id}",
        "category": category,
        "recommended_action": "auto_approve",
        "factors": {"amount_variance_ratio": 0.4},
        "metadata": metadata,
    }


def outcome(*, is_correct: bool = True, reward: float = 1.0, timestamp: str = "verified-1") -> dict:
    return {
        "actual_action": "auto_approve" if is_correct else "hold_for_review",
        "is_correct": is_correct,
        "reward": reward,
        "timestamp": timestamp,
    }


def add_events(
    accumulator: SupplierProfileAccumulator,
    supplier_id: str,
    count: int,
    *,
    incorrect_after: int | None = None,
    dated: bool = True,
) -> None:
    for index in range(count):
        day = index + 1
        invoice_date = f"2026-01-{day:02d}" if dated else None
        is_correct = incorrect_after is None or index < incorrect_after
        accumulator.on_decision_verified(
            decision(
                supplier_id,
                invoice_id=f"{supplier_id}-{index}",
                invoice_date=invoice_date,
                amount=100.0 + index,
            ),
            outcome(is_correct=is_correct, reward=1.0 if is_correct else -1.0, timestamp=f"t-{index}"),
        )


def test_fixture_cold_start_returns_baseline():
    accumulator = make_accumulator()

    profile = accumulator.get_profile("SUP-001")

    assert profile is not None
    assert profile.supplier_name == "Aster Industrial Chemicals"
    assert profile.exception_rate == 0.12
    assert profile.otif == 0.88
    assert profile.source == "fixture"


def test_on_decision_verified_increments_count():
    accumulator = make_accumulator()

    accumulator.on_decision_verified(decision("SUP-NEW"), outcome())

    profile = accumulator.get_profile("SUP-NEW")
    assert profile is not None
    assert profile.invoice_count == 1


def test_on_decision_verified_updates_exception_rate():
    accumulator = make_accumulator()
    accumulator.on_decision_verified(decision("SUP-NEW", invoice_id="1"), outcome(is_correct=True))
    accumulator.on_decision_verified(decision("SUP-NEW", invoice_id="2"), outcome(is_correct=False))

    profile = accumulator.get_profile("SUP-NEW")

    assert profile is not None
    assert profile.exception_rate == 0.5


def test_trailing_window_caps_at_200():
    accumulator = make_accumulator()

    add_events(accumulator, "SUP-WINDOW", TRAILING_WINDOW + 5)

    history = accumulator.get_supplier_history("SUP-WINDOW", limit=TRAILING_WINDOW + 20)
    assert len(history) == TRAILING_WINDOW
    assert history[0]["invoice_id"] == "SUP-WINDOW-5"


def test_exception_rate_trend_with_sufficient_data():
    accumulator = make_accumulator()

    add_events(accumulator, "SUP-TREND", TREND_MIN_POINTS, incorrect_after=5)

    profile = accumulator.get_profile("SUP-TREND")
    assert profile is not None
    assert profile.exception_rate_trend is not None
    assert profile.exception_rate_trend > 0


def test_exception_rate_trend_none_below_threshold():
    accumulator = make_accumulator()

    add_events(accumulator, "SUP-SPARSE", TREND_MIN_POINTS - 1, incorrect_after=3)

    profile = accumulator.get_profile("SUP-SPARSE")
    assert profile is not None
    assert profile.exception_rate_trend is None


def test_computed_replaces_fixture_after_threshold():
    accumulator = make_accumulator()

    add_events(accumulator, "SUP-001", COMPUTED_THRESHOLD, incorrect_after=10)

    profile = accumulator.get_profile("SUP-001")
    assert profile is not None
    assert profile.invoice_count == COMPUTED_THRESHOLD
    assert profile.exception_rate == 0.5
    assert profile.otif == 0.88
    assert profile.source == "hybrid"


def test_multiple_suppliers_independent():
    accumulator = make_accumulator()
    accumulator.on_decision_verified(decision("SUP-A"), outcome(is_correct=True))
    accumulator.on_decision_verified(decision("SUP-B"), outcome(is_correct=False))

    profile_a = accumulator.get_profile("SUP-A")
    profile_b = accumulator.get_profile("SUP-B")

    assert profile_a is not None
    assert profile_b is not None
    assert profile_a.exception_rate == 0.0
    assert profile_b.exception_rate == 1.0


def test_reset_clears_events_keeps_fixtures():
    accumulator = make_accumulator()
    accumulator.on_decision_verified(decision("SUP-NEW"), outcome())

    accumulator.reset()

    assert accumulator.get_profile("SUP-NEW") is None
    assert accumulator.get_profile("SUP-001") is not None


def test_missing_supplier_id_skips_gracefully():
    accumulator = make_accumulator()

    accumulator.on_decision_verified(decision(None), outcome())

    assert accumulator.skipped_missing_supplier_id == 1
    assert accumulator.get_all_profiles()


def test_quarterly_from_invoice_dates():
    accumulator = make_accumulator()
    for invoice_id, invoice_date, correct in (
        ("q1-ok", "2026-01-15", True),
        ("q1-bad", "2026-03-15", False),
        ("q2-bad", "2026-04-15", False),
    ):
        accumulator.on_decision_verified(
            decision("SUP-Q", invoice_id=invoice_id, invoice_date=invoice_date),
            outcome(is_correct=correct),
        )

    profile_events = list(accumulator._events["SUP-Q"])
    quarterly = accumulator._compute_quarterly(profile_events)

    assert quarterly == {"Q1": 0.5, "Q2": 1.0}


def test_quarterly_empty_without_dates():
    accumulator = make_accumulator()
    add_events(accumulator, "SUP-NODATE", 3, dated=False)

    assert accumulator._compute_quarterly(list(accumulator._events["SUP-NODATE"])) == {}


def test_payment_response_computed():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-PAY", invoice_id=f"pay-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"payment_days": 20 + index},
        )

    profile = accumulator.get_profile("SUP-PAY")
    assert profile is not None
    assert profile.payment_response["avg_payment_days"] == 29.5


def test_early_pay_discount_detected():
    accumulator = make_accumulator()
    accumulator.on_decision_verified(
        decision("SUP-DISCOUNT"),
        outcome(),
        {"early_pay_discount": True},
    )

    profile = accumulator.get_profile("SUP-DISCOUNT")
    assert profile is not None
    assert profile.payment_response["early_pay_discount"] is True


def test_quality_defect_rate():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-QUALITY", invoice_id=f"quality-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"defect": index < 5},
        )

    profile = accumulator.get_profile("SUP-QUALITY")
    assert profile is not None
    assert profile.quality_patterns["defect_rate"] == 0.25


def test_quality_trend_worsening():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-WORSE", invoice_id=f"worse-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"defect": index >= 10},
        )

    profile = accumulator.get_profile("SUP-WORSE")
    assert profile is not None
    assert profile.quality_patterns["quality_trend"] == "worsening"


def test_quality_trend_improving():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-BETTER", invoice_id=f"better-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"defect": index < 10},
        )

    profile = accumulator.get_profile("SUP-BETTER")
    assert profile is not None
    assert profile.quality_patterns["quality_trend"] == "improving"


def test_f14_shape_ready():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-F14", invoice_id=f"f14-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"lead_time_days": 10 + (index % 3), "quantity": 50 + index * 25},
        )
    profile = accumulator.get_profile("SUP-F14")
    assert profile is not None
    assert profile.avg_lead_time_days is not None
    assert profile.avg_lead_time_days > 0
    assert len(profile.lead_time_by_quarter) > 0
    assert len(profile.lead_time_by_volume) > 0


def test_f16_shape_ready():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-F16", invoice_id=f"f16-{index}", invoice_date=f"2026-01-{index + 1:02d}", amount=100 + index),
            outcome(is_correct=index < 15),
            {"lead_time_days": 8 + (index % 4), "quantity": 75 + index * 20, "defect": index >= 16, "payment_days": 25 + index},
        )
    profile = accumulator.get_profile("SUP-F16")
    assert profile is not None
    assert profile.avg_lead_time_days is not None
    assert profile.pricing_trend is not None
    assert profile.exception_rate >= 0
    assert profile.quality_patterns["defect_rate"] > 0
    assert profile.payment_response["avg_payment_days"] > 0


def test_f19_shape_ready():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-F19", invoice_id=f"f19-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(is_correct=index < 10),
            {"payment_days": 15 + index, "early_pay_discount": index == 0},
        )
    profile = accumulator.get_profile("SUP-F19")
    assert profile is not None
    assert profile.payment_response["avg_payment_days"] > 0
    assert profile.payment_response["early_pay_discount"] is True
    assert profile.payment_response["payment_correlation_with_otif"] is not None


def test_lead_time_computed():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-LEAD", invoice_id=f"lead-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"lead_time_days": 12, "quantity": 100},
        )
    profile = accumulator.get_profile("SUP-LEAD")
    assert profile is not None
    assert profile.avg_lead_time_days == 12


def test_lead_time_by_quarter():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        month = 1 if index < 10 else 4
        day = (index % 10) + 1
        accumulator.on_decision_verified(
            decision("SUP-LEAD-Q", invoice_id=f"lead-q-{index}", invoice_date=f"2026-{month:02d}-{day:02d}"),
            outcome(),
            {"lead_time_days": 10 if month == 1 else 20, "quantity": 100},
        )
    profile = accumulator.get_profile("SUP-LEAD-Q")
    assert profile is not None
    assert profile.lead_time_by_quarter == {"Q1": 10.0, "Q2": 20.0}


def test_lead_time_by_volume():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        quantity = 50 if index < 7 else 200 if index < 14 else 700
        accumulator.on_decision_verified(
            decision("SUP-LEAD-V", invoice_id=f"lead-v-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"lead_time_days": 8 + index, "quantity": quantity},
        )
    profile = accumulator.get_profile("SUP-LEAD-V")
    assert profile is not None
    assert set(profile.lead_time_by_volume) == {"low", "medium", "high"}


def test_f14_shape_semantic():
    accumulator = make_accumulator()
    for index in range(COMPUTED_THRESHOLD):
        accumulator.on_decision_verified(
            decision("SUP-F14-SEM", invoice_id=f"f14-sem-{index}", invoice_date=f"2026-01-{index + 1:02d}"),
            outcome(),
            {"lead_time_days": 9 + (index % 2), "quantity": 600 if index > 10 else 50},
        )
    profile = accumulator.get_profile("SUP-F14-SEM")
    assert profile is not None
    assert profile.avg_lead_time_days is not None
    assert profile.lead_time_by_quarter
    assert profile.lead_time_by_volume


def test_no_created_at_proxy_for_seasonality():
    accumulator = make_accumulator()
    for index in range(TREND_MIN_POINTS):
        accumulator.on_decision_verified(
            decision(
                "SUP-CREATED",
                invoice_id=f"created-{index}",
                invoice_date=None,
                created_at=f"2026-01-{index + 1:02d}",
            ),
            outcome(is_correct=index < 5),
        )

    profile = accumulator.get_profile("SUP-CREATED")

    assert profile is not None
    assert profile.exception_rate_trend is None
    assert accumulator._compute_quarterly(list(accumulator._events["SUP-CREATED"])) == {}


def test_otif_from_fixture_hybrid():
    accumulator = make_accumulator()
    add_events(accumulator, "SUP-002", COMPUTED_THRESHOLD, incorrect_after=COMPUTED_THRESHOLD)

    profile = accumulator.get_profile("SUP-002")

    assert profile is not None
    assert profile.otif == 0.91
    assert profile.source == "hybrid"


def test_invoice_metadata_includes_invoice_date():
    invoice = json.loads(INVOICE_FIXTURE.read_text(encoding="utf-8"))[0]

    metadata = s2p_router._invoice_decision_metadata(invoice)

    assert metadata["invoice_date"] == invoice["metadata"]["invoice_date"]


def test_invoice_metadata_includes_due_date():
    invoice = json.loads(INVOICE_FIXTURE.read_text(encoding="utf-8"))[0]

    metadata = s2p_router._invoice_decision_metadata(invoice)

    assert metadata["due_date"] == invoice["metadata"]["due_date"]


def test_invoice_metadata_includes_po_number():
    invoice = json.loads(INVOICE_FIXTURE.read_text(encoding="utf-8"))[0]

    metadata = s2p_router._invoice_decision_metadata(invoice)

    assert metadata["po_number"] == invoice["po_number"]


def test_invoice_metadata_missing_temporal_fields_are_explicit_none():
    metadata = s2p_router._invoice_decision_metadata({
        "invoice_id": "INV-NO-DATE",
        "supplier_id": "SUP-001",
    })

    assert metadata["invoice_date"] is None
    assert metadata["due_date"] is None
    assert metadata["po_number"] is None
