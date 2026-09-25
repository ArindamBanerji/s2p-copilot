"""Preseed the S2P demo state that is representable by the live API.

This script deliberately reports, rather than fabricates, requirements that
the current API contract cannot expose. It never writes directly to backend
storage, and it is idempotent when the decision listing endpoint is available.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "data" / "demo_fixtures" / "s2p_demo_decisions.json"
DEFAULT_API = "http://127.0.0.1:8002"
VALID_ACTIONS = {
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
}


def _load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"fixture must contain an object: {FIXTURE_PATH}")
    return value


def _request_json(
    api: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | None, str | None]:
    data = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
    target = urlsplit(api.rstrip("/") + path)
    if target.scheme not in {"http", "https"} or target.hostname is None:
        return 0, None, "API URL must use HTTP or HTTPS and include a hostname"
    connection_type = (
        http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(target.hostname, target.port, timeout=30)
    try:
        # Consume the response before closing. urllib forces Connection: close,
        # which truncates large responses on the local Windows demo server.
        request_path = target.path + ("?" + target.query if target.query else "")
        connection.request(method, request_path, body=data, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read()
        if response.status >= 400:
            return response.status, None, raw.decode("utf-8", errors="replace")[:300]
        return response.status, json.loads(raw), None
    except (http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
        return 0, None, str(exc)
    finally:
        connection.close()


def _decision_rows(api: str) -> list[dict[str, Any]]:
    status, body, error = _request_json(api, "GET", "/api/demo/s2p/seed-decisions")
    if status != 200 or not isinstance(body, dict):
        raise ValueError(f"Cannot safely resume seed: decision lookup returned {status}: {error}")
    rows = body.get("decisions")
    if (not isinstance(rows, list) or body.get("complete") is not True
            or body.get("total") != len(rows) or any(not isinstance(row, dict) for row in rows)):
        raise ValueError("Cannot safely resume seed: decision inventory is incomplete")
    return rows


def _existing_decisions(api: str) -> dict[str, dict[str, Any]]:
    existing: dict[str, dict[str, Any]] = {}
    for row in _decision_rows(api):
        if not isinstance(row, dict) or not row.get("decision_id"):
            continue
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        event_id = row.get("event_id") or row.get("invoice_id") or metadata.get("invoice_id")
        if event_id:
            existing[str(event_id)] = {
                **row, "action": row.get("action") or row.get("recommended_action"),
                "factor_vector": row.get("factor_vector") or metadata.get("factor_vector"),
            }
    return existing


def _score(
    api: str,
    payload: dict[str, Any],
    existing: dict[str, dict[str, Any]],
    report: dict[str, Any],
) -> dict[str, Any] | None:
    event_id = str(payload["event_id"])
    if event_id in existing:
        report["skipped_existing"].append(event_id)
        return existing[event_id]
    payload = {**payload, "context": {**dict(payload.get("context") or {}),
                                     "origin": "s2p_demo_preseed", "planted": True, "evidence_tier": "T_S"}}
    status, body, error = _request_json(api, "POST", "/api/s2p/score", payload)
    if status != 200 or not isinstance(body, dict):
        report["errors"].append({"event_id": event_id, "status": status, "detail": error})
        return None
    if not body.get("decision_id"):
        report["errors"].append({"event_id": event_id, "status": status, "detail": "score returned no decision_id"})
        return None
    existing[event_id] = body
    report["scored"].append(event_id)
    return body


def _learn(api: str, score: dict[str, Any], report: dict[str, Any]) -> None:
    if score.get("status") in {"confirmed", "overridden"}:
        return
    action = str(score.get("canonical_action") or score.get("action", ""))
    if action not in VALID_ACTIONS:
        report["learning_errors"].append({"decision_id": score.get("decision_id"),
                                          "detail": "No canonical action available for confirmation"})
        return
    payload = {
        "decision_id": score.get("decision_id"),
        "actual_action": action,
        "outcome": "confirmed",
        "context": {"origin": "s2p_demo_preseed", "planted": True, "evidence_tier": "T_S"},
    }
    status, body, error = _request_json(api, "POST", "/api/learn", payload)
    if status == 200 and isinstance(body, dict) and body.get("learning_applied") is True:
        report["learned"] += 1
        score["status"] = "confirmed"
    else:
        report["learning_errors"].append({"decision_id": score.get("decision_id"), "status": status, "detail": error or body})


def _warmup_payload(index: int, category: str, *, hard: bool) -> dict[str, Any]:
    if hard:
        values = {
            "match_status": 0.30,
            "amount_variance_ratio": 0.45,
            "duplicate_score": 0.85,
            "supplier_exception_history": 0.75,
            "payment_terms_impact": 0.70,
            "commodity_index_correlation": 0.15,
            "tax_regulatory_compliance": 0.35,
            "environmental_risk": 0.80,
        }
    else:
        values = {
            "match_status": 0.98,
            "amount_variance_ratio": 0.01,
            "duplicate_score": 0.02,
            "supplier_exception_history": 0.02,
            "payment_terms_impact": 0.20,
            "commodity_index_correlation": 0.90,
            "tax_regulatory_compliance": 0.98,
            "environmental_risk": 0.10,
        }
    return {
        # This version adds explicit synthetic provenance and causal outcomes.
        # Do not reuse historical v2 rows that predate the evidence writer.
        "event_id": f"S2P-DEMO-WARM-V3-{index:03d}",
        "category": category,
        "amount": 500.0 + index * 125.0,
        "supplier_id": f"SUP-WARM-{index:03d}",
        **values,
    }


def _warmup_category(index: int, categories: list[str]) -> str:
    eligible = categories if index < 10 else [
        category for category in categories if category != "format_compliance"
    ]
    if not eligible:
        raise ValueError("warmup requires non-format_compliance categories after decision 10")
    return eligible[(index if index < 10 else index - 10) % len(eligible)]


def _verified_warmup_category(index: int, categories: list[str]) -> str:
    """Use all classes during the verified seed, then retire format compliance."""
    if index < 10:
        if "format_compliance" not in categories:
            raise ValueError("verified warmup requires format_compliance")
        return categories[index % len(categories)]
    return _warmup_category(index, categories)


def _owned_format_seed(row: dict[str, Any]) -> bool:
    """Recognize this seeder's legacy rows by both invoice and supplier identity."""
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict) or row.get("category") != "format_compliance":
        return False
    event = str(row.get("event_id") or metadata.get("invoice_id") or "")
    match = re.fullmatch(r"S2P-DEMO-WARM-(?:V[23]-)?(\d{3})", event)
    if match:
        index = int(match[1])
        return index in {4, 9} and metadata.get("supplier_id") == f"SUP-WARM-{index:03d}"
    return (event.startswith("S2P-DEMO-EXTINCTION-")
            and metadata.get("supplier_id") == "SUP-DEMO-EXTINCTION"
            and metadata.get("planted") is True)


def _seed_extinction(api: str, existing: dict[str, dict[str, Any]], report: dict[str, Any]) -> None:
    """Resume only known seed outcomes; a planted final decision earns the event."""
    pending = [row for row in _decision_rows(api)
               if row.get("category") == "format_compliance" and row.get("status") == "pending"]
    blockers = [str(row["decision_id"]) for row in pending if not _owned_format_seed(row)]
    if blockers:
        report["limitations"].append(f"S2P-05: unrelated pending format decisions require review: {blockers}")
        return
    _, compliance, _ = _probe(api, "/api/s2p/evidence/compliance")
    if not pending and compliance and any(
        row.get("category") == "format_compliance" and row.get("earning_decision_id")
        and row.get("start_count", 0) > 0 and row.get("end_count") == 0
        for row in compliance.get("extinction_classes", []) if isinstance(row, dict)
    ):
        return
    # Keep a newly labelled decision pending while legacy rows are resolved.
    # Legacy decision/audit provenance is never rewritten or retroactively sealed.
    payload = {**_warmup_payload(4, "format_compliance", hard=False),
               "event_id": f"S2P-DEMO-EXTINCTION-{uuid4().hex}",
               "supplier_id": "SUP-DEMO-EXTINCTION"}
    final = _score(api, payload, existing, report)
    if final is None:
        return
    for row in pending:
        before = len(report["learning_errors"])
        _learn(api, {**row, "action": row.get("recommended_action") or row.get("action")}, report)
        if len(report["learning_errors"]) != before:
            return
    # Recheck the complete queue: concurrent/unrelated arrivals cannot be drained.
    remaining = [row for row in _decision_rows(api)
                 if row.get("category") == "format_compliance" and row.get("status") == "pending"]
    if {row["decision_id"] for row in remaining} != {final["decision_id"]}:
        report["limitations"].append("S2P-05: format queue changed during seed; final outcome deferred")
        return
    _learn(api, final, report)


def _investigation_warmup(
    api: str, candidates: list[tuple[str, dict[str, Any]]], report: dict[str, Any],
) -> None:
    for profile in dict.fromkeys(intended for intended, _ in candidates):
        selected = [score for intended, score in candidates if intended == profile][-2:]
        if not selected:
            report["limitations"].append(f"S2P-07: no scored {profile} candidates available")
        for score in selected:
            decision_id = score.get("decision_id")
            vector = score.get("factor_vector")
            category = score.get("category")
            if not decision_id or not isinstance(vector, list) or not vector or not category:
                report["investigation_errors"].append({
                    "profile": profile, "detail": "score response lacks decision_id/category/factor_vector",
                })
                continue
            status, body, error = _request_json(
                api,
                "POST",
                "/api/investigation/investigate",
                {"decision_id": decision_id, "category": category,
                 "factor_vector": vector, "use_K": False},
            )
            if status == 200:
                report["investigations"].append({
                    "intended_profile": profile, "decision_id": decision_id, "response": body,
                })
            else:
                report["investigation_errors"].append({"profile": profile, "status": status, "detail": error})


def _probe(api: str, path: str) -> tuple[int, dict[str, Any] | None, str | None]:
    status, body, error = _request_json(api, "GET", path)
    return status, body if isinstance(body, dict) else None, error


def _seed_boundary_allocations(api: str, existing: dict[str, dict[str, Any]], report: dict[str, Any]) -> None:
    """Investigate the actual scored vector before any learning moves its boundary."""
    for _ in range(2):
        status, body, error = _probe(api, "/api/demo/s2p/budget-candidates")
        if status != 200 or body is None:
            report["investigation_errors"].append({"status": status, "detail": error})
            return
        for profile in ("easy", "hard"):
            candidate = body["candidates"][profile]
            confidence = float(candidate["confidence"])
            if (profile == "hard" and confidence >= 0.65) or (profile == "easy" and confidence < 0.85):
                report["limitations"].append(f"S2P-07: no measured {profile} candidate; confidence={confidence}")
                continue
            payload = {"event_id": f"S2P-DEMO-BOUNDARY-{uuid4().hex}", "category": candidate["category"],
                       "amount": 1000, "supplier_id": "SUP-DEMO-BOUNDARY",
                       "supplier_risk_rating": 1.0 - candidate["factors"]["supplier_exception_history"],
                       **candidate["factors"]}
            score = _score(api, payload, existing, report)
            if score is None:
                continue
            # Full request identity is taken from the persisted score, not a
            # fixture ID or a guessed decision ID. Do not specify a fixed budget.
            _investigation_warmup(api, [(profile, score)], report)
            _learn(api, score, report)


def _seed_review_learning(api: str, existing: dict[str, dict[str, Any]], report: dict[str, Any]) -> None:
    """Bounded synthetic reviewer corrections; no invented measured accuracy."""
    status, body, error = _probe(api, "/api/demo/s2p/budget-candidates")
    if status != 200 or body is None:
        report["limitations"].append(f"S2P-09: boundary review unavailable ({status}: {error})")
        return
    hard = body["candidates"].get("review")
    if hard is None:
        report["limitations"].append("S2P-09: no borderline approval available for synthetic review")
        return
    # Explicit synthetic ground truth: a near-boundary invoice needs manual
    # review. Keep this label fixed across repetitions, independent of scores.
    review_action = "hold_for_review"
    for index in range(12):
        payload = {"event_id": f"S2P-DEMO-REVIEW-{uuid4().hex}", "category": hard["category"],
                   "amount": 1000, "supplier_id": "SUP-DEMO-REVIEW",
                   "supplier_risk_rating": 1.0 - hard["factors"]["supplier_exception_history"], **hard["factors"]}
        scored = _score(api, payload, existing, report)
        if scored is None:
            return
        override = (scored.get("canonical_action") or scored["action"]) != review_action
        status, result, error = _request_json(api, "POST", "/api/learn", {
            "decision_id": scored["decision_id"], "actual_action": review_action,
            "outcome": "override" if override else "confirmed",
            "reason_code": "wrong_action" if override else None,
            "context": {"planted": True, "evidence_tier": "T_S", "origin": "demo_boundary_review"},
        })
        if status != 200 or not isinstance(result, dict) or result.get("learning_applied") is not True:
            report["learning_errors"].append({"decision_id": scored["decision_id"], "status": status, "detail": error or result})
            return  # Never bypass a conservation block to manufacture a gain.
        report["learned"] += 1
        # Interleave known easy confirmations to avoid an artificial all-error
        # stream; both corrections and confirmations still traverse the gate.
        for confirmation in range(4):
            easy = body["candidates"]["easy"]
            good = _score(api, {"event_id": f"S2P-DEMO-CONFIRM-{uuid4().hex}",
                               "category": easy["category"], "amount": 1000,
                               "supplier_id": "SUP-DEMO-CONFIRM",
                               "supplier_risk_rating": 1.0 - easy["factors"]["supplier_exception_history"],
                               **easy["factors"]}, existing, report)
            if good is not None:
                _learn(api, good, report)
        _, twin, _ = _probe(api, "/api/s2p/learning/frozen-twin")
        if twin and isinstance(twin.get("delta_accuracy"), (int, float)) and twin["delta_accuracy"] > 0:
            break


def run(api: str, *, dry_run: bool = False) -> dict[str, Any]:
    fixture = _load_fixture()
    report: dict[str, Any] = {
        "api": api,
        "fixture": str(FIXTURE_PATH),
        "seed_version": "s2p-final-v4",
        "normal_phase_environment": {"S2P_DEMO_READ_MODE": "true"},
        "dry_run": dry_run,
        "scored": [],
        "learned": 0,
        "skipped_existing": [],
        "errors": [],
        "learning_errors": [],
        "investigations": [],
        "investigation_errors": [],
        "limitations": [],
    }
    special = fixture.get("special_decisions", [])
    warmup = fixture.get("warmup", {})
    post_freeze = fixture.get("post_freeze", {})
    if not isinstance(special, list) or not isinstance(warmup, dict) or not isinstance(post_freeze, dict):
        raise ValueError("fixture has invalid special_decisions, warmup, or post_freeze sections")

    if dry_run:
        report["planned_scores"] = len(special) + int(warmup.get("count", 0)) + int(post_freeze.get("count", 0))
        report["planned_investigations"] = 4
        report["additional_boundary_scores"] = 4
        report["maximum_post_freeze_review_scores"] = 60
        report["maximum_extinction_scores"] = 1
        return report

    # Check deployment/guard before making any scoring mutations.
    status, _, error = _probe(api, "/api/demo/s2p/budget-candidates")
    if status != 200:
        raise ValueError(f"Demo control unavailable ({status}: {error}); restart S2P with S2P_DEMO_MODE=true")
    existing = _existing_decisions(api)
    for payload in special:
        if isinstance(payload, dict):
            score = _score(api, payload, existing, report)
            if score is not None:
                _learn(api, score, report)

    categories = [str(value) for value in warmup.get("categories", [])]
    warm_count = int(warmup.get("count", 0))
    for index in range(warm_count):
        category = _verified_warmup_category(index, categories)
        score = _score(api, _warmup_payload(index, category, hard=index % 2 == 1), existing, report)
        if score is not None:
            _learn(api, score, report)

    _seed_extinction(api, existing, report)
    if report["errors"] or report["learning_errors"] or report["limitations"]:
        return report

    freeze_status, freeze_body, freeze_error = _request_json(api, "POST", "/api/s2p/learning/frozen-twin/freeze", {})
    report["twin_freeze"] = {"status": freeze_status, "body": freeze_body, "detail": freeze_error}
    if freeze_status not in {200, 201}:
        report["errors"].append(report["twin_freeze"])
        return report
    else:
        status, body, error = _request_json(api, "POST", "/api/demo/s2p/learning/re-freeze", {"confirm_demo_reseed": True})
        report["demo_refreeze"] = {"status": status, "body": body, "detail": error}
        if status != 200:
            report["errors"].append(report["demo_refreeze"])
            return report

    post_categories = [str(value) for value in post_freeze.get("categories", [])]
    for index in range(int(post_freeze.get("count", 0))):
        category = _warmup_category(warm_count + index, post_categories)
        score = _score(api, _warmup_payload(100 + index, category, hard=index % 2 == 1), existing, report)
        if score is not None:
            _learn(api, score, report)

    _seed_review_learning(api, existing, report)
    _seed_boundary_allocations(api, existing, report)
    status, body, error = _request_json(api, "POST", "/api/demo/s2p/learning/persist")
    report["demo_runtime_persistence"] = {"status": status, "body": body, "detail": error}
    if status != 200 or not isinstance(body, dict) or body.get("status") != "persisted":
        report["errors"].append(report["demo_runtime_persistence"])
        return report
    twin_status, twin, twin_error = _probe(api, "/api/s2p/learning/frozen-twin")
    report["twin_comparison"] = {"status": twin_status, "body": twin, "detail": twin_error}
    if not twin or not isinstance(twin.get("delta_accuracy"), (int, float)) or twin["delta_accuracy"] <= 0:
        report["limitations"].append("S2P-09: measured live/frozen accuracy gap is not positive; no gain fabricated")
    budget_status, budget, budget_error = _probe(api, "/api/self/investigation-budget")
    report["budget"] = {"status": budget_status, "body": budget, "detail": budget_error}
    if not isinstance(budget, dict) or not isinstance(budget.get("allocations"), list):
        report["limitations"].append("S2P-07: budget endpoint does not expose the allocations array required by the spec")
    elif not {"easy", "hard"} <= {
        row.get("profile") for row in budget["allocations"] if isinstance(row, dict)
    }:
        report["limitations"].append("S2P-07: actual policy has not produced both easy and hard allocations")

    compliance_status, compliance, compliance_error = _probe(api, "/api/s2p/evidence/compliance")
    report["compliance"] = {"status": compliance_status, "body": compliance, "detail": compliance_error}
    extinction_rows = (
        compliance.get("extinction_classes", compliance.get("extinct_classes", []))
        if isinstance(compliance, dict) else []
    )
    if not isinstance(extinction_rows, list) or not any(
        isinstance(row, dict) and row.get("category") == "format_compliance"
        and row.get("start_count", 0) > 0 and row.get("end_count") == 0
        and row.get("earning_decision_id")
        for row in extinction_rows
    ):
        report["limitations"].append(
            "S2P-05: no causal format_compliance extinction recorded; stopping scores alone "
            "does not resolve older pending decisions. Only recognized preseed rows are verified."
        )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API, help="S2P API base URL")
    parser.add_argument("--dry-run", action="store_true", help="validate fixture and print planned work")
    args = parser.parse_args()
    try:
        result = run(args.api, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "detail": str(exc)}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if args.dry_run or not any(
        result.get(key) for key in ("errors", "learning_errors", "investigation_errors", "limitations")
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
