# Frontend end-to-end tests (real browser, real button)

`package.json` has no test runner (scripts are only `dev` / `build` / `start`), so
these tests drive the **actual running stack** — headless Chrome over CDP on
`localhost:9222`, `frontend` on `:3000`, `backend` on `:8001` — and assert against the
**rendered DOM**, not against `fetch()` calls. They are the tests for:

| Requirement | Test |
|---|---|
| Run Risk Analysis success (one click → classify → risk → complete) | `accept_web.py` |
| API failure (backend error surfaced, no silent "Partial", no stack trace) | `fail_web.py` |
| Progress state (stages, duplicate-click disable, bounded run) | `accept_web.py` |
| Completed refresh (results persist after a browser reload) | `accept_web.py` |
| Summary is a **descriptive briefing** — no risk counts, findings, rules or recommendations (Part 4) | `check_sections.py`, `accept_web.py` |
| Risk assessment contains risk counts/results + Phase-8 clause fields | `check_sections.py`, `accept_web.py` |
| Live contract search — debounce, states, stale-request cancellation | `search_live.py` |
| Spec queries in the browser (vend, balanced, saas, nda, …) | `search_live.py` |
| Elasticsearch down → explicit search error, page stays usable, Retry recovers | `search_live.py` |
| Login, account indicator/menu, logout, route guards, 401 vs 403, open redirect, browser Back | `auth_flow.py` |

## Prerequisites

```bash
google-chrome --headless=new --remote-debugging-port=9222 \
  --user-data-dir=/tmp/opencode/chrome-prof --no-sandbox &
```

A JWT for the contract's org (tests take it via `--token`; nothing is stored here).

## Run

```bash
# Phase 16 — acceptance: processed-but-not-analyzed contract, click, observe, reload
python accept_web.py \
  --url http://localhost:3000/contracts/<id> --token "$TOKEN" \
  --expect-clauses 13 --label acceptance --reprocess \
  --out /tmp/acc

# Phase 17 — failure: mock the risk endpoint to 503, then retry
python fail_web.py \
  --url http://localhost:3000/contracts/<id> --token "$TOKEN" \
  --expect-clauses 11 --out /tmp/fail

# Phases 8 / 9–15 — read-only section ownership + clause-card field audit
python check_sections.py \
  --url http://localhost:3000/contracts/<id> --token "$TOKEN" \
  --expect-clauses 13 --out /tmp/sec
```

Each script writes `<out>.json` (machine-readable checks) plus DOM dumps, and prints
`PASS`/`FAIL` per check with a non-zero exit on failure.

## Part 2 — contract discovery search

`search_live.py` needs a JWT and drives `/dashboard` (it sets the token itself, so no
`--token` flag):

```bash
# Part 2 — O..T states + the eight spec queries + incremental typing
python search_live.py --mode accept        # 21 assertions

# Part 2 — Step 22: with Elasticsearch STOPPED, then STARTED again
python search_live.py --mode fail          # 4 assertions (ES down)
python search_live.py --mode recover       # 2 assertions (Retry after ES is back)
```

`--mode recover` deliberately does **not** reload the page: it must observe the same
session that was left in the error state, otherwise the Retry assertion is meaningless.
Summaries land in `/tmp/opencode/p2_search_<mode>_summary.json`, screenshots alongside
them (`p2_accept_*.png`, `p2_fail_error.png`, `p2_recover.png`).

## Part 3 — authentication (real browser, spec tests I–V)

`auth_flow.py` drives the **real login form, real header account control and real
logout** against `:3000` / `:8001`. It needs real credentials — nothing is injected
into `localStorage` except in the two negative tests (invalid/expired token):

```bash
# logged-out guards · login · identity · menu · refresh · navigation · 403
# · logout · no-credential leak · invalid/expired token · browser Back
python e2e/auth_flow.py --email 'user@example.com' --password '...'
```

It prints `PASS`/`FAIL` per assertion, writes
`/tmp/opencode/p3_auth_flow_summary.json` plus `p3_*.png` screenshots, and exits
non-zero on any failure.

What it proves, from the rendered DOM only:

| Test | Assertion |
|---|---|
| I / J / K (+2, I3) | logged-out `/dashboard`, `/portfolio`, `/search`, `/templates`, `/policies`, `/ml-evaluation`, `/contracts/[id]` land on `/login?next=…`, with **no** protected UI and **no** data request |
| L | real form login returns to the requested `?next=` path |
| M / N | account indicator exists and shows the real email |
| O / P | menu opens; organization + role match PostgreSQL |
| V/V2/V3/V4 | Escape closes + restores focus, Enter/Space open, hover does not |
| S | refresh restores the session, never flashes `/login` |
| NAV | account stays consistent across all main routes |
| U | a real 403 leaves the session alive (role lowered, then restored) |
| Q / R | logout clears the credential; direct `/portfolio` afterwards → `/login` |
| LEAK | no `Authorization` header is sent after logout |
| T | invalid and expired tokens are rejected and cleared |
| NR | `?next=` never leaves the origin (external, `//host`, `/\host`, `/login` → `/dashboard`; internal paths honoured) |
| BACK | browser Back after logout cannot re-open protected content |

`accept_web.py --reprocess` first clicks the page's own **Re-process document** button,
waits for the pipeline, and only then runs the acceptance flow — this both resets the
fixture without touching the database and exercises the re-process handler.

`cdp_shot.py` is the shared CDP helper (connect, `evaluate`, screenshot).

## Part 4 — contract summary vs risk assessment (section ownership)

`check_sections.py` now verifies the strict separation on the rendered DOM:

* `#summary` reads like a document briefing — `Overview`, `Agreement type`,
  `Main agreement areas` — with **no** clause-count opener and none of the
  `FORBIDDEN` risk strings (findings, priorities, counts, rules, attention);
* `#risk-assessment` still owns analysis coverage, the risk-overview counts,
  top attention items, the clause-by-clause list and the state filters.

```bash
python check_sections.py \
  --url http://localhost:3000/contracts/<id> --token "$TOKEN" \
  --expect-clauses <n> --out /tmp/opencode/p4_cs_<label>
```

Run for `02_balanced_saas_agreement.pdf`, `03_nda.pdf` and
`04_employment_agreement.pdf` (8 checks each).
