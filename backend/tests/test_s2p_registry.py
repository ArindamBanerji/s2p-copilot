from __future__ import annotations

import inspect

from app.main import app
from app.state import S2P_MUTATION_PATHS


def test_all_keys_have_schemas():
    cache = app.state.s2p_tab_state_cache
    for key, spec in cache.registrations.items():
        assert spec.schema is not None, key


def test_all_keys_have_compute_fns():
    cache = app.state.s2p_tab_state_cache
    for key, spec in cache.registrations.items():
        assert callable(spec.compute_fn), key
        assert callable(spec.service_fn), key


def test_critical_keys_count():
    cache = app.state.s2p_tab_state_cache
    critical = [key for key, spec in cache.registrations.items() if spec.tier == "CRITICAL"]
    assert len(critical) <= 5
    assert {"iks"} <= set(critical)


def test_cold_keys_only_reset():
    cache = app.state.s2p_tab_state_cache
    for key, spec in cache.registrations.items():
        if spec.tier == "COLD":
            assert spec.invalidated_by == ("reset",), key


def test_standard_keys_have_events():
    cache = app.state.s2p_tab_state_cache
    for key, spec in cache.registrations.items():
        if spec.tier == "STANDARD":
            assert spec.invalidated_by, key


def test_no_parameterized_cached_static():
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not _has_cached_static(endpoint):
            continue
        path = str(getattr(route, "path", ""))
        methods = set(getattr(route, "methods", set()))
        signature = inspect.signature(endpoint)
        params = [
            name
            for name in signature.parameters
            if name not in {"request", "http_request"}
        ]
        assert "GET" in methods, path
        assert "{" not in path and "}" not in path, path
        assert not params, f"{path} has parameters {params}"


def test_mutation_handlers_locked():
    missing = []
    for route in app.routes:
        methods = {str(method).upper() for method in getattr(route, "methods", set())}
        path = str(getattr(route, "path", ""))
        if ("POST", path) not in S2P_MUTATION_PATHS:
            continue
        endpoint = getattr(route, "endpoint", None)
        source = ""
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            pass
        has_decorator = getattr(endpoint, "__mutation_lock_event__", None) is not None
        has_manual_lock = "get_mutation_lock(" in source and "apply_cache_invalidation_event(" in source
        if not has_decorator and not has_manual_lock:
            missing.append(path)
    assert not missing


def test_tab_state_cache_registered():
    cache = app.state.s2p_tab_state_cache
    paths = {getattr(route, "path", "") for route in app.routes}
    assert cache.copilot == "s2p"
    assert "/api/s2p/tab-state" in paths
    assert "/api/{copilot}/static-urls" in paths


def _has_cached_static(endpoint) -> bool:
    closure = getattr(endpoint, "__closure__", None)
    if not closure:
        return False
    names = getattr(endpoint, "__code__", None)
    if names is None:
        return False
    return "_cached_value" in names.co_names
