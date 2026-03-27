"""
tests/test_framework_discipline.py — CopilotFramework extraction discipline.

Enforces the boundary rules required for clean future extraction to copilot-sdk.
These tests must pass before any new file is added to app/framework/.

Run from backend/:
    pytest tests/test_framework_discipline.py -v
"""

import ast
import importlib
import pathlib
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============================================================================
# Test 1 — Framework files must have zero domain or router imports
# ============================================================================

def test_framework_has_no_domain_imports():
    """
    Framework files must never import from app.domains or app.routers.
    This test enforces the copilot-sdk extraction discipline.
    Fails immediately if the boundary is violated.
    """
    framework_dir = pathlib.Path("app/framework")
    forbidden_prefixes = ("app.domains", "app.routers")

    violations = []
    for py_file in sorted(framework_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                for fp in forbidden_prefixes:
                    if module.startswith(fp):
                        violations.append(f"{py_file.name}: imports {module}")

    assert violations == [], f"Framework discipline violations: {violations}"


# ============================================================================
# Test 2 — All framework modules importable without error
# ============================================================================

def test_framework_modules_importable():
    """All framework modules import without error."""
    modules = [
        "app.framework.ols_status",
        "app.framework.event_bus",
        "app.framework.decision_history",
        "app.framework.checkpoint",
        "app.framework.economics",
        "app.framework.shadow_mode",
        "app.framework.composite_gate",
        "app.framework.agent",
        "app.framework.intervention_controls",
        "app.framework.convergence_math",
        "app.framework.feedback_store",
        "app.framework.audit",
        "app.framework.provenance",
        "app.framework.narrative_base",
        "app.framework.similar_cases_base",
        "app.framework.iks_base",
        "app.framework.learning_state",
        "app.framework.feedback_base",
    ]
    for m in modules:
        mod = importlib.import_module(m)
        assert mod is not None, f"Module {m} returned None"


# ============================================================================
# Test 3 — Re-export stubs are transparent (same object as framework)
# ============================================================================

def test_reexport_stubs_transparent():
    """
    Importing from app.services still works after stub replacement.
    Same object as importing from app.framework directly.
    """
    from app.framework.ols_status import get_ols_status as fw
    from app.services.ols_status import get_ols_status as svc
    assert fw is svc, (
        f"Re-export stub broken: app.services.ols_status.get_ols_status "
        f"is not app.framework.ols_status.get_ols_status"
    )
