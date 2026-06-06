# S2P causal diagnostic plan

## Problem statement

SDK S2P Playwright remains unstable after several local backend performance fixes. The remaining failures cluster around score, confirm, learn, operational summary, and route readiness. The current evidence is not sufficient to choose a final architecture fix.

This plan defines measurements to prove the causal chain before changing architecture. It intentionally does not prescribe a final product architecture.

## What we know

- Trading, Purchasing, and DataOps Playwright gates are green.
- S2P remains unstable, especially around score/confirm/learn flows.
- S2P workers=1 improved the full run, but still had failures in `flows.spec.ts` around score/learn.
- S2P workers=4 has broader instability.
- Score-path conservation, full conservation status, and operational summary caches improved results but did not eliminate failures.
- `GET /api/s2p/preview/queue` currently calls the live app scorer during cold preview cache creation.
- `CompoundingScorer.score()` writes decisions through `graph_store.write_decision(...)`.
- `GET /api/s2p/preview/conservation` calls the same preview invoice cache and can trigger the same cold path.
- An isolated in-memory preview scorer experiment improved serial behavior but was backed out because product preview recommendations must reflect live learned persistent scorer/context-graph state.

## What we do not know

- Whether current full-run failures are primarily caused by backend contention, frontend request cancellation, weak Playwright readiness, hidden GET writes, or test state coupling.
- Whether `POST /api/s2p/score` completes after Playwright times out, or whether the browser request is actually cancelled.
- How much decision/outcome count grows during workers=1 and workers=4 runs.
- Whether dashboard/triage opens increase persistent decision count when preview cache is cold.
- Which request waterfalls overlap with scoring in the failing traces.
- Whether frontend `null` fallbacks are converting failures into permanent loading or missing-panel states.
- Whether state mutation by earlier tests changes later score/learn expectations.

## Hypotheses

### H1: Preview GET endpoints mutate live graph state

Opening Dashboard/Triage can call `/api/s2p/preview/queue`; cold queue creation can call live `scorer.score`; live `score()` writes decisions. This creates hidden state coupling and graph growth.

### H2: Score failures are caused by backend contention, not stale locators

Failure snapshots show selected invoices and `Scoring...`, while traces show `/api/s2p/score` with no completed response. The `Action index` locator is valid when a score response renders.

### H3: Frontend request cancellation or unmounting loses score responses

Tab remounts, React StrictMode, duplicate effects, or page reloads may cancel or orphan requests. If the backend returns 200 but frontend remains in `Scoring...`, this is a frontend lifecycle/state issue.

### H4: Frontend error handling hides failures as `null` or loading

`api.ts` catches many errors and returns `null`. Components often render loading or simply omit panels when `null` is returned. This can convert backend/network errors into missing UI without visible diagnostics.

### H5: S2P tests are not parallel-safe against one shared live backend

Tests score, confirm, override, and learn against the same persistent backend. Read-looking preview endpoints may also write. Workers > 1 may amplify mutation and contention.

### H6: Additional expensive endpoints remain in the full-suite waterfall

Endpoint caches helped known hotspots, but full S2P exercises many route effects at once. There may still be uncached full-history scans or SQLite contention.

## Evidence required for each hypothesis

| Hypothesis | Evidence that supports it | Evidence that weakens it |
|---|---|---|
| H1 preview GET mutation | Decision count increases after cold `/api/s2p/preview/queue`, `/api/s2p/preview/conservation`, Dashboard open, or Triage open. | Decision count remains stable across cold preview reads and screen opens. |
| H2 backend contention | Backend access log/timing shows score request remains in progress past Playwright assertion window; trace status is pending/aborted; direct concurrent waterfall reproduces slowness. | Backend returns fast 200 before UI failure, and browser receives it. |
| H3 frontend cancellation | Backend logs show 200 for score, while Playwright trace shows request aborted/status -1 or UI still `Scoring...`; component unmount/remount occurs during request. | Browser receives 200 and UI renders correctly; no unmount/cancel observed. |
| H4 null/loading masking | API helper logs show rejected fetch converted to `null`; component stays loading or omits panel without error. | Failures happen despite non-null successful API payloads. |
| H5 parallel unsafety | Workers=4 has much higher decision growth/failures than workers=1; tests mutate same first invoice/decision history; failures vary by test order. | Workers=1 and workers=4 behave similarly with isolated state. |
| H6 remaining endpoint hotspot | Full-suite network traces show slow/pending endpoints besides score/conservation/summary; backend timings show full-history scans. | Waterfall endpoints all complete quickly under full-suite load. |

## Instrumentation plan

Do not implement instrumentation until the measurements below show where it is needed. If needed, keep it temporary and behind a local diagnostic flag.

### Temporary backend timing instrumentation

Add request timing logs around these boundaries only after the non-invasive measurements identify a suspect:

- `s2p_preview.preview_queue`: total route time, cache cold/warm flag, number of `_score_invoice` calls.
- `s2p_preview.preview_conservation`: total route time, whether it triggered queue cache creation.
- `s2p.score_procurement_event`: time for factor computation, `scorer.score`, Neo4j write attempt, conservation status, novelty, graph link, response assembly.
- `s2p.learn_decision` and `s2p.record_outcome`: time for conservation snapshots, `scorer.learn`, receipt write, supplier profile update, evolver outcome.
- `s2p_performance.summary`: cache hit/miss and `_build_summary` duration.
- `SQLiteGraphStore._run_write`: write operation duration, retry count, transient lock count.

Recommended log fields:

- `request_id`
- route path
- method
- decision/invoice id if present
- cache hit/miss
- elapsed milliseconds per stage
- thread id
- exception class if any

Do not log full invoice payloads, full factor vectors, or large decision lists.

### Temporary frontend instrumentation

Add only if backend timings show responses are successful but UI remains stuck:

- In `api.ts`, log start/end/error for `/api/s2p/score`, `/api/s2p/preview/queue`, `/api/conservation/status`, `/api/s2p/performance/summary`.
- In `TriageScreen.handleScore`, log selected invoice id, scoring state transitions, whether `scoreInvoice()` returned null, and whether the component unmounted before response.
- In `DashboardScreen`, log queue/conservation request start/end and cancelled flag.
- In `PerformanceScreen`/`OperationalSummary`, log summary request start/end/null.

Use `console.debug` with a clear prefix such as `[s2p-diagnostic]`. Remove after diagnosis.

## DB/state measurement plan

Use the persistent live S2P SQLite DB because this is what the live backend uses:

`s2p-copilot/backend/app/data/s2p.db`

The count query should measure:

- decisions
- outcomes
- centroid checkpoints
- decision_entity_edges
- evolution events
- decisions by `metadata.source`, especially `s2p_preview`

### Count helper command

Run from:

`C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects`

```powershell
@'
import json
import sqlite3
from pathlib import Path

db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row

def one(sql, args=()):
    return con.execute(sql, args).fetchone()[0]

print("db", db.resolve())
print("decisions", one("select count(*) from decisions where domain='s2p'"))
print("outcomes", one("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'"))
print("centroid_checkpoints", one("select count(*) from centroid_checkpoints where domain='s2p'"))
print("decision_entity_edges", one("select count(*) from decision_entity_edges where domain='s2p'"))
print("evolution_events", one("select count(*) from evolution_events where domain='s2p'"))
rows = con.execute("""
    select json_extract(factors_json, '$.metadata.source') as source, count(*) as n
    from decisions
    where domain='s2p'
    group by source
    order by n desc
""").fetchall()
print("decisions_by_source", json.dumps([dict(row) for row in rows], indent=2))
'@ | python -
```

### Endpoint delta command: preview queue

```powershell
@'
import sqlite3
import time
from pathlib import Path
import requests

db = Path("s2p-copilot/backend/app/data/s2p.db")
base = "http://127.0.0.1:8002"

def count():
    con = sqlite3.connect(db)
    return con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0]

before = count()
t0 = time.perf_counter()
r = requests.get(base + "/api/s2p/preview/queue", timeout=60)
elapsed = time.perf_counter() - t0
after = count()
print("status", r.status_code, "elapsed", round(elapsed, 3), "before", before, "after", after, "delta", after - before)
print(r.text[:300])
'@ | python -
```

### Endpoint delta command: preview conservation

```powershell
@'
import sqlite3
import time
from pathlib import Path
import requests

db = Path("s2p-copilot/backend/app/data/s2p.db")
base = "http://127.0.0.1:8002"

def count():
    con = sqlite3.connect(db)
    return con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0]

before = count()
t0 = time.perf_counter()
r = requests.get(base + "/api/s2p/preview/conservation", timeout=60)
elapsed = time.perf_counter() - t0
after = count()
print("status", r.status_code, "elapsed", round(elapsed, 3), "before", before, "after", after, "delta", after - before)
print(r.text[:300])
'@ | python -
```

### Endpoint delta command: POST score

```powershell
@'
import sqlite3
import time
from pathlib import Path
import requests

db = Path("s2p-copilot/backend/app/data/s2p.db")
base = "http://127.0.0.1:8002"
payload = {
    "event_id": "S2P-DIAG-SCORE-001",
    "category": "price_variance",
    "amount": 3781.7,
    "supplier_id": "SUP-003"
}

def count(sql):
    con = sqlite3.connect(db)
    return con.execute(sql).fetchone()[0]

before_decisions = count("select count(*) from decisions where domain='s2p'")
before_outcomes = count("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'")
t0 = time.perf_counter()
r = requests.post(base + "/api/s2p/score", json=payload, timeout=60)
elapsed = time.perf_counter() - t0
after_decisions = count("select count(*) from decisions where domain='s2p'")
after_outcomes = count("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'")
print("status", r.status_code, "elapsed", round(elapsed, 3))
print("decisions", before_decisions, after_decisions, "delta", after_decisions - before_decisions)
print("outcomes", before_outcomes, after_outcomes, "delta", after_outcomes - before_outcomes)
print(r.text[:500])
'@ | python -
```

### Endpoint delta command: confirm/learn

This command scores a diagnostic invoice, then confirms the returned decision through `/api/learn`.

```powershell
@'
import sqlite3
import time
from pathlib import Path
import requests

db = Path("s2p-copilot/backend/app/data/s2p.db")
base = "http://127.0.0.1:8002"
score_payload = {
    "event_id": "S2P-DIAG-LEARN-001",
    "category": "price_variance",
    "amount": 4123.0,
    "supplier_id": "SUP-003"
}

def count(sql):
    con = sqlite3.connect(db)
    return con.execute(sql).fetchone()[0]

before_decisions = count("select count(*) from decisions where domain='s2p'")
before_outcomes = count("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'")
score = requests.post(base + "/api/s2p/score", json=score_payload, timeout=60)
print("score", score.status_code, score.text[:300])
score.raise_for_status()
data = score.json()
learn_payload = {
    "decision_id": data["decision_id"],
    "actual_action": data["action"],
    "outcome": "confirmed",
    "context": {"recovery_pct": 100, "source": "diagnostic"}
}
t0 = time.perf_counter()
learn = requests.post(base + "/api/learn", json=learn_payload, timeout=60)
elapsed = time.perf_counter() - t0
after_decisions = count("select count(*) from decisions where domain='s2p'")
after_outcomes = count("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'")
print("learn", learn.status_code, "elapsed", round(elapsed, 3), learn.text[:500])
print("decisions", before_decisions, after_decisions, "delta", after_decisions - before_decisions)
print("outcomes", before_outcomes, after_outcomes, "delta", after_outcomes - before_outcomes)
'@ | python -
```

### Playwright screen-open deltas: Dashboard

Run a count before and after an existing Dashboard-only test.

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects"
@'
import sqlite3
from pathlib import Path
db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
print(con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0])
'@ | python -
cd ".\copilot-sdk\e2e"
npx playwright test s2p/dashboard.spec.ts --project=s2p --grep "Dashboard loads with exception queue" --workers=1 --timeout=60000
cd "..\.."
@'
import sqlite3
from pathlib import Path
db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
print(con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0])
'@ | python -
```

### Playwright screen-open deltas: Triage

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects"
@'
import sqlite3
from pathlib import Path
db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
print(con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0])
'@ | python -
cd ".\copilot-sdk\e2e"
npx playwright test s2p/triage.spec.ts --project=s2p --grep "invoice list loads from queue" --workers=1 --timeout=60000
cd "..\.."
@'
import sqlite3
from pathlib import Path
db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
print(con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0])
'@ | python -
```

## Network waterfall measurement plan

### Browser-like concurrent API probe

Run from workspace root with live backend running:

```powershell
@'
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
import requests

base = "http://127.0.0.1:8002"
paths = [
    ("queue", "GET", "/api/s2p/preview/queue", None),
    ("preview_conservation", "GET", "/api/s2p/preview/conservation", None),
    ("full_conservation", "GET", "/api/conservation/status", None),
    ("performance_summary", "GET", "/api/s2p/performance/summary", None),
    ("performance_trajectory", "GET", "/api/s2p/performance/trajectory", None),
    ("what_if", "GET", "/api/s2p/performance/what-if?additional_correct=10&additional_incorrect=0", None),
    ("evidence_template", "GET", "/api/s2p/evidence/template?invoice_id=S2P-INV-0003&category=price_variance", None),
    ("score", "POST", "/api/s2p/score", {
        "event_id": "S2P-INV-0003",
        "category": "price_variance",
        "amount": 3781.7,
        "supplier_id": "SUP-003",
    }),
]

def call(item):
    name, method, path, payload = item
    t0 = perf_counter()
    try:
        if method == "POST":
            response = requests.post(base + path, json=payload, timeout=60)
        else:
            response = requests.get(base + path, timeout=60)
        return name, response.status_code, round(perf_counter() - t0, 3), response.text[:160]
    except Exception as exc:
        return name, "ERR", round(perf_counter() - t0, 3), repr(exc)

t0 = perf_counter()
with ThreadPoolExecutor(max_workers=len(paths)) as pool:
    for row in pool.map(call, paths):
        print(row)
print("total", round(perf_counter() - t0, 3))
'@ | python -
```

### Extract latest Playwright trace network records

Run after a failing Playwright run:

```powershell
cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\e2e"
Get-ChildItem .\test-results -Recurse -Filter trace.zip | Sort-Object LastWriteTime -Descending | Select-Object -First 10 FullName
```

Then inspect a selected trace:

```powershell
$trace = "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects\copilot-sdk\e2e\test-results\<failure-folder>\trace.zip"
tar -xOf $trace 0-trace.network | Select-String -Pattern "/api/s2p/score|/api/s2p/preview/queue|/api/s2p/preview/conservation|/api/conservation/status|/api/s2p/performance/summary" -Context 0,3
```

Record for each matching request:

- URL
- method
- start time if available
- status
- failure/error text
- whether response headers/body exist
- whether another navigation or reload happened before completion

## Frontend state measurement plan

Inspect whether score failures are pending, rejected, or successful-but-not-rendered.

Evidence to capture:

- Browser console output for `scoreInvoice` start/end/error.
- Whether `TriageScreen.handleScore` sets `scoring=true` and later `scoring=false`.
- Whether `scoreInvoice()` returns `null`.
- Whether selected invoice id changes while scoring is in progress.
- Whether component unmounts while score request is in flight.

Decision signals:

- If backend logs 200 and trace has response but UI does not render: frontend state/render issue.
- If backend logs 200 after test timeout and trace status is pending: backend latency/contention issue.
- If backend never sees request but UI shows `Scoring...`: frontend fetch/proxy/request cancellation issue.
- If `scoreInvoice()` returns `null`: network/server error is currently being hidden by `api.ts`.

## Playwright readiness audit

Current helpers mostly wait for the app shell or headings:

- `waitForAppShell` waits for `domcontentloaded` and non-empty `main`.
- Triage helpers accept `/queued|S2P-INV/i`; this can pass on text like `0 queued` before a usable invoice row exists.
- Score helpers click `Score` without waiting for `/api/s2p/preview/queue` to complete successfully.
- Score assertions wait for `Action index`, which is valid only after a successful score result renders.

Readiness evidence to collect:

- Does the failing test click Score before `S2P-INV-*` row buttons exist?
- Does the failing test click Score while `/api/s2p/preview/queue` is still pending?
- Does a `Promise.all([waitForResponse('/api/s2p/score'), click])` diagnostic pass where UI-only wait fails?
- Are tests sharing helper logic or duplicating stale readiness assumptions across files?

## Parallel-safety audit

### Tests that mutate state

These tests call score, learn, confirm, or override:

- `s2p/triage.spec.ts`
  - `score button exists and scoring shows recommendation with confidence`
  - `factor breakdown shows S2P factors`
  - `process context shows bottleneck when available`
  - `confirm button records reward or confirmed result`
  - `override path records reward and learned text`
  - `conservation status visible after learn`
  - `S2P actions are canonical and SOC actions are absent`
- `s2p/phase1.spec.ts`
  - `test_s2p_triage_select_invoice_shows_factors`
  - `test_s2p_triage_score_produces_result_card`
  - `test_s2p_override_shows_reason_dropdown`
- `s2p/flows.spec.ts`
  - `triage select score confirm reward round trip`
  - `score learn round trip preserves conservation projection`
  - `process context persists across reload after scoring`
  - `graded financial reward appears as decimal reward`

Also treat Dashboard/Triage/Insight/Evidence opens as possible mutators while preview queue uses live `score()`.

### Tests that assume state

- Queue loads and first invoice exists.
- First invoice can be repeatedly scored.
- Confirm/override operates on the most recent score card.
- Conservation and performance summaries return within UI assertion windows.
- Historical graph state does not affect scoring latency enough to miss Playwright waits.

### Workers=1 and workers=4 decision-count deltas

Run from workspace root:

```powershell
function Get-S2PCounts {
@'
import json
import sqlite3
from pathlib import Path
db = Path("s2p-copilot/backend/app/data/s2p.db")
con = sqlite3.connect(db)
counts = {
    "decisions": con.execute("select count(*) from decisions where domain='s2p'").fetchone()[0],
    "outcomes": con.execute("select count(*) from outcomes o join decisions d on d.decision_id=o.decision_id where d.domain='s2p'").fetchone()[0],
    "checkpoints": con.execute("select count(*) from centroid_checkpoints where domain='s2p'").fetchone()[0],
    "edges": con.execute("select count(*) from decision_entity_edges where domain='s2p'").fetchone()[0],
}
print(json.dumps(counts, sort_keys=True))
'@ | python -
}

cd "C:\Users\baner\CopyFolder\IoT_thoughts\python-projects\kaggle_experiments\claude_projects"
"before workers=1"; Get-S2PCounts
cd ".\copilot-sdk\e2e"
npx playwright test --project=s2p --workers=1 --timeout=60000 2>&1 | Tee-Object -FilePath pw_s2p_workers1_causal.txt
cd "..\.."
"after workers=1"; Get-S2PCounts

"before workers=4"; Get-S2PCounts
cd ".\copilot-sdk\e2e"
npx playwright test --project=s2p --workers=4 --timeout=60000 2>&1 | Tee-Object -FilePath pw_s2p_workers4_causal.txt
cd "..\.."
"after workers=4"; Get-S2PCounts
```

## Commands to run

Recommended order:

1. Capture baseline DB counts.
2. Measure individual endpoint deltas for preview queue, preview conservation, score, and learn.
3. Measure Dashboard and Triage Playwright open deltas.
4. Run browser-like concurrent API probe.
5. Run S2P workers=1 with count deltas.
6. Run S2P workers=4 with count deltas.
7. Extract latest trace network records for failed tests.
8. Only then decide whether temporary backend or frontend instrumentation is needed.

## Expected observations and decision rules

### Rule A: preview GET writes are confirmed

Observation:

- Decision count increases after cold `/api/s2p/preview/queue`, `/api/s2p/preview/conservation`, Dashboard open, or Triage open.

Justified fix family:

- Design a read-only live prediction architecture or explicitly model preview scoring side effects.
- If live learned scorer state must be reflected, add a read-only prediction path over the live scorer/centroids instead of using write-capable `score()`.
- Do not use disconnected in-memory preview scorer as final architecture.

### Rule B: score backend completes after Playwright timeout

Observation:

- Backend timing/access logs show `/api/s2p/score` returns 200 after the assertion window.
- Trace shows pending/status -1 before test ends.

Justified fix family:

- Backend contention reduction, query aggregation, write coalescing, or explicit scoring queue/priority.
- Playwright should wait for score response diagnostically, but final fix should not be arbitrary timeout increase.

### Rule C: score backend returns 200 before UI failure

Observation:

- Backend logs 200 quickly.
- Trace contains completed score response.
- UI remains `Scoring...` or score card absent.

Justified fix family:

- Frontend state lifecycle fix: request cancellation/versioning, `try/finally`, explicit error state, ensure `setScoring(false)` in all paths.
- Add UI tests around failed/null score handling.

### Rule D: request never reaches backend

Observation:

- UI enters `Scoring...`, trace shows request aborted or not present, backend logs no score request.

Justified fix family:

- Frontend/proxy/Vite request lifecycle investigation.
- Check tab remounts, navigation, service worker/cache, base URL mismatch, or duplicate click/state race.

### Rule E: failures correlate with workers=4 only

Observation:

- Workers=1 passes or has much lower mutation/latency; workers=4 fails and decision count grows faster.

Justified fix family:

- Test isolation architecture: per-worker backend state, test namespace, deterministic fixture setup, or serial S2P project as temporary containment.
- Do not hide this with DB reset between individual tests unless it is an explicit test-mode isolation contract.

### Rule F: `api.ts` null fallbacks hide errors

Observation:

- Console diagnostics show API rejects and frontend stores `null`.
- Panels keep loading or disappear without error state.

Justified fix family:

- Frontend error-state semantics and testable error rendering.
- API helpers should preserve failure details for critical flows like score/learn.

### Rule G: stale locator only

Observation:

- Network and backend are healthy.
- UI renders successful equivalent content without `Action index`.

Justified fix family:

- E2E locator update scoped to current product content.
- This is not currently the leading hypothesis because `Action index` is present in the score success card.

## What not to fix yet

- Do not increase Playwright timeouts as the primary fix.
- Do not add sleeps.
- Do not force-click disabled buttons.
- Do not accept loading states as success.
- Do not fake score, summary, conservation, or preview responses.
- Do not silently reset the DB as a hidden fix.
- Do not weaken assertions to generic page-loaded checks.
- Do not reintroduce a disconnected isolated preview scorer as final product behavior.
- Do not choose serial-only execution as a final answer until workers=1/workers=4 evidence proves parallel unsafety and an isolation plan is scoped.

## Architecture questions deferred until evidence is collected

- Should S2P preview recommendations be produced by a read-only live scorer facade?
- Should `CompoundingScorer` expose a non-mutating `predict`/`score_readonly` method?
- Should preview queue be an explicit "scored snapshot" resource with modeled writes instead of a GET read?
- Should S2P E2E use per-worker DB isolation?
- Should S2P live backend expose a test-only reset/seed endpoint guarded by environment?
- Should frontend critical APIs stop converting failures to `null`?
- Should S2P Playwright have a shared readiness helper that waits for API responses, not just DOM headings?
- Should GraphStore expose aggregate methods for all hot dashboard/performance metrics?
