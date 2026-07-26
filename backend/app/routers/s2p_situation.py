"""S2P situation endpoint for category-specific context traversal."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from copilot_sdk.situation import NLRenderer, SituationAnalyzer

from app.domains.s2p.config import S2PDomainConfig
from app.services.situation_traversals import (
    SITUATION_NL_TEMPLATES,
    S2P_TRAVERSAL_PATTERNS,
    build_context_chain,
    pattern_for_category,
)

router = APIRouter(prefix="/api/s2p", tags=["s2p-situation"])
_renderer = NLRenderer(SITUATION_NL_TEMPLATES)
_PROVENANCE_ORDER = {"sample": 0, "context": 1, "proven": 2, "learned": 3}


@router.get("/situation/{decision_id}")
async def get_situation(
    decision_id: str,
    request: Request,
    max_depth: int = 3,
) -> dict[str, Any]:
    """Traverse graph context for a scored decision and render NL explanation."""
    depth = _bounded_depth(max_depth)
    graph_store = _graph_store(request)
    if graph_store is None:
        raise HTTPException(status_code=503, detail="Graph store unavailable")
    try:
        decision = _decision(graph_store, decision_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Decision graph unavailable") from exc
    if decision is None:
        raise HTTPException(status_code=404, detail=f"decision_id not found: {decision_id}")
    category = str(decision.get("category") or "")
    if category not in S2PDomainConfig.categories:
        raise HTTPException(status_code=400, detail=f"unsupported category: {category}")
    if pattern_for_category(category) is None:
        raise HTTPException(status_code=400, detail=f"no traversal pattern for category: {category}")

    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    analyzer = SituationAnalyzer(
        S2P_TRAVERSAL_PATTERNS,
        default_max_depth=3,
        max_allowed_depth=3,
    )
    intent = analyzer.normalize_signal(
        {
            "domain": "s2p",
            "intent_type": "situation_context",
            "verb": "explain",
            "subject": "decision",
            "decision_id": decision_id,
            "scope": {
                "decision_id": decision_id,
                "category": category,
                "invoice_id": metadata.get("invoice_id") or decision.get("entity_id"),
            },
            "payload": {**dict(metadata), **decision},
        }
    )
    try:
        context = analyzer.analyze_intent(intent, graph_store=graph_store, max_depth=depth)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Decision graph unavailable") from exc
    template_vars = context.metadata.get("template_variables")
    variables = dict(template_vars) if isinstance(template_vars, dict) else {}
    confidence = _float(decision.get("confidence"), 0.0)
    variables["confidence"] = confidence
    variables["confidence_pct"] = _confidence_pct(confidence)
    variables["action"] = str(decision.get("recommended_action") or variables.get("action") or "unknown")
    dk_weights = _dk_weights(request)
    rendered = _renderer.render(category, variables, dk_weights=dk_weights)
    nl_explanation = rendered.rendered
    chain = build_context_chain(context, nl_explanation=nl_explanation)
    context_chain = [
        {
            "node": node.type,
            "id": node.id,
            "properties": node.properties,
            "depth": node.depth,
            "provenance": _node_provenance(node),
        }
        for node in context.nodes
    ]
    overall_provenance = _weakest([item["provenance"] for item in context_chain])
    explanation_provenance = _weakest([item["provenance"] for item in context_chain])
    confidence_provenance = _confidence_provenance(decision)

    return {
        "decision_id": decision_id,
        "category": category,
        "context_chain": context_chain,
        "nl_explanation": nl_explanation,
        "confidence": confidence,
        "factors_used": list(context.metadata.get("factors_used") or []),
        "traversal_depth": chain.hop_count,
        "context_available": bool(context.metadata.get("context_available", True)),
        "warnings": list(context.warnings),
        "template_variables": chain.template_variables,
        "missing_variables": list(rendered.missing_variables),
        "situation_context": context.to_dict(),
        "provenance": {
            "nl_explanation": explanation_provenance,
            "confidence": confidence_provenance,
            "overall": overall_provenance,
        },
    }


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _decision(graph_store: Any | None, decision_id: str) -> dict[str, Any] | None:
    get_decision = getattr(graph_store, "get_decision", None)
    if not callable(get_decision):
        return None
    try:
        decision = get_decision(str(decision_id), domain="s2p")
    except Exception as exc:
        raise RuntimeError("S2P decision graph lookup failed") from exc
    if isinstance(decision, dict) and decision.get("domain") not in (None, "s2p"):
        raise RuntimeError("S2P decision lookup returned a foreign domain")
    return decision if isinstance(decision, dict) else None


def _dk_weights(request: Request) -> dict[str, Any] | None:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    get_dk_weights = getattr(scorer, "get_dk_weights", None)
    if not callable(get_dk_weights):
        return None
    try:
        weights = get_dk_weights()
    except Exception:
        return None
    if weights is None:
        return None
    return {"weights": weights, "factor_names": list(S2PDomainConfig.factors)}


def _bounded_depth(value: Any) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = 3
    return max(0, min(depth, 3))


def _node_provenance(node: Any) -> str:
    properties = getattr(node, "properties", None)
    if not isinstance(properties, dict):
        properties = {}
    raw = (
        properties.get("provenance")
        or properties.get("source")
        or properties.get("provenance_tier")
        or getattr(node, "source", None)
    )
    return _tier_from_source(raw)


def _confidence_provenance(decision: dict[str, Any]) -> str:
    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    raw = (
        decision.get("confidence_provenance")
        or metadata.get("confidence_provenance")
        or decision.get("provenance")
        or metadata.get("provenance")
        or "learned"
    )
    return _tier_from_source(raw)


def _tier_from_source(source: Any) -> str:
    normalized = str(source or "").strip().lower()
    if normalized in _PROVENANCE_ORDER:
        return normalized
    if normalized in {"fixture", "demo", "sample_data", "synthetic"}:
        return "sample"
    if normalized in {"graph_store", "graph", "enriched", "external", "scraped_external", "cached", "index", "feed"}:
        return "context"
    if normalized in {"verified", "audited", "receipt", "proof"}:
        return "proven"
    if normalized in {"decision", "decisions", "centroid", "centroids", "real_measured", "learned"}:
        return "learned"
    return "sample"


def _weakest(tiers: list[str]) -> str:
    if not tiers:
        return "sample"
    return min(tiers, key=lambda tier: _PROVENANCE_ORDER.get(tier, 0))


def _confidence_pct(value: Any) -> str:
    return f"{round(max(0.0, min(_float(value, 0.0), 1.0)) * 100.0):.0f}%"


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
