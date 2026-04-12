# S2P Copilot Dependencies

## Consumed by
- gen-ai-roi-demo-v4-v50/backend/app/main.py (router mount at /api/s2p/)
- Playwright test 25 (POST /api/s2p/score)

## Depends on
- GAE / graph-attention-engine (ProfileScorer, build_profile_scorer, KernelType)
- ci-platform (graph client — optional, for Neo4j decision history)

## Isolation contract
- Never import from `app.domains.soc` — S2P must be independently deployable
- Tensor: (N_CATEGORIES=6, N_ACTIONS=4, N_FACTORS=6) = 144 values
- penalty_ratio = 5.0 (SOC uses 20.0 — never change without re-running convergence validation)
- S2P categories are procurement domain; SOC security categories must never appear

## Verification after any change
1. `python -m pytest tests/ -v`  (70+ tests must pass)
2. Playwright: `npx playwright test -g "s2p" --reporter=list`
3. If router prefix /api/s2p/ changes: update gen-ai-roi-demo-v4-v50/main.py mount point
