## How to Think (read first, every session)

### 1. State Assumptions Before Coding
- Before implementing, state your assumptions explicitly
- If multiple interpretations exist, present them — don't pick silently
- NEVER silently pick a property name, field type, or API path — state it

Example of WRONG: "I'll use {id: $val} in the Cypher query"
Example of CORRECT: "Assuming property 'id'. Verifying: grep shows 'alert_id'. Using that."

### 2. Minimum Code That Solves the Problem
- No features beyond what was asked. No abstractions for single-use code.
- If 200 lines could be 50, rewrite it.

### 3. Surgical Changes
- Touch only what you must. Don't "improve" adjacent code.
- Every changed line traces directly to the request.

### 4. Goal-Driven Execution
- Before starting: Step → verify: [specific check] for each step.
- "This should work" is never verification. Show the output.

### 5. Dual Representation Rule
- Before adding any constant/tensor/property: check if it exists under a different name.
- Grep: get_actions(), SCORER_ACTIONS, SOC_PROFILE_CENTROIDS, alert_id, decision_id

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

### Domain Isolation
- Never import from domains.soc. No SOC constants in S2P config.
- S2P tensor: see config.py (BACKLOG-051: spec says (5,5,8), actual is (6,4,6) — decision pending).
- penalty_ratio = 5.0 (not SOC 20.0).

### No Silent Failure on Displayed Metrics
- If a try/except computes a NUMBER shown in the UI: the except block
  must set a flag (estimated=True, source="fallback") — never bare pass
- If a try/except computes OPTIONAL enrichment: bare pass is acceptable
- NEVER hardcode a number that looks like a computed metric (0.89, 23, 127)
  without a comment explaining why it's a constant and not computed
- The test: if the graph is empty, does the UI show zeros or plausible-looking
  fake numbers? If fake numbers: it's a mockup, not a fallback.

### AGE Is Not Neo4j — Three Critical Differences

1. **SET n = {props} WIPES all other properties**
   - NEVER: `SET d = {category: 'x'}` — destroys every other property
   - ALWAYS: `SET d.category = 'x'` — preserves all other properties
   - SAFE for bulk: `SET d += {a: 1, b: 2}` — merges, preserves existing
   - AGEClient rejects the destructive form with ValueError

2. **Concurrent writes to the same node fail**
   - "Entity failed to be updated: 3" = PostgreSQL row lock conflict
   - AGEClient retries with jitter (3 attempts, 100-250ms backoff)
   - Avoid concurrent writes to the same node when possible

3. **Decision nodes must be created atomically with their edge**
   - ALWAYS: `MATCH (a:Alert) CREATE (d:Decision {...})-[:DECIDED_ON]->(a)`
   - NEVER: CREATE Decision as one query, then edge as a second
   - If MATCH finds no Alert, no Decision is created (proven atomic)
