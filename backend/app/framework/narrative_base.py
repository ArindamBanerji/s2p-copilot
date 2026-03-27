"""
NarrativeProvider ABC for CopilotFramework.
Domain implementations (e.g. TemplateNarrativeProvider for SOC)
live in their respective domain service layers.
Safe to copy to copilot-sdk.

Design
------
Provider classes are registered by the domain layer at import time via
register_narrative_provider().  create_narrative_provider() looks up the
registry so this module has zero coupling to concrete implementations.

Typical boot sequence:
  1. app.services.narrative is imported (e.g. by a router).
  2. That module imports this one AND calls register_narrative_provider()
     for "template" and "ollama".
  3. On first call to get_narrative_provider() the registry is already
     populated and the factory succeeds.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ============================================================================
# Protocol
# ============================================================================

@runtime_checkable
class NarrativeProvider(Protocol):
    def generate(
        self,
        alert: Dict[str, Any],
        decision: Dict[str, Any],
        factors: List[Dict[str, Any]],
        calibration_context: Dict[str, Any],
    ) -> str: ...


# ============================================================================
# Provider registry
# ============================================================================

_PROVIDER_REGISTRY: Dict[str, Any] = {}  # name → class


def register_narrative_provider(name: str, cls: Any) -> None:
    """
    Register a NarrativeProvider class under a name.

    Called by the domain service layer (e.g. services/narrative.py) at
    module import time so the factory can resolve provider names without
    importing domain code directly.
    """
    _PROVIDER_REGISTRY[name] = cls
    logger.debug("[NARRATIVE] Registered provider %r -> %s", name, cls.__name__)


# ============================================================================
# Factory
# ============================================================================

def create_narrative_provider(provider_type: str = "template") -> NarrativeProvider:
    """
    Create a NarrativeProvider instance by name.

    Args:
        provider_type: name registered via register_narrative_provider().
                       Reads NARRATIVE_PROVIDER env var if not specified.

    Returns:
        A NarrativeProvider instance.

    Raises:
        RuntimeError if the registry is empty (domain layer not yet imported).
    """
    pt = provider_type.lower().strip()
    cls = _PROVIDER_REGISTRY.get(pt)

    if cls is None:
        fallback = _PROVIDER_REGISTRY.get("template")
        if fallback is None:
            raise RuntimeError(
                f"No NarrativeProvider registered for {pt!r}. "
                "Ensure app.services.narrative is imported before calling "
                "create_narrative_provider()."
            )
        logger.warning(
            "[NARRATIVE] Unknown provider %r; falling back to template", provider_type
        )
        cls = fallback

    logger.info("[NARRATIVE] Creating %s", cls.__name__)
    return cls()


# ============================================================================
# Module-level singleton
# ============================================================================

_provider: Optional[NarrativeProvider] = None


def get_narrative_provider() -> NarrativeProvider:
    """
    Return the active NarrativeProvider singleton.

    If set_narrative_provider() was never called (e.g. in tests),
    creates a provider on first access using the registry.
    """
    global _provider
    if _provider is None:
        _provider = create_narrative_provider(
            os.getenv("NARRATIVE_PROVIDER", "template")
        )
    return _provider


def set_narrative_provider(provider: NarrativeProvider) -> None:
    """Set the module-level singleton. Called once at app startup."""
    global _provider
    _provider = provider
    logger.info("[NARRATIVE] Provider set: %s", type(provider).__name__)
