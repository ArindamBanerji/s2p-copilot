"""S2P payload builders using one shared graph snapshot per refresh."""
from __future__ import annotations

import math
from typing import Any, cast

from copilot_sdk.backend.conservation_utils import compute_conservation_status_payload
from copilot_sdk.scoring.composite_gate import CompositeGate, outcomes_from_state
from copilot_sdk.config.tenant import current_tenant_id

from app.graph.s2p_graph_reader import S2PGraphReader
from app.services.response_materializer import ResponseMaterializer
from app.services.s2p_preview_data import merge_decisions
from app.services.supplier_profile_provider import build_profiles, preview_profiles


class SnapshotStore:
    """Build-local read view; history is shared, small auxiliary reads memoized."""
    def __init__(self, shared: dict[str, Any]) -> None:
        self._source = shared["store"]
        self._decisions = cast(list[dict[str, Any]], shared["decisions"])
        self._verified = cast(list[dict[str, Any]], shared["verified"])
        self._reads: dict[Any, Any] = {}

    def get_all_decisions(self, domain: str = "s2p") -> list[dict[str, Any]]:
        return list(self._decisions)

    def get_verified_decisions(self, domain: str = "s2p") -> list[dict[str, Any]]:
        return list(self._verified)

    def __getattr__(self, name: str) -> Any:
        # Do not expose AGE internals: summary action counts must use the rows.
        if name.startswith("_"):
            raise AttributeError(name)
        method = getattr(self._source, name)
        if not callable(method):
            return method

        def read(*args: Any, **kwargs: Any) -> Any:
            if not name.startswith(("get_", "count_", "list_")):
                raise AttributeError(f"Snapshot is read-only: {name}")
            key = (name, args, tuple(sorted(kwargs.items())))
            if key not in self._reads:
                self._reads[key] = method(*args, **kwargs)
            return self._reads[key]
        return read


def conservation_payload(state: dict[str, Any]) -> dict[str, Any]:
    """Full shared conservation GET contract, without a response-cache dependency."""
    payload = compute_conservation_status_payload("s2p", state)
    threshold = payload.get("theta_min")
    threshold = float(threshold) if threshold is not None else None
    if threshold is not None and not math.isfinite(threshold):
        threshold = None
    layers = CompositeGate(
        w_short=int(state.get("w_short", 20)), m_rate=float(state.get("m_rate", 0.85)),
        m_rel=float(payload.get("relative_trigger_ratio") or 0.7),
    ).evaluate(
        alpha_q_v=float(payload.get("signal") or 0), theta_min=threshold,
        rolling_accuracy=float(payload.get("q") or 0), baseline=float(payload.get("baseline_q") or 0),
        verified_outcomes=outcomes_from_state(state), base_status=str(payload.get("status") or "GREEN"),
    )
    payload.update(layers)
    mode = payload.get("conservation_mode")
    if mode in {"cold_start", "bootstrap"}:
        payload.update(status=str(mode).upper(), passed=True)
    active = [name.upper() for name, details in layers.items()
              if name.startswith("g_") and isinstance(details, dict) and details.get("active") is True]
    status = str(layers.get("status") or "GREEN")
    if active and status in {"AMBER", "RED"}:
        reason = "Gate RED" if status == "RED" else "/".join(active) + " active"
        payload.update(status=status, passed=False, reason=reason)
    return dict(payload)


def create_s2p_materializer(app_state: Any) -> ResponseMaterializer:
    # Local imports avoid router/main import cycles.
    from app.routers.s2p import _read_conservation_counts
    from app.routers.s2p_data_helpers import load_invoices
    from app.routers.s2p_evidence import _audit_pack_payload, compliance_payload
    from app.routers.s2p_governance import SCREENING_RECEIPT_LIMIT, _screening_payload
    from app.routers.s2p_insight import fingerprint_payload
    from app.routers.s2p_performance import _build_summary
    from app.routers.s2p_preview import queue_payload, score_pending, suppliers_payload
    from app.services.receipt_store import get_receipt_store

    # A startup snapshot must never be returned for another tenant or a
    # replacement store/scorer. Those contexts retain the live fallback.
    store, scorer, tenant = app_state.graph_store, app_state.scorer, current_tenant_id()

    def inputs(shared: dict[str, Any]) -> dict[str, Any]:
        if "view" not in shared:
            shared["view"] = SnapshotStore(shared)
            shared["normalized"] = merge_decisions(shared["decisions"], shared["verified"])
        return shared

    def invoices(shared: dict[str, Any]) -> list[dict[str, Any]]:
        # Preserve invoice-file provenance of compliance and fingerprints;
        # graph-backed previews must not depend on invoice files being present.
        if "invoices" not in shared:
            shared["invoices"] = load_invoices()
        return cast(list[dict[str, Any]], shared["invoices"])

    def queue(shared: dict[str, Any]) -> dict[str, Any]:
        s = inputs(shared)
        return cast(dict[str, Any], queue_payload(*score_pending(s["normalized"], app_state.scorer)))

    def conservation(shared: dict[str, Any]) -> dict[str, Any]:
        from app.routers.s2p import _conservation_state_from_rows

        s = inputs(shared)
        counts: dict[str, Any] = dict(_read_conservation_counts(s["view"], "s2p"))
        state = _conservation_state_from_rows(counts, s["verified"])
        return cast(dict[str, Any], conservation_payload(state))

    def supplier_profiles(shared: dict[str, Any]) -> list[dict[str, Any]]:
        s = inputs(shared)
        if "normalized_supplier_profiles" not in s:
            s["normalized_supplier_profiles"] = build_profiles(s["normalized"])
        return cast(list[dict[str, Any]], s["normalized_supplier_profiles"])

    def supplier_profile_snapshot(shared: dict[str, Any]) -> dict[str, Any]:
        profiles = supplier_profiles(shared)
        return {"profiles": profiles, "count": len(profiles), "status": "ready"}

    def fingerprints(shared: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for invoice in invoices(shared):
            for name in ("invoice_id", "event_id"):
                identity = invoice.get(name)
                if identity and str(identity) not in result:
                    result[str(identity)] = fingerprint_payload(invoice, str(identity))
        return result

    def conservation_snapshots(shared: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = conservation(shared)
        verified_count = int(payload.get("verified_count") or 0)
        correct_count = int(payload.get("correct_count") or 0)
        total_decisions = len(cast(list[dict[str, Any]], shared["decisions"]))
        status = str(payload.get("status") or "UNKNOWN")
        return (
            {
                "status": status,
                "verified_count": verified_count,
                "correct_count": correct_count,
                "total_decisions": total_decisions,
            },
            {
                "state": status,
                "verified_count": verified_count,
                "correct_count": correct_count,
                "total_decisions": total_decisions,
            },
        )

    def receipt_snapshot(shared: dict[str, Any]) -> dict[str, Any]:
        if "receipt_snapshot" not in shared:
            store = get_receipt_store()
            shared["receipt_snapshot"] = {
                "receipts": store.get_chain(limit=SCREENING_RECEIPT_LIMIT),
                "chain": store.verify_chain(),
                "stats": store.stats,
            }
        return cast(dict[str, Any], shared["receipt_snapshot"])

    def audit_pack(shared: dict[str, Any]) -> dict[str, Any]:
        snapshot = receipt_snapshot(shared)
        evidence_conservation, _ = conservation_snapshots(shared)
        receipts = cast(list[dict[str, Any]], snapshot["receipts"])[-100:]
        return _audit_pack_payload(
            receipts,
            cast(dict[str, Any], snapshot["stats"]),
            cast(dict[str, Any], snapshot["chain"]),
            evidence_conservation,
        )

    def governance_screening(shared: dict[str, Any]) -> dict[str, Any]:
        snapshot = receipt_snapshot(shared)
        _, governance_conservation = conservation_snapshots(shared)
        return _screening_payload(
            cast(list[dict[str, Any]], snapshot["receipts"]),
            cast(dict[str, Any], snapshot["chain"]),
            cast(dict[str, Any], snapshot["stats"]),
            governance_conservation,
        )

    return ResponseMaterializer(
        "s2p", lambda: app_state.graph_store,
        {
            "preview_queue": queue,
            "supplier_profiles": supplier_profile_snapshot,
            "preview_suppliers": lambda s: suppliers_payload(preview_profiles(supplier_profiles(s))),
            "performance_summary": lambda s: _build_summary(S2PGraphReader(inputs(s)["view"])),
            "audit_pack": audit_pack,
            "governance_compliance_screening": governance_screening,
            "conservation_status": conservation,
            "evidence_compliance": lambda s: compliance_payload(invoices(s), inputs(s)["view"]),
            "insight_fingerprint": fingerprints,
        }, ttl=60.0, available=lambda: (
            app_state.graph_store is store and app_state.scorer is scorer
            and current_tenant_id() == tenant
        ),
    )
