"""Read-only S2P novelty tracking utilities."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Any


def euclidean_distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return float("inf")
    return math.sqrt(sum((float(left) - float(right)) ** 2 for left, right in zip(a, b)))


def _centroids_from_scorer(scorer: Any) -> Any:
    gae_scorer = getattr(scorer, "gae_scorer", None)
    if gae_scorer is not None and hasattr(gae_scorer, "centroids"):
        return getattr(gae_scorer, "centroids")
    if hasattr(scorer, "centroids"):
        return getattr(scorer, "centroids")
    return None


def compute_nearest_distance(
    factor_vector: list[float],
    category: str,
    scorer: Any,
    config: Any,
) -> float:
    centroids = _centroids_from_scorer(scorer)
    if centroids is None:
        return 0.0

    if hasattr(config, "get_category_index"):
        category_index = int(config.get_category_index(category))
    else:
        category_index = list(config.categories).index(category)

    action_count = int(getattr(config, "n_actions", len(getattr(config, "actions", []))))
    vector = [float(value) for value in factor_vector]
    distances: list[float] = []

    for action_index in range(action_count):
        centroid = centroids[category_index][action_index]
        if hasattr(centroid, "tolist"):
            centroid_values = centroid.tolist()
        else:
            centroid_values = list(centroid)
        distances.append(euclidean_distance(vector, [float(value) for value in centroid_values]))

    finite = [distance for distance in distances if math.isfinite(distance)]
    return min(finite) if finite else 0.0


@dataclass(frozen=True)
class NoveltyEntry:
    sequence: int
    category: str
    nearest_distance: float
    is_novel: bool
    vector_norm: float
    factor_vector: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "category": self.category,
            "nearest_distance": self.nearest_distance,
            "is_novel": self.is_novel,
            "vector_norm": self.vector_norm,
            "factor_vector": list(self.factor_vector),
        }


class NoveltyTracker:
    def __init__(self, window_size: int = 50, distance_threshold: float = 0.6):
        self.window_size = int(window_size)
        self.distance_threshold = float(distance_threshold)
        self._history: deque[NoveltyEntry] = deque(maxlen=self.window_size)
        self._sequence = 0

    def record(
        self,
        factor_vector: list[float],
        category: str,
        nearest_distance: float,
    ) -> dict[str, Any]:
        vector = [float(value) for value in factor_vector]
        self._sequence += 1
        entry = NoveltyEntry(
            sequence=self._sequence,
            category=str(category),
            nearest_distance=float(nearest_distance),
            is_novel=float(nearest_distance) > self.distance_threshold,
            vector_norm=math.sqrt(sum(value * value for value in vector)),
            factor_vector=vector,
        )
        self._history.append(entry)
        return entry.to_dict()

    @property
    def novelty_rate(self) -> float:
        if not self._history:
            return 0.0
        novelty_count = sum(1 for entry in self._history if entry.is_novel)
        return novelty_count / len(self._history)

    @property
    def alert_active(self) -> bool:
        return self.novelty_rate > 0.20

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        limit_value = max(int(limit), 0)
        if limit_value == 0:
            return []
        return [entry.to_dict() for entry in list(self._history)[-limit_value:]]

    def get_status(self) -> dict[str, Any]:
        novelty_count = sum(1 for entry in self._history if entry.is_novel)
        return {
            "window_size": self.window_size,
            "distance_threshold": self.distance_threshold,
            "total_in_window": len(self._history),
            "novelty_count": novelty_count,
            "novelty_rate": self.novelty_rate,
            "alert_active": self.alert_active,
            "per_category": self._per_category_breakdown(),
        }

    def _per_category_breakdown(self) -> dict[str, dict[str, Any]]:
        categories = sorted({entry.category for entry in self._history})
        breakdown: dict[str, dict[str, Any]] = {}
        for category in categories:
            entries = [entry for entry in self._history if entry.category == category]
            novel = sum(1 for entry in entries if entry.is_novel)
            breakdown[category] = {
                "total": len(entries),
                "novel": novel,
                "novelty_rate": novel / len(entries) if entries else 0.0,
            }
        return breakdown


_novelty_tracker = NoveltyTracker()


def get_novelty_tracker() -> NoveltyTracker:
    return _novelty_tracker


def reset_novelty_tracker() -> NoveltyTracker:
    global _novelty_tracker
    _novelty_tracker = NoveltyTracker()
    return _novelty_tracker
