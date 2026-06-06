"""S2P active graph cutover config and read-only status reporting.

Phase A only validates cutover intent. It does not construct an AGE active
store or change the authoritative SQLite runtime path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import os
import re

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/s2p/graph", tags=["s2p-graph"])

_GENERIC_GRAPH_ENV_KEYS = (
    "GRAPH_BACKEND",
    "GRAPH_DSN",
    "GRAPH_NAME",
    "GRAPH_DOMAIN",
    "AGE_DSN",
    "AGE_GRAPH_NAME",
)

S2P_ALLOWED_PRODUCT_AGE_GRAPHS = frozenset(
    {
        "governed_copilot_graph",
    }
)
_HISTORICAL_VISIBILITY_WARNING = (
    "Historical SQLite records are not visible in AGE-active mode unless migrated."
)
_TRUE_PARALLEL_GATE_STATUS = "completed_backend_live"


class S2PActiveGraphConfigError(ValueError):
    """Raised when S2P active AGE cutover config is unsafe."""


def _parse_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise S2PActiveGraphConfigError(f"Invalid boolean value: {value!r}")


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


def _generic_graph_env_present(source: Mapping[str, str]) -> bool:
    return any(source.get(key) not in (None, "") for key in _GENERIC_GRAPH_ENV_KEYS)


def _active_warning(age_active: bool, graph_kind: str) -> str:
    if not age_active:
        return "Phase A status only: active AGE writes are not enabled."
    if graph_kind == "product":
        return (
            "Product Decision/Outcome active AGE writes are enabled; "
            "migration/backfill and EvidenceReceipt mapping remain excluded."
        )
    return "Phase B test mode: active AGE writes are enabled for protocol_v2_test* only."


@dataclass(frozen=True)
class S2PActiveGraphConfig:
    requested_backend: str = "sqlite"
    dsn: str | None = None
    graph: str | None = None
    domain: str = "s2p"
    test_mode: bool = False
    ignored_generic_graph_env: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "S2PActiveGraphConfig":
        source = os.environ if env is None else env
        backend = (source.get("S2P_ACTIVE_GRAPH_BACKEND") or "sqlite").strip().lower()
        if backend not in {"sqlite", "age"}:
            raise S2PActiveGraphConfigError(
                "S2P_ACTIVE_GRAPH_BACKEND must be 'sqlite' or 'age'"
            )

        domain = (source.get("S2P_ACTIVE_AGE_DOMAIN") or "s2p").strip()
        if not domain:
            raise S2PActiveGraphConfigError("S2P_ACTIVE_AGE_DOMAIN must not be blank")
        if domain != "s2p":
            raise S2PActiveGraphConfigError(
                "S2P_ACTIVE_AGE_DOMAIN must be 's2p' for S2P active graph"
            )

        config = cls(
            requested_backend=backend,
            dsn=source.get("S2P_ACTIVE_AGE_DSN"),
            graph=source.get("S2P_ACTIVE_AGE_GRAPH"),
            domain=domain,
            test_mode=_parse_bool(source.get("S2P_ACTIVE_AGE_TEST_MODE"), default=False),
            ignored_generic_graph_env=_generic_graph_env_present(source),
        )
        config.validate(source)
        return config

    def validate(self, source: Mapping[str, str] | None = None) -> None:
        if self.requested_backend == "sqlite":
            return

        source = source or {}
        if self.domain != "s2p":
            raise S2PActiveGraphConfigError(
                "S2P_ACTIVE_AGE_DOMAIN must be 's2p' for S2P active graph"
            )
        if _parse_bool(source.get("S2P_SHADOW_AGE"), default=False):
            raise S2PActiveGraphConfigError(
                "S2P_SHADOW_AGE=1 conflicts with active AGE cutover"
            )
        if not self.dsn or not self.dsn.strip():
            raise S2PActiveGraphConfigError(
                "S2P_ACTIVE_AGE_DSN is required when S2P_ACTIVE_GRAPH_BACKEND=age"
            )
        if not self.graph or not self.graph.strip():
            raise S2PActiveGraphConfigError(
                "S2P_ACTIVE_AGE_GRAPH is required when S2P_ACTIVE_GRAPH_BACKEND=age"
            )

        graph = self.graph.strip()
        if graph == "soc_graph":
            raise S2PActiveGraphConfigError("S2P active AGE must not target soc_graph")
        if self.test_mode:
            if not graph.startswith("protocol_v2_test"):
                raise S2PActiveGraphConfigError(
                    "S2P active AGE test mode is allowed only for protocol_v2_test* graphs"
                )
            return

        if graph.startswith("protocol_v2_test"):
            raise S2PActiveGraphConfigError(
                "protocol_v2_test* graphs require S2P_ACTIVE_AGE_TEST_MODE=1"
            )
        if graph not in S2P_ALLOWED_PRODUCT_AGE_GRAPHS:
            raise S2PActiveGraphConfigError(
                "S2P active AGE product graph must be reviewed and allow-listed"
            )

    def graph_kind(self) -> str:
        if self.requested_backend != "age" or not self.graph:
            return "none"
        return "test" if self.graph.strip().startswith("protocol_v2_test") else "product"

    def safe_summary(self) -> dict[str, Any]:
        return {
            "requested_backend": self.requested_backend,
            "dsn": _redact_secret(self.dsn),
            "graph": self.graph,
            "domain": self.domain,
            "test_mode": self.test_mode,
            "ignored_generic_graph_env": self.ignored_generic_graph_env,
        }


def initialize_s2p_active_graph_config(
    env: Mapping[str, str] | None = None,
) -> S2PActiveGraphConfig:
    return S2PActiveGraphConfig.from_env(env)


class S2PActiveAGEGraphStore:
    """S2P active AGE test-mode adapter preserving Protocol v2 score writes."""

    domain = "s2p"

    def __init__(self, store: Any, *, active_phase: str = "phase_b_test_mode") -> None:
        self._store = store
        self.active_phase = active_phase

    def write_decision(
        self,
        domain: str,
        category: str,
        action: str,
        confidence: float,
        factors: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        from app.domains.s2p.config import S2PDomainConfig

        if domain != "s2p":
            raise ValueError("S2P active AGE store only accepts domain 's2p'")
        decision_metadata = dict(metadata or {})
        decision_id = str(decision_metadata.get("decision_id") or "").strip()
        if not decision_id:
            raise ValueError("S2P active AGE write_decision requires metadata.decision_id")
        factor_names = list(S2PDomainConfig.factors)
        if isinstance(decision_metadata.get("factor_vector"), list):
            factor_vector = [float(value) for value in decision_metadata["factor_vector"]]
        else:
            factor_vector = [float(factors.get(name, 0.5)) for name in factor_names]
        recommended_index = int(
            decision_metadata.get(
                "recommended_index",
                S2PDomainConfig.get_action_index(action),
            )
        )
        category_index = int(
            decision_metadata.get(
                "category_index",
                S2PDomainConfig.get_category_index(category),
            )
        )
        probabilities = decision_metadata.get("probabilities")
        if not isinstance(probabilities, list):
            probabilities = [
                1.0 if index == recommended_index else 0.0
                for index in range(S2PDomainConfig.n_actions)
            ]
        self._store.write_governed_decision(
            decision_id=decision_id,
            domain=domain,
            category=category,
            category_index=category_index,
            recommended_action=action,
            recommended_index=recommended_index,
            confidence=confidence,
            probabilities=[float(value) for value in probabilities],
            factor_vector=factor_vector,
            factor_names=factor_names,
            source="s2p_active_age_score",
            scorer_version=f"s2p_active_age_{self.active_phase}",
            preset_version="s2p",
            factor_schema_version="s2p_factor_schema_v1",
            metadata={
                **decision_metadata,
                "active_age": True,
                "active_age_phase": self.active_phase,
            },
        )
        return decision_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


def create_s2p_active_graph_store(
    config: S2PActiveGraphConfig,
    *,
    store_factory: Any | None = None,
) -> Any | None:
    if config.requested_backend != "age":
        return None
    if _parse_bool(os.environ.get("S2P_SHADOW_AGE"), default=False):
        raise S2PActiveGraphConfigError(
            "S2P_SHADOW_AGE=1 conflicts with active AGE cutover"
        )
    config.validate({"S2P_ACTIVE_GRAPH_BACKEND": "age"})
    graph_kind = config.graph_kind()
    factory = store_factory
    if factory is None:
        from copilot_sdk.graph.factory import create_graph_store

        factory = create_graph_store
    store = factory(
        backend="age",
        domain=config.domain,
        dsn=config.dsn,
        graph_name=config.graph,
        env={},
        test_mode=config.test_mode,
    )
    active_phase = "product_decision_outcome_cutover" if graph_kind == "product" else "phase_b_test_mode"
    return S2PActiveAGEGraphStore(store, active_phase=active_phase)


def _shadow_summary(shadow: Any) -> dict[str, Any]:
    if shadow is None:
        return {
            "enabled": False,
            "run_id": None,
            "status_counts": {},
            "last_error": None,
        }
    config = getattr(shadow, "config", None)
    diagnostics = getattr(shadow, "diagnostics", None)
    return {
        "enabled": bool(getattr(config, "enabled", False)),
        "run_id": getattr(diagnostics, "shadow_run_id", None),
        "status_counts": diagnostics.status_counts() if diagnostics else {},
        "last_error": diagnostics.last_error() if diagnostics else None,
    }


def build_s2p_graph_status(app_state: Any) -> dict[str, Any]:
    config = getattr(app_state, "s2p_active_graph_config", None)
    if not isinstance(config, S2PActiveGraphConfig):
        config = S2PActiveGraphConfig.from_env({})
    shadow = getattr(app_state, "s2p_shadow", None)
    shadow_summary = _shadow_summary(shadow)
    requested_age = config.requested_backend == "age"
    active_store = getattr(app_state, "graph_store", None)
    age_active = bool(
        requested_age
        and isinstance(active_store, S2PActiveAGEGraphStore)
    )
    graph_kind = config.graph_kind()
    product_graph_allowed = (
        None
        if not requested_age or graph_kind != "product"
        else str(config.graph).strip() in S2P_ALLOWED_PRODUCT_AGE_GRAPHS
    )
    product_graph_allow_listed = bool(product_graph_allowed)
    product_age_active = bool(age_active and graph_kind == "product")
    decision_outcome_cutover_ready = bool(
        product_age_active
        and product_graph_allow_listed
    )
    full_audit_memory_ready = False

    return {
        "active_backend": "age" if age_active else "sqlite",
        "requested_backend": config.requested_backend,
        "sqlite_authoritative": not age_active,
        "age_active": age_active,
        "shadow_enabled": shadow_summary["enabled"],
        "shadow_allowed": not requested_age,
        "active_graph_name": config.graph if requested_age else None,
        "age_graph_kind": graph_kind,
        "graph_kind": "sqlite" if not requested_age else graph_kind,
        "product_graph_allow_list": sorted(S2P_ALLOWED_PRODUCT_AGE_GRAPHS),
        "product_graph_allowed": product_graph_allowed,
        "product_cutover_implementation_ready": decision_outcome_cutover_ready,
        "decision_outcome_cutover_ready": decision_outcome_cutover_ready,
        "full_audit_memory_ready": full_audit_memory_ready,
        "migration_complete": False,
        "evidence_receipt_ready": False,
        "true_parallel_gate_status": _TRUE_PARALLEL_GATE_STATUS,
        "evidence_receipt_mapping_status": "design_required",
        "active_domain": config.domain,
        "active_test_mode": config.test_mode,
        "ignored_generic_graph_env": config.ignored_generic_graph_env,
        "migration_backfill_status": "not_in_scope",
        "receipt_mapping_status": "excluded_first_cutover",
        "historical_visibility": "new_writes_only_history_not_migrated",
        "historical_visibility_warning": _HISTORICAL_VISIBILITY_WARNING,
        "historical_sqlite_count_warning": _HISTORICAL_VISIBILITY_WARNING,
        "rollback_instructions": [
            "Unset S2P_ACTIVE_GRAPH_BACKEND or set it to sqlite.",
            "Restart S2P.",
            "Rollback routes new writes to SQLite; it does not reconcile AGE data.",
        ],
        "diagnostics_summary": {
            "shadow_status_counts": shadow_summary["status_counts"],
            "recent_events_exposed": False,
        },
        "last_error": shadow_summary["last_error"],
        "cutover_ready": decision_outcome_cutover_ready,
        "cutover_ready_flags": {
            "phase_a_status_only": not age_active,
            "phase_b_test_mode_active": bool(age_active and graph_kind == "test"),
            "product_decision_outcome_active": product_age_active,
            "backend_guard_valid": True,
            "graph_guard_valid": True,
            "product_graph_allow_listed": product_graph_allow_listed,
            "product_graph_reviewed": product_graph_allow_listed,
            "true_parallel_gate_complete": True,
            "true_parallel_active_age_gate_passed": True,
            "rollback_proof_complete": True,
            "rollback_proof_passed": True,
            "evidence_receipt_mapping_complete": False,
            "evidence_receipts_active": False,
            "evidence_receipt_ready": False,
            "full_audit_memory_ready": full_audit_memory_ready,
            "preview_read_only_guard": True,
            "receipt_scope_decision": "excluded_first_cutover",
            "rollback_plan_present": True,
            "active_age_writes_enabled": age_active,
            "decision_outcome_cutover_ready": decision_outcome_cutover_ready,
            "migration_complete": False,
            "migration_backfill_in_scope": False,
            "product_claim_allowed": False,
        },
        "warnings": [
            _active_warning(age_active, graph_kind),
            "Historical SQLite migration/backfill is not in scope.",
            _HISTORICAL_VISIBILITY_WARNING,
            "EvidenceReceipt mapping is excluded from first cutover.",
        ],
    }


@router.get("/status")
def graph_status(request: Request) -> dict[str, Any]:
    return build_s2p_graph_status(request.app.state)


__all__ = [
    "S2PActiveGraphConfig",
    "S2PActiveGraphConfigError",
    "S2PActiveAGEGraphStore",
    "build_s2p_graph_status",
    "create_s2p_active_graph_store",
    "initialize_s2p_active_graph_config",
    "router",
    "S2P_ALLOWED_PRODUCT_AGE_GRAPHS",
]
