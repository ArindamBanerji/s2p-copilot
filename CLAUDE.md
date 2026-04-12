# CLAUDE.md — S2P Copilot (Procurement)

## Architecture
- Shares FastAPI backend with gen-ai-roi-demo-v4-v50
  (mounted at /api/s2p/)
- Uses GAE for scoring (same ProfileScorer, different DomainConfig)
- S2P tensor: (5, 5, 8) = 200 values. penalty_ratio = 5.0
- SOC A=4 / S2P A=5 — intentional asymmetry

## Consumed by
- gen-ai-roi-demo-v4-v50/backend/app/main.py (router mount)
- Playwright test 15 (POST /api/s2p/score)

## After any change
1. python -m pytest tests/ -v (58 tests must pass)
2. Run Playwright test: npx playwright test -g "s2p" --reporter=list
3. If you changed the router prefix: update
   gen-ai-roi-demo-v4-v50/main.py mount point

## Never do these
- Import SOC-specific code (domains/soc/) — S2P must be independent
- Change the /api/s2p/ prefix without updating the mount point
- Use SOC tensor dimensions (6,4,6) — S2P is (5,5,8)
- Change penalty_ratio without re-running S2P convergence validation
