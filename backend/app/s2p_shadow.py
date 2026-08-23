"""S2P shadow configuration, namespace isolation, and diagnostics.

Shadow state is non-authoritative.  When a distinct shadow graph is configured,
startup creates a distinct store for it; otherwise an explicitly injected store
remains supported for isolated tests.
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
        test_mode = _parse_bool(
            source.get("S2P_SHADOW_AGE_TEST_MODE") or source.get("S2P_AGE_TEST_MODE"),
            default=False,
        )
        domain = (
            source.get("S2P_SHADOW_AGE_DOMAIN")
            or source.get("S2P_AGE_DOMAIN")
            or "s2p"
        ).strip()
        shadow_dsn = source.get("S2P_SHADOW_AGE_DSN")
        shadow_graph = source.get("S2P_SHADOW_AGE_GRAPH")
        legacy_dsn = source.get("S2P_AGE_DSN")
        legacy_graph = source.get("S2P_AGE_GRAPH")
        configured_dsn = shadow_dsn or legacy_dsn
        configured_graph = shadow_graph or legacy_graph
        if env is None and enabled and not (configured_dsn or configured_graph):
            try:
                graph_config = GraphConfig.load("s2p")
            except GraphConfigError as exc:
                raise S2PShadowConfigError(str(exc)) from exc
            domain = graph_config.domain
            dsn = graph_config.dsn
            graph = graph_config.graph
        else:
            # Legacy values remain visible in diagnostics for compatibility,
            # but are never used to construct a graph store.
            dsn = configured_dsn
            graph = configured_graph

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

        # The active GraphStore, resolved by S2P startup, is the only graph
        # authority. Legacy DSN/graph fields are informational only.

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


def create_s2p_shadow_store(
    *,
    active_graph: str | None,
    active_domain: str = "s2p",
    profile: str = "production",
) -> Any | None:
    """Create an isolated shadow store when its configured graph differs.

    The active store remains the compatibility fallback for legacy deployments
    that do not configure a separate namespace.  A configured distinct graph
    is never allowed to share the active store.
    """
    config = S2PShadowConfig.from_env()
    if not config.enabled or not config.graph or config.graph == active_graph:
        return None
    if config.domain != active_domain or config.domain != "s2p":
        raise S2PShadowConfigError("S2P shadow graph must use domain='s2p'")
    if not config.dsn:
        raise S2PShadowConfigError(
            "S2P shadow graph requires S2P_SHADOW_AGE_DSN when using a separate namespace"
        )

    from copilot_sdk.graph.factory import create_graph_store

    return create_graph_store(
        backend="age",
        domain=config.domain,
        dsn=config.dsn,
        graph_name=config.graph,
        env={},
        test_mode=config.test_mode,
        shared_graph_authorization=f"{config.domain}:{config.graph}",
        profile=profile,
    )


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


def initialize_s2p_shadow_state(
    *,
    env: Mapping[str, str] | None = None,
    store_factory: Any | None = None,
    diagnostics: S2PShadowDiagnostics | None = None,
    store: Any | None = None,
) -> S2PShadowState:
    config = S2PShadowConfig.from_env(env)
    coordinator = diagnostics or S2PShadowDiagnostics()
    # ``store_factory`` is retained as a compatibility parameter for isolated
    # callers.  Production startup supplies the namespace-specific store.
    del store_factory
    return S2PShadowState(
        config=config,
        diagnostics=coordinator,
        store=store if config.enabled else None,
    )


__all__ = [
    "S2PShadowConfig",
    "S2PShadowConfigError",
    "S2PShadowDiagnostics",
    "S2PShadowEvent",
    "S2PShadowState",
    "SHADOW_STATUSES",
    "create_s2p_shadow_store",
    "initialize_s2p_shadow_state",
]
