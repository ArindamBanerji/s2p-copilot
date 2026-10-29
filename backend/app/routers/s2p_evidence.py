"""S2P evidence endpoints for audit, lifecycle, and compliance views."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from typing import Any, cast
from sqlite3 import OperationalError as SQLiteOperationalError

from psycopg import InterfaceError, OperationalError
from ci_platform.graph.age_graph_store import GraphUnavailableError as AGEGraphUnavailableError

from fastapi import APIRouter, HTTPException, Request

from copilot_sdk.state.cached_static import cached_static

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import FACTOR_NAMES, compute_all_factors
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.models.responses import GenericResponse
from app.services.graph_timeout import GraphTimeoutError, with_graph_timeout
from app.services.response_materializer import materialized_response
from app.routers.s2p_data_helpers import load_invoices
from app.services.receipt_store import get_receipt_store
from app.services.extinction_evidence import extinction_history
from app.services.s2p_evidence_templates import S2PEvidenceEngine, evidence_context_from_record
from app.services.s2p_situation_pattern import S2PInvoiceTraversalPattern
from app.services.s2p_trust_explanations import format_trust_explanation
from copilot_sdk.situation import SituationAnalyzer, SituationContext

router = APIRouter(prefix="/api/s2p/evidence", tags=["s2p-evidence"])

AUDIT_PACK_TIMEOUT_SECONDS = 4.0
_GRAPH_CALL_FAILED = object()

_load_invoices = load_invoices
_evidence_engine = S2PEvidenceEngine()
_CONTEXT_CACHE_TTL_SECONDS = 120.0
_context_cache: dict[str, tuple[float, SituationContext]] = {}
_context_cache_lock = RLock()


def clear_evidence_context_cache() -> None:
    """Drop request-derived context after a score/learning mutation."""
    with _context_cache_lock:
        _context_cache.clear()


def _cached_situation_context(invoice_id: str | None, graph_store: Any | None) -> SituationContext | None:
    if not invoice_id:
        return None
    cache_key = f"{id(graph_store)}:{invoice_id}"
    with _context_cache_lock:
        cached = _context_cache.get(cache_key)
        if cached is None:
            return None
        created, context = cached
        if monotonic() - created > _CONTEXT_CACHE_TTL_SECONDS:
            _context_cache.pop(cache_key, None)
            return None
        return context


def _store_situation_context(invoice_id: str | None, graph_store: Any | None, context: SituationContext) -> None:
    if not invoice_id:
        return
    cache_key = f"{id(graph_store)}:{invoice_id}"
    with _context_cache_lock:
        _context_cache[cache_key] = (monotonic(), context)


def _invoice_by_id(invoice_id: str) -> dict[str, Any] | None:
    for invoice in _load_invoices():
        if invoice.get("invoice_id") == invoice_id or invoice.get("event_id") == invoice_id:
            return cast(dict[str, Any], invoice)
    return None


def _graph_store(request: Request) -> Any | None:
    try:
        graph_store = request.app.state.graph_store
    except AttributeError:
        graph_store = None
    if graph_store is not None:
        return graph_store
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _scorer(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    return getattr(state, "scorer", None)


def _graph_reader(request: Request) -> S2PGraphReader:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    graph_store = getattr(scorer, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader) and reader.store is graph_store:
        return reader
    if graph_store is None:
        raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
    return S2PGraphReader(store=graph_store)


def _trust_explanation_payload(
    request: Request,
    *,
    category: str,
    action: str,
    confidence: float,
    variables: dict[str, Any],
) -> dict[str, Any]:
    scorer = _scorer(request)
    category_index = _category_index(category)
    weights = _call_or_none(getattr(scorer, "get_dk_weights", None))
    phase = _call_or_none(getattr(scorer, "get_category_phase", None), category)
    verified_count = _call_or_none(_scorer_method(scorer, "get_verified_count"))
    centroid = _call_or_none(getattr(scorer, "get_centroid", None), category, action)
    graph_available = all(
        value is not _GRAPH_CALL_FAILED
        for value in (weights, phase, verified_count, centroid)
    )
    weights = None if weights is _GRAPH_CALL_FAILED else weights
    phase = None if phase is _GRAPH_CALL_FAILED else phase
    verified_count = None if verified_count is _GRAPH_CALL_FAILED else verified_count
    centroid = None if centroid is _GRAPH_CALL_FAILED else centroid
    if centroid is None:
        centroid = (
            S2PDomainConfig.get_initial_centroids()
            .get(category, {})
            .get(action)
        )
    explanation = format_trust_explanation(
        category=category,
        recommended_action=action,
        confidence=confidence,
        factor_values=variables.get("factors") if isinstance(variables.get("factors"), dict) else {},
        factor_names=list(S2PDomainConfig.factors),
        dk_weights=weights,
        centroid=centroid,
        category_index=category_index,
        phase=str(phase) if phase else None,
        verified_count=verified_count if isinstance(verified_count, int) else None,
        verified_target=200,
    )
    payload = explanation.to_dict()
    factor_value_provenance = _provenance(
        "context",
        "factor value · scorer input context",
    )
    dk_weight_provenance = (
        _provenance("scorer", "DK trust weight · learned from verified outcomes")
        if payload.get("trust_available")
        else _provenance("unavailable", "DK trust weight · learning")
    )
    for factor in payload.get("factors", []):
        if isinstance(factor, dict):
            factor.update(factor_value_provenance)
            factor["factor_value_provenance"] = dict(factor_value_provenance)
            factor["dk_weight_provenance"] = dict(dk_weight_provenance)
    payload["provenance"] = factor_value_provenance
    payload["dk_weight_provenance"] = dk_weight_provenance
    if not graph_available:
        payload["graph_available"] = False
    return cast(dict[str, Any], payload)


def _category_index(category: str) -> int | None:
    try:
        return cast(int, S2PDomainConfig.get_category_index(category))
    except (ValueError, AttributeError):
        return None


def _call_or_none(fn: Any, *args: Any) -> Any | None:
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        return _GRAPH_CALL_FAILED


def _scorer_method(scorer: Any, name: str) -> Any:
    return getattr(scorer, name, None) if scorer is not None else None


def _provenance(source: str, label: str) -> dict[str, Any]:
    if source == "scorer":
        return {
            "source": "scorer",
            "provenance_label": label,
            "provenance_tier": "learned",
            "integration_status": "configured",
            "measured": True,
            "display_prefix": "██ learned",
        }
    if source == "unavailable":
        return {
            "source": "unavailable",
            "provenance_label": label,
            "provenance_tier": "unavailable",
            "integration_status": "pending",
            "measured": False,
            "display_prefix": "integration pending",
        }
    response = {
        "source": source,
        "provenance_label": label,
        "provenance_tier": "context",
        "integration_status": "pending",
        "measured": False,
        "display_prefix": "░░ context",
    }
    return response


def _conservation_snapshot(request: Request) -> dict[str, Any]:
    try:
        from app.routers.s2p import _current_conservation_status, _graph_verified_counts
    except Exception:
        return {}

    try:
        verified_count, correct_count = _graph_verified_counts(request)
        total_decisions = len(_graph_reader(request).get_all_decisions())
        return {
            "status": _current_conservation_status(request),
            "verified_count": verified_count,
            "correct_count": correct_count,
            "total_decisions": max(total_decisions, 0),
        }
    except GraphUnavailableError:
        raise
    except Exception:
        return {}


def _override_distribution(receipts: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for receipt in receipts:
        if receipt.get("human_action") != receipt.get("scored_action"):
            reason = receipt.get("override_reason") or "unspecified"
            counter[str(reason)] += 1
    return dict(sorted(counter.items()))


def _decision_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _decision_matches_invoice(
    decision: dict[str, Any],
    invoice_id: str,
) -> bool:
    metadata = _decision_metadata(decision)
    return (
        decision.get("entity_id") == invoice_id
        or decision.get("decision_id") == invoice_id
        or decision.get("invoice_id") == invoice_id
        or decision.get("source_invoice_id") == invoice_id
        or metadata.get("invoice_id") == invoice_id
        or metadata.get("entity_id") == invoice_id
        or metadata.get("source_invoice_id") == invoice_id
    )


def _enrich_decision_invoice_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(decision)
    metadata = dict(enriched.get("metadata") or {})
    enriched["metadata"] = metadata
    for key in ("invoice_id", "source_invoice_id", "supplier_id", "supplier_name", "amount"):
        value = metadata.get(key) or enriched.get(key)
        if value is not None:
            enriched[key] = value
    return enriched


def _graph_linked_decisions(reader: S2PGraphReader, invoice_id: str) -> list[dict[str, Any]]:
    links = reader.get_decision_links()

    linked_decision_ids: list[str] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("edge_type") != "DECIDED_ON" or link.get("entity_id") != invoice_id:
            continue
        decision_id = link.get("decision_id")
        if isinstance(decision_id, str) and decision_id not in seen:
            seen.add(decision_id)
            linked_decision_ids.append(decision_id)

    decisions: list[dict[str, Any]] = []
    for decision_id in linked_decision_ids:
        decision = reader.get_decision(decision_id)
        if isinstance(decision, dict):
            decisions.append(_enrich_decision_invoice_metadata(decision))
    return decisions


@router.get("/audit-trail/{invoice_id}", response_model=GenericResponse)
def audit_trail(invoice_id: str, request: Request) -> dict[str, Any]:
    reader = _graph_reader(request)
    try:
        decisions = _graph_linked_decisions(reader, invoice_id)
        if not decisions:
            decisions = [
                _enrich_decision_invoice_metadata(decision)
                for decision in reader.get_all_decisions()
                if isinstance(decision, dict) and _decision_matches_invoice(decision, invoice_id)
            ]
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for evidence") from exc
    return {"invoice_id": invoice_id, "decisions": decisions, "count": len(decisions)}


@router.get("/receipts", response_model=GenericResponse)
def receipts(limit: int = 50) -> dict[str, Any]:
    store = get_receipt_store()
    return {"receipts": store.get_chain(limit=limit), "stats": store.stats}


@router.get("/receipts/{invoice_id}", response_model=GenericResponse)
def receipts_for_invoice(invoice_id: str) -> dict[str, Any]:
    invoice_receipts = get_receipt_store().get_for_invoice(invoice_id)
    if not invoice_receipts:
        raise HTTPException(status_code=404, detail=f"Invoice not found: {invoice_id}")
    receipt = dict(invoice_receipts[-1])
    factor_vector = receipt.get("factor_vector")
    raw_factors = receipt.get("factors")
    factors = (
        list(factor_vector)
        if isinstance(factor_vector, list)
        else list(raw_factors.values()) if isinstance(raw_factors, dict) else []
    )
    return {
        **receipt,
        "invoice_id": invoice_id,
        "outcome": "correct" if receipt.get("is_correct") else "incorrect",
        "decision_id": str(receipt.get("decision_id") or ""),
        "factors": factors,
        "pd_audit": {
            "receipt_id": receipt.get("receipt_id"),
            "receipt_hash": receipt.get("receipt_hash"),
            "previous_hash": receipt.get("previous_hash"),
            "conservation_state_before": receipt.get("conservation_state_before"),
            "conservation_state_after": receipt.get("conservation_state_after"),
        },
        "timestamp": receipt.get("timestamp"),
        "amount": float(receipt.get("amount") or 0.0),
        "receipts": invoice_receipts,
    }


@router.get("/receipts/decision/{decision_id}", response_model=GenericResponse)
def receipts_for_decision(decision_id: str) -> dict[str, Any]:
    store = get_receipt_store()
    decision_receipts = store.get_for_decision(decision_id)
    if not decision_receipts:
        raise HTTPException(status_code=404, detail=f"No receipts for decision {decision_id}")
    return {"decision_id": decision_id, "receipts": decision_receipts}


@router.get("/chain-integrity", response_model=GenericResponse)
@cached_static("evidence-chain-integrity", copilot="s2p")
def chain_integrity() -> dict[str, Any]:
    return cast(dict[str, Any], get_receipt_store().verify_chain())


def _audit_pack_sync(request: Request, limit: int = 100) -> dict[str, Any]:
    try:
        if limit == 100:
            materialized = materialized_response(request, "audit_pack")
            if materialized is not None:
                return cast(dict[str, Any], materialized)
        store = get_receipt_store()
        receipts_payload = store.get_chain(limit=limit)
        stats = store.stats
        return _audit_pack_payload(
            receipts_payload,
            stats,
            store.verify_chain(),
            _conservation_snapshot(request),
        )
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for evidence") from exc


@router.get("/audit-pack", response_model=GenericResponse)
async def audit_pack(request: Request, limit: int = 100) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_audit_pack_sync, request, limit),
            timeout=AUDIT_PACK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="S2P graph unavailable for evidence",
        ) from exc
    except GraphUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"S2P graph unavailable for evidence: {exc}",
        ) from exc


def _audit_pack_payload(
    receipts_payload: list[dict[str, Any]],
    stats: dict[str, Any],
    chain_integrity: dict[str, Any],
    conservation_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the audit response from snapshots already read by the caller."""
    return {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "receipt_count": stats["total_receipts"],
        "chain_integrity": chain_integrity,
        "conservation_state": conservation_state,
        "override_distribution": _override_distribution(receipts_payload),
        "override_count": stats["overrides"],
        "confirm_count": stats["confirms"],
        "receipts": receipts_payload,
    }


@router.get("/template", response_model=GenericResponse)
def evidence_template(
    request: Request,
    category: str,
    invoice_id: str | None = None,
) -> dict[str, Any]:
    invoice = _invoice_by_id(invoice_id) if invoice_id else None
    invoice_found = invoice is not None
    if invoice is None:
        invoice = {
            "category": category,
            "factors": {},
        }
        if invoice_id:
            invoice.update({"invoice_id": invoice_id, "event_id": invoice_id})
    graph_store = _graph_store(request)
    try:
        reader = _graph_reader(request)
    except HTTPException:
        reader = None
        graph_store = None
    analyzer = SituationAnalyzer(
        [
            S2PInvoiceTraversalPattern(
                scorer=_scorer(request),
                reader=reader,
            )
        ],
        default_max_depth=3,
        max_allowed_depth=5,
    )
    scope = {"category": category}
    if invoice_id:
        scope["invoice_id"] = invoice_id
    intent = analyzer.normalize_signal(
        {
            "domain": "s2p",
            "intent_type": "evidence_template",
            "verb": "explain",
            "subject": "invoice",
            "source_event_id": invoice_id,
            "scope": scope,
            "payload": invoice,
        }
    )
    situation_context = _cached_situation_context(invoice_id, graph_store)
    if situation_context is None:
        situation_context = analyzer.analyze_intent(intent, graph_store=graph_store, max_depth=3)
        _store_situation_context(invoice_id, graph_store, situation_context)
    variables = evidence_context_from_record(invoice)
    context_used = situation_context.metadata.get("context_used")
    if isinstance(context_used, dict):
        variables.update({key: value for key, value in context_used.items() if value is not None})
    action = (
        str(variables.get("action") or invoice.get("ground_truth_action") or "unknown")
    )
    confidence = float(variables.get("score") or 0.0)
    evidence = _evidence_engine.render(category, variables, action, confidence)
    evidence_payload = evidence.to_dict()
    trust_explanation = _trust_explanation_payload(
        request,
        category=category,
        action=action,
        confidence=confidence,
        variables=variables,
    )
    evidence_payload["trust_explanation"] = trust_explanation
    evidence_payload["trust_weighted_factors"] = trust_explanation["factors"]
    evidence_payload["trust_available"] = trust_explanation["trust_available"]
    evidence_payload["trust_learning_message"] = trust_explanation["learning_message"]
    if not invoice_id and "invoice_id" not in evidence_payload["missing_fields"]:
        evidence_payload["missing_fields"].append("invoice_id")
        evidence_payload["missing_fields"].sort()
    template = evidence.template or ""
    rendered = evidence.text
    response = {
        "invoice_id": invoice.get("invoice_id") or invoice_id or "unknown",
        "invoice_found": invoice_found,
        "category": category,
        "template": template,
        "rendered": rendered,
        "variables": variables,
        "evidence": evidence_payload,
        "trust_explanation": trust_explanation,
        "trust_weighted_factors": trust_explanation["factors"],
        "trust_available": trust_explanation["trust_available"],
        "trust_learning_message": trust_explanation["learning_message"],
        "situation_context": situation_context.to_dict(),
    }
    if trust_explanation.get("graph_available") is False:
        response["graph_available"] = False
    return response


@router.get("/rules", response_model=GenericResponse)
@cached_static("evidence-rules", copilot="s2p")
def rules() -> dict[str, Any]:
    return _rules_payload()


def _rules_payload() -> dict[str, Any]:
    ruleset = [
        {
            "rule_id": "S2P-RULE-EXCEPTION-CLUSTER",
            "name": "Supplier exception cluster threshold",
            "state": "promoted",
            "action": "hold_for_review",
            "factor": "supplier_exception_history",
        },
        {
            "rule_id": "S2P-RULE-COMMODITY-DRIFT",
            "name": "Commodity volatility drift review",
            "state": "shadow",
            "action": "flag_leakage",
            "factor": "commodity_index_correlation",
        },
        {
            "rule_id": "S2P-RULE-TAX-CHECK",
            "name": "Tax and withholding completeness",
            "state": "proposed",
            "action": "refer_to_specialist",
            "factor": "tax_regulatory_compliance",
        },
        {
            "rule_id": "S2P-RULE-LOW-CONFIDENCE-AUTO",
            "name": "Reject low-confidence auto approval",
            "state": "rejected",
            "action": "auto_approve",
            "factor": "match_status",
        },
    ]
    return {
        "rules": ruleset,
        "count": len(ruleset),
        "source": "fixture",
        "note": "Based on seeded evolution data.",
    }


@router.get("/rule-lifecycle", response_model=GenericResponse)
def rule_lifecycle() -> dict[str, Any]:
    ruleset = cast(list[dict[str, Any]], _rules_payload()["rules"])
    stage_names = ("proposed", "shadow", "promoted", "rejected")
    stages = [
        {
            "stage": stage,
            "count": sum(rule.get("state") == stage for rule in ruleset),
        }
        for stage in stage_names
    ]
    active_rules = [
        rule for rule in ruleset if rule.get("state") in {"shadow", "promoted"}
    ]
    promotions = [
        {
            "rule_id": rule.get("rule_id"),
            "name": rule.get("name"),
            "stage": "promoted",
        }
        for rule in ruleset
        if rule.get("state") == "promoted"
    ]
    ledger = [
        {
            "rule_id": rule.get("rule_id"),
            "stage": rule.get("state"),
            "action": rule.get("action"),
            "factor": rule.get("factor"),
        }
        for rule in ruleset
    ]
    return {
        "rules": ruleset,
        "stages": stages,
        "active_rules": active_rules,
        "promotions": promotions,
        "evolution_ledger": ledger,
        "source": "seeded_evolution_data",
    }


@router.get("/compliance", response_model=GenericResponse)
def compliance(request: Request) -> dict[str, Any]:
    materialized = materialized_response(request, "evidence_compliance")
    if materialized is not None:
        return cast(dict[str, Any], materialized)
    try:
        result = with_graph_timeout(
            compliance_payload,
            _load_invoices(),
            _graph_store(request),
            label="evidence_compliance",
        )
        return cast(dict[str, Any], result)
    except GraphTimeoutError:
        raise HTTPException(
            status_code=503,
            detail="S2P graph unavailable for compliance",
        ) from None


def compliance_payload(invoices: list[dict[str, Any]], graph_store: Any) -> dict[str, Any]:
    flagged: list[dict[str, Any]] = []
    compliant = 0
    compliance_available = True
    degraded_factors: list[str] = []
    for invoice in invoices:
        try:
            factor_result = compute_all_factors(invoice, return_status=True)
        except (ValueError, RuntimeError):
            factor_result = ({}, False, list(FACTOR_NAMES))
        if (
            not isinstance(factor_result, tuple)
            or len(factor_result) != 3
            or not isinstance(factor_result[0], dict)
        ):
            compliance_available = False
            degraded_factors.append("tax_regulatory_compliance")
            factors = {}
        else:
            factors = cast(dict[str, float], factor_result[0])
            factors_available = bool(factor_result[1])
            factor_names = list(factor_result[2])
            if not factors_available:
                compliance_available = False
                degraded_factors.extend(factor_names)
        raw_tax_score = factors.get("tax_regulatory_compliance", 1.0)
        tax_score = float(raw_tax_score) if isinstance(raw_tax_score, (int, float)) else 1.0
        if not isinstance(raw_tax_score, (int, float)):
            compliance_available = False
            degraded_factors.append("tax_regulatory_compliance")
        is_compliant = tax_score < 0.3
        compliant += int(is_compliant)
        if not is_compliant:
            flagged.append(
                {
                    "invoice_id": invoice.get("invoice_id"),
                    "category": invoice.get("category"),
                    "supplier_id": invoice.get("supplier_id"),
                    "tax_regulatory_compliance": tax_score,
                    "recommended_action": invoice.get("ground_truth_action"),
                }
            )
    total = len(invoices)
    categories = list(S2PDomainConfig.categories)
    # Keep a compact, observed class timeline for the demo panel.  The input
    # order is the fixture's event order; each bucket is a rolling slice of
    # the same invoice stream, so no synthetic decisions are introduced.
    bucket_count = 5
    bucket_size = max(1, (len(invoices) + bucket_count - 1) // bucket_count)
    class_timeline: dict[str, list[int]] = {}
    for category in categories:
        class_timeline[category] = [
            sum(1 for invoice in invoices[start : start + bucket_size] if invoice.get("category") == category)
            for start in range(0, len(invoices), bucket_size)
        ][:bucket_count]
        while len(class_timeline[category]) < bucket_count:
            class_timeline[category].append(0)
    invoice_class_trends: list[dict[str, Any]] = []
    for category, timeline in class_timeline.items():
        if timeline and timeline[-1] < timeline[0]:
            invoice_class_trends.append(
                {
                    "category": category,
                    "last_seen_decision": sum(timeline[:-1]),
                    "current_decision": sum(timeline),
                    "trend": "declining",
                }
            )
    extinction_classes: list[dict[str, Any]] = []
    extinction_available = False
    if graph_store is not None:
        try:
            history = extinction_history(graph_store)
            if isinstance(history, list) and all(isinstance(row, dict) for row in history):
                extinction_classes = history
                extinction_available = True
        except (OSError, GraphUnavailableError, AGEGraphUnavailableError,
                OperationalError, InterfaceError, SQLiteOperationalError):
            # Invoice-derived compliance evidence remains valid if optional
            # extinction-history storage is unavailable.
            pass
    return {
        "total": total,
        "compliant": compliant,
        "compliant_pct": round(compliant / total, 4) if total else 0.0,
        "flagged_count": len(flagged),
        "flagged_invoices": flagged[:20],
        "factor": S2PDomainConfig.factors[-1],
        "extinction_classes": extinction_classes,
        "extinct_classes": extinction_classes,
        "queue_extinctions": extinction_classes,
        "extinction_available": extinction_available,
        "invoice_class_trends": invoice_class_trends,
        "class_timeline": class_timeline,
        "evidence_tier": "T_S",
        "extinction_source": "persisted_verified_outcome_transitions" if extinction_available else "",
        "extinction_note": "Historical queue-depletion events; not a claim of permanent class elimination.",
        "compliance_available": compliance_available,
        "degraded_factors": sorted(set(degraded_factors)),
    }
