# ⚠️ GROUNDING CONTRACT (non-negotiable)

**These rules apply to every AI coding agent working in this repo.**

1. **Docs are aspirational until proven in code.** Check actual source files.
2. **Cite file + line for every behavioral claim.**
3. **Code and tests beat docs.** Discrepancy = DRIFT, report and stop.
4. **Check downstream consumers before changing interfaces.**
5. **Verify after every change:** `cd backend && python -m pytest tests/ -v`

---

## How to Think (read first, every session)

1. State assumptions before coding. Never silently pick a field name.
2. Minimum code that solves the problem.
3. Surgical changes only.
4. Verify after every step — "this should work" is not verification.
5. Before adding a constant: grep to check if it exists under a different name.

---

## What This Repo Is

S2P (Source-to-Pay) Copilot — a procurement domain copilot. Uses the same
GAE scoring engine as the SOC Copilot but with different tensor dimensions
and domain-specific actions.

### Architecture
- Shares FastAPI backend with gen-ai-roi-demo-v4-v50 (mounted at /api/s2p/)
- Uses GAE ProfileScorer with S2P DomainConfig
- S2P tensor: (5, 5, 7) = 175 values
- penalty_ratio = 5.0 (NOT SOC's 20.0)

### Consumed by
- gen-ai-roi-demo-v4-v50/backend/app/main.py (router mount)
- Playwright test 15 (POST /api/s2p/score)

---

## Domain Isolation (critical)

S2P is an independent domain. It must never depend on SOC-specific code.

- **Never** import from `domains.soc` or any SOC config file.
- **Never** use SOC tensor dimensions — S2P is (5,5,7).
- **Never** use SOC constants (SOC_PROFILE_CENTROIDS, SOC_SCORING_ACTIONS).
- penalty_ratio = 5.0, not SOC's 20.0.
- The legacy six-category, four-action, six-factor S2P tensor has been removed; verify config.py remains canonical (5,5,7).

---

## Rules

- Do NOT use git directly. User handles all git operations.
- Tests must run from `s2p-copilot/backend/tests/`, not the repo root.
- asyncio.run() not asyncio.get_event_loop() (Windows Python 3.11+).

## After Any Change

1. `cd backend && python -m pytest tests/ -v` (70 tests must pass)
2. Run Playwright test: `npx playwright test -g "s2p" --reporter=list`
3. If you changed the router prefix: update gen-ai-roi-demo-v4-v50/main.py mount
