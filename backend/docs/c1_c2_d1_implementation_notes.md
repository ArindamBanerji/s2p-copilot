# C1+C2+D1 Implementation Notes

## Summary

This change removes the obsolete S2P legacy scorer module and moves remaining callers to the SDK CompoundingScorer path.

It also fixes the S2P performance what-if conservation threshold formula.

The implementation keeps the S2P tensor shape at `(5, 5, 7)`.

The implementation keeps the S2P penalty ratio at `5.0`.

No SDK code was changed.

No frontend code was changed.

## Deleted Legacy Module

`app/domains/s2p/scorer.py` was deleted.

That module owned a module-level singleton around the old GAE scorer.

The singleton was independent from `app.state.scorer`.

Keeping it allowed score, learn, preview, and IKS paths to diverge.

Removing it makes `app.state.scorer` the canonical runtime scorer.

## Preview Router Changes

`app/routers/s2p_preview.py` no longer imports or constructs the old scorer.

Preview queue scoring now reads the scorer from `request.app.state.scorer`.

The scorer call was adapted to the SDK shape: `score(factors_dict, category_name, metadata=...)`.

The previous scorer accepted a factor vector and category index.

The response adapter still returns the existing preview JSON fields.

Those fields include invoice ids, supplier data, category, confidence, probabilities, factors, and factor vectors.

The preview cache still exists for scored invoices.

`reset_preview_state()` now clears the preview invoice and centroid caches only.

The compounding preview uses an isolated in-memory CompoundingScorer.

That avoids writing 1000 synthetic simulation decisions into the live app scorer.

## Demo Changes

`demo/s2p_demo.py` no longer imports `app.domains.s2p.scorer`.

The demo constructs a CompoundingScorer with the same pattern used by `app/main.py`.

It uses an in-memory SQLiteGraphStore with domain `s2p`.

It uses `S2PRewardFunction`.

The demo score adapter converts factor vectors into S2P factor dictionaries.

The demo reads IKS from the CompoundingScorer trajectory.

## Conservation Formula Fix

`app/routers/s2p_performance.py` now imports `compute_theta_min` from `gae.calibration`.

The old formula used `23.53 / (PENALTY_RATIO * new_verified)`.

That was wrong because `PENALTY_RATIO` is not the override rate.

The canonical formula is `compute_theta_min(override_rate, verified)`.

The what-if endpoint derives override rate from verified decisions and projected additional incorrect decisions.

Verified decisions are read through the existing graph store access pattern.

When no positive override rate is available, the endpoint returns a safe `theta_min` fallback of `1.0`.

The performance summary formula was not changed.

## Tests

Tests were updated away from the deleted legacy scorer module.

Preview tests cover response compatibility and app-state scorer use.

Performance tests cover canonical theta-min computation.

Legacy-removal tests assert the old scorer file is absent.

The tensor shape test still asserts `(5, 5, 7)`.

## Residual Risks

Preview queue scoring writes score records through the canonical app scorer when the preview cache is cold.

The compounding preview intentionally uses an isolated scorer to avoid live-state pollution.

The override-rate derivation treats explicit override outcomes and incorrect verified decisions as overrides.

If future verified decision records add a more precise override field, the derivation should use it.
