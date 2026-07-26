"""S2P AGE shadow configuration and diagnostics.

Shadow state is non-authoritative. SQLite remains the S2P runtime source of
truth until explicit parity and cutover gates pass.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from time import time
from typing import Any, Mapping
from uuid import uuid4
import os
import re

from copilot_sdk.config import GraphConfig, GraphConfigError

SHADOW_STATUSES = frozenset(
    {
        "disabled",
        "skipped",
        "succeeded",
        "failed",
        "parity_mismatch",
    }
)


class S2PShadowConfigError(ValueError):
    """Raised when S2P AGE shadow configuration is unsafe or incomplete."""


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise S2PShadowConfigError(f"Invalid boolean value: {value!r}")


def _redact_secret(text: str | None) -> str | None:
    if text is None:
        return None
    redacted = str(text)
    redacted = re.sub(r"://([^:/?#]+):([^@/?#]+)@", r"://\1:***@", redacted)
    redacted = re.sub(
        r"(?i)(password|passwd|pwd|token|secret)=([^&\s]+)",
        r"\1=***",
        redacted,
    )
    return redacted


@dataclass(frozen=True)
class S2PShadowConfig:
    enabled: bool = False
    strict: bool = False
    dsn: str | None = None
    graph: str | None = None
    domain: str = "s2p"
    test_mode: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "S2PShadowConfig":
        source = os.environ if env is None else env
        enabled = _parse_bool(source.get("S2P_SHADOW_AGE"), default=False)
        strict = _parse_bool(source.get("S2P_SHADOW_STRICT"), default=False)
        test_mode = _parse_bool(source.get("S2P_AGE_TEST_MODE"), default=False)
        domain = (source.get("S2P_AGE_DOMAIN") or "s2p").strip()
        legacy_dsn = source.get("S2P_AGE_DSN")
        legacy_graph = source.get("S2P_AGE_GRAPH")
        if env is None and (legacy_dsn or legacy_graph) and not os.environ.get("PYTEST_CURRENT_TEST"):
            raise GraphConfigError(
                "S2P_AGE_DSN/S2P_AGE_GRAPH are test-only overrides. "
                "Production shadow must use GraphConfig. Remove these env vars "
                "or set PYTEST_CURRENT_TEST."
            )
        if env is None and enabled and not (legacy_dsn or legacy_graph):
            try:
                graph_config = GraphConfig.load("s2p")
            except GraphConfigError as exc:
                raise S2PShadowConfigError(str(exc)) from exc
            domain = graph_config.domain
            dsn = graph_config.dsn
            graph = graph_config.graph
        else:
            # Explicit mapping injection is retained for isolated tests only;
            # production resolution always uses GraphConfig above.  The
            # legacy names are accepted only for test-mode compatibility.
            dsn = legacy_dsn
            graph = legacy_graph

        if not domain:
            raise S2PShadowConfigError("S2P_AGE_DOMAIN must not be blank")
        if domain != "s2p":
            raise S2PShadowConfigError("S2P_AGE_DOMAIN must be 's2p' for S2P shadow")

        config = cls(
            enabled=enabled,
            strict=strict,
            dsn=dsn,
            graph=graph,
            domain=domain,
            test_mode=test_mode,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.enabled:
            return

        if not self.dsn or not self.dsn.strip():
            raise S2PShadowConfigError("S2P_AGE_DSN is required when S2P_SHADOW_AGE=1")
        if not self.graph or not self.graph.strip():
            raise S2PShadowConfigError("S2P_AGE_GRAPH is required when S2P_SHADOW_AGE=1")

        graph = self.graph.strip()
        if graph == "soc_graph":
            raise S2PShadowConfigError("S2P AGE shadow must not target soc_graph")
        if graph.startswith("protocol_v2_test") and not self.test_mode:
            raise S2PShadowConfigError(
                "protocol_v2_test* graphs require S2P_AGE_TEST_MODE=1"
            )

    def safe_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "strict": self.strict,
            "dsn": _redact_secret(self.dsn),
            "graph": self.graph,
            "domain": self.domain,
            "test_mode": self.test_mode,
        }


@dataclass
class S2PShadowState:
    config: S2PShadowConfig
    diagnostics: "S2PShadowDiagnostics"
    store: Any | None = None


@dataclass(frozen=True)
class S2PShadowEvent:
    shadow_run_id: str
    operation_id: str
    operation: str
    status: str
    timestamp: float
    error_class: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None
    counts: dict[str, Any] = field(default_factory=dict)
    parity: dict[str, Any] = field(default_factory=dict)


class S2PShadowDiagnostics:
    def __init__(self, *, max_events: int = 100, shadow_run_id: str | None = None) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.shadow_run_id = shadow_run_id or str(uuid4())
        self._events: deque[S2PShadowEvent] = deque(maxlen=max_events)
        self._status_counts: Counter[str] = Counter()
        self._last_error: dict[str, str] | None = None

    def record(
        self,
        *,
        operation: str,
        status: str,
        operation_id: str | None = None,
        error: BaseException | str | None = None,
        latency_ms: float | None = None,
        counts: Mapping[str, Any] | None = None,
        parity: Mapping[str, Any] | None = None,
    ) -> S2PShadowEvent:
        if status not in SHADOW_STATUSES:
            raise ValueError(f"Unsupported S2P shadow status: {status}")
        if not operation:
            raise ValueError("operation must not be blank")

        error_class = None
        error_message = None
        if error is not None:
            error_class = error.__class__.__name__ if isinstance(error, BaseException) else "Error"
            error_message = _redact_secret(str(error))

        event = S2PShadowEvent(
            shadow_run_id=self.shadow_run_id,
            operation_id=operation_id or str(uuid4()),
            operation=operation,
            status=status,
            timestamp=time(),
            error_class=error_class,
            error_message=error_message,
            latency_ms=latency_ms,
            counts=dict(counts or {}),
            parity=dict(parity or {}),
        )
        self._events.append(event)
        self._status_counts[status] += 1
        if error_message is not None:
            self._last_error = {"class": error_class or "Error", "message": error_message}
        return event

    def events(self) -> list[S2PShadowEvent]:
        return list(self._events)

    def status_counts(self) -> dict[str, int]:
        return dict(self._status_counts)

    def last_error(self) -> dict[str, str] | None:
        return dict(self._last_error) if self._last_error else None


def _default_shadow_store_factory(config: S2PShadowConfig) -> Any:
    # Import lazily so disabled/default S2P startup does not construct or depend
    # on AGE/factory objects.
    from copilot_sdk.graph.factory import create_graph_store

    return create_graph_store(
        backend="age",
        domain=config.domain,
        dsn=config.dsn,
        graph_name=config.graph,
        env={},
        test_mode=config.test_mode,
    )


def initialize_s2p_shadow_state(
    *,
    env: Mapping[str, str] | None = None,
    store_factory: Any | None = None,
    diagnostics: S2PShadowDiagnostics | None = None,
) -> S2PShadowState:
    config = S2PShadowConfig.from_env(env)
    coordinator = diagnostics or S2PShadowDiagnostics()
    store = None
    if config.enabled:
        factory = store_factory or _default_shadow_store_factory
        store = factory(config)
    return S2PShadowState(config=config, diagnostics=coordinator, store=store)


__all__ = [
    "S2PShadowConfig",
    "S2PShadowConfigError",
    "S2PShadowDiagnostics",
    "S2PShadowEvent",
    "S2PShadowState",
    "SHADOW_STATUSES",
    "initialize_s2p_shadow_state",
]
