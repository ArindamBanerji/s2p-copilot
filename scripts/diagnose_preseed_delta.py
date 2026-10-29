"""Read-only S2P-09 diagnostics: python scripts/diagnose_preseed_delta.py.

Only stdlib and GET requests; never enables demo mode, scores, or learns.
The default decision/history endpoints are bounded; incomplete data is labelled.
"""
from __future__ import annotations

import http.client
import json
import sys
from typing import Any


def get(path: str) -> dict[str, Any] | None:
    connection = http.client.HTTPConnection("127.0.0.1", 8002, timeout=30)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = json.loads(response.read())
        print(f"GET {path}: HTTP {response.status}")
        if response.status != 200 or not isinstance(body, dict):
            print(json.dumps(body, ensure_ascii=True))
            return None
        return body
    except (OSError, ValueError, http.client.HTTPException) as exc:
        print(f"UNAVAILABLE {path}: {type(exc).__name__}: {exc}")
        return None
    finally:
        connection.close()


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def origin(row: dict[str, Any]) -> Any:
    return mapping(mapping(row.get("outcome_metadata")).get("context")).get("origin")


def report_comparison(items: list[dict[str, Any]], label: str) -> None:
    eligible = [item for item in items if item.get("actual_action") is not None]
    live = sum(mapping(item.get("live")).get("action") == item["actual_action"] for item in eligible)
    frozen = sum(mapping(item.get("frozen")).get("action") == item["actual_action"] for item in eligible)
    delta = (live - frozen) / len(eligible) if eligible else None
    print(f"{label}: population={len(eligible)} live_correct={live} frozen_correct={frozen} delta={delta}")


def checkpoint_trace(checkpoints: list[dict[str, Any]], review: dict[str, Any]) -> bool:
    """Show weighted centroid margins for the latest review, not fabricated scores."""
    vector = review.get("factor_vector")
    category_index = review.get("category_index")
    if not isinstance(vector, list) or not isinstance(category_index, int):
        print("Checkpoint margin trace unavailable: review vector/category index absent")
        return False
    print("Latest review checkpoint trace: weighted squared distance(hold)-distance(auto)")
    print("Negative favors hold over auto; positive favors auto. These are centroid margins, not API replay scores.")
    weights: list[Any] | None = None
    saw_hold = False
    reverted = False
    start = float(review.get("created_at") or 0)
    actions = ["auto_approve", "hold_for_review", "escalate_to_buyer", "flag_leakage", "refer_to_specialist"]
    for checkpoint in sorted(checkpoints, key=lambda item: float(item.get("created_at") or 0)):
        metadata = mapping(checkpoint.get("metadata"))
        dk = metadata.get("dk_weights")
        if isinstance(dk, list) and len(dk) > category_index and isinstance(dk[category_index], list):
            weights = dk[category_index]
        if float(checkpoint.get("created_at") or 0) < start:
            continue
        tensor = checkpoint.get("centroids")
        if weights is None or not isinstance(tensor, list) or len(tensor) <= category_index:
            continue
        centers = tensor[category_index]
        if not isinstance(centers, list) or len(centers) != len(actions):
            continue
        if len(weights) != len(vector) or any(not isinstance(c, list) or len(c) != len(vector) for c in centers):
            continue
        distances = [sum(float(w) * (float(a) - float(b)) ** 2
                         for w, a, b in zip(weights, vector, center)) for center in centers]
        best = actions[min(range(len(distances)), key=distances.__getitem__)]
        if saw_hold and best == "auto_approve":
            reverted = True
        saw_hold = saw_hold or best == "hold_for_review"
        print(f"  {checkpoint.get('decision_id')} source={metadata.get('source', 'checkpoint')} "
              f"nearest={best} margin={distances[1] - distances[0]:.12g}")
    return reverted


def main() -> int:
    print("S2P preseed delta diagnostic (read-only, localhost:8002)")
    candidates = get("/api/demo/s2p/budget-candidates")
    for profile in ("review", "easy", "hard"):
        candidate = mapping(mapping(candidates).get("candidates")).get(profile)
        print(f"{profile} candidate: {json.dumps(candidate, sort_keys=True)}")
    if candidates is None:
        print("Candidate values unavailable; demo guard may be off. No mode change attempted.")

    twin = get("/api/s2p/learning/frozen-twin")
    if twin:
        print("Twin status:", json.dumps({key: twin.get(key) for key in (
            "frozen_available", "data_available", "current_decisions", "compared_decisions",
            "current_accuracy", "frozen_accuracy", "delta_accuracy", "provenance", "source")}, sort_keys=True))
    comparisons = rows(mapping(twin).get("visual_diff"))
    for item in comparisons:
        live = mapping(item.get("live")).get("action")
        frozen = mapping(item.get("frozen")).get("action")
        actual = item.get("actual_action")
        print(f"{item.get('decision_id')}: live={live} frozen={frozen} actual={actual} "
              f"different={live != frozen} live_correct={live == actual} frozen_correct={frozen == actual}")
    report_comparison(comparisons, "Full comparison")

    history = get("/api/self/centroid-history?limit=500")
    checkpoints = rows(mapping(history).get("checkpoints"))
    counts: dict[str, int] = {}
    for checkpoint in checkpoints:
        category = str(checkpoint.get("category", "unknown"))
        counts[category] = counts.get(category, 0) + 1
    print("Returned checkpoint counts per category (not training counts):", json.dumps(counts, sort_keys=True))
    if len(checkpoints) >= 500:
        print("WARNING: history reached 500-row cap; earlier checkpoints may be omitted")
    if checkpoints:
        latest = max(checkpoints, key=lambda item: float(item.get("created_at") or 0))
        tensor = latest.get("centroids")
        print("Latest checkpoint:", latest.get("checkpoint_id"), "created_at:", latest.get("created_at"))
        print("Centroid cells per category index:", [len(c) for c in tensor] if isinstance(tensor, list) else None)
        replay = mapping(mapping(latest.get("metadata")).get("replay_state"))
        print("Checkpoint category training states:", json.dumps(replay.get("category_states")))
        print("Checkpoint category/action counts:", json.dumps(replay.get("counts")))
        print("Checkpoint decision_count:", mapping(latest.get("metadata")).get("decision_count"))
    print("Live total decisions (twin):", mapping(twin).get("current_decisions"))

    inventory = get("/api/self/decisions?limit=500")
    decisions = rows(mapping(inventory).get("decisions"))
    complete = inventory is not None and inventory.get("total") == len(decisions)
    print(f"Inventory complete={complete}, returned={len(decisions)}, total={mapping(inventory).get('total')}")
    review = [row for row in decisions if origin(row) == "demo_boundary_review"]
    ids = {row.get("decision_id") for row in review}
    review_comparisons = [item for item in comparisons if item.get("decision_id") in ids]
    print("Review population (origin=demo_boundary_review, all runs):", len(review))
    report_comparison(review_comparisons, "Review comparison")
    for row in review:
        print("Review source:", json.dumps({key: row.get(key) for key in (
            "decision_id", "created_at", "factor_vector", "probabilities", "confidence", "actual_action")}, sort_keys=True))
    reverted = False
    if review:
        latest_review = max(review, key=lambda item: float(item.get("created_at") or 0))
        latest_items = [item for item in comparisons if item.get("decision_id") == latest_review.get("decision_id")]
        report_comparison(latest_items, "Latest review (not necessarily the complete latest run)")
        reverted = checkpoint_trace(checkpoints, latest_review)

    print("SUMMARY")
    if twin is None or twin.get("data_available") is not True or not comparisons:
        print("Insufficient live comparison data; do not infer zero gain from unavailable endpoints.")
        return 1
    flips = sum(mapping(item.get("live")).get("action") != mapping(item.get("frozen")).get("action") for item in comparisons)
    print(f"Measured delta_accuracy={twin.get('delta_accuracy')}; action differences={flips}/{len(comparisons)}.")
    gains = sum(mapping(item.get("live")).get("action") == item.get("actual_action")
                and mapping(item.get("frozen")).get("action") != item.get("actual_action") for item in comparisons)
    losses = sum(mapping(item.get("live")).get("action") != item.get("actual_action")
                 and mapping(item.get("frozen")).get("action") == item.get("actual_action") for item in comparisons)
    print(f"Correctness gains={gains}, losses={losses}; equal totals cancel in full-set accuracy.")
    print("Accuracy only changes when action correctness changes; confidence movement alone earns no gain.")
    print("Preseed checks for an early gain inside review learning, then runs four additional budget confirmations.")
    if reverted:
        print("OBSERVED: latest review crossed to hold, then reverted to auto in later checkpoints.")
        print("Root cause: subsequent learning undid the boundary correction after the early success check.")
    else:
        print("No hold-to-auto reversal established by available checkpoints; do not assume high D is the cause.")
    print("Total D is not a learning weight: updates use category/action counts and decayed gradient rates.")
    if not complete or len(review_comparisons) != len(review):
        print("WARNING: review population incomplete; missing rows cannot be treated as zero correctness.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
