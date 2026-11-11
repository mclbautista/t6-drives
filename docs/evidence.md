# Synthetic verification and remaining evidence

## Combined tickets #3–4 integration checkpoint

Both reviewed candidates were merged into the existing draft integration branch, #3 before #4. `app.py`, package/typecheck scope and CI conflicts were resolved by retaining both features. `tests/test_integration.py` adds three HTTP/real-SQLite regressions for cache outage plus custody writes, Assignment/custody mutation independence, AND filter intersection, retained navigation, both histories after fact edits/restart, missing/stale revision rejection and unknown ownership/default actor preservation. The Project/text filter regression initially failed because the inherited helper ORed them; independent predicates now intersect their Drive-level results.

The extended Assignment Chromium scenario also follows Record custody and appends receipt/release/return while checking two retained Assignments, one still open. The original independent custody Chromium scenario remains. Integration exposed a broad text locator colliding with the G2 warning and a cross-session whole-page assertion comparing new Assignment CSRF secrets; selectors were scoped to Assignment rows and only CSRF values are normalized for the restart assertion. No history evidence is discarded.

Executed successfully from the integration worktree:

```sh
.venv/bin/mypy
# Success: no issues found in 9 source files (check_untyped_defs, not strict)
BROWSER_ARTIFACTS=/Users/cyrilbautista/.hermes/cache/scratch/drives-implementation/integration-3-4-browser .venv/bin/python -m unittest discover -s tests -v
# 26 tests passed, including both real Chromium scenarios; no skips
.venv/bin/ruff check app.py assignments.py custody.py tests
.venv/bin/ruff format --check app.py assignments.py custody.py tests
# All checks passed; 9 files already formatted
.venv/bin/python -m compileall -q app.py assignments.py custody.py tests
.venv/bin/python -m build
# sdist and wheel built with app.py, assignments.py and custody.py
```

Only invented fixtures and disposable stores were used. No push, issue closure, acceptance-checkbox update, main merge, real exports/workbooks or deployment was performed. Independent integration review is still required; remote CI is not claimed green. G1–G5 remain unresolved. This verifies combined synthetic behavior, not whole-parent acceptance, shipment snapshots/imports, production migrations, recovery or operational authorization.

## Ticket #3 candidate checkpoint (historical evidence): Project cache and Assignments

The completed synthetic candidate adds `tests/test_assignments.py` at the approved HTTP/UI + real SQLite seam; only the external Project reader is substituted. The bootstrap scenario uses a real JSON export through `refresh-projects --export`, then exercises successful changed-active/retained-legacy refresh, permanent-ID filtering, sanitized outage/incomplete/missing-binding/rollback failures, unresolved text that equals a code without guessed resolution, and restart persistence. Assignment scenarios cover meaningful no-serial/review-flagged Drives, unknown role/start, repeated labels, concurrent open use, explicit end, invalid date atomicity, repeat-end rejection, unknown Drives, escaped remarks, CSRF/Origin protection and non-mutating GETs. The real Chromium scenario now also navigates cached/unresolved Assignment creation, explicit ending, retained history and open-use inventory filters independently of custody, with no browser console/page errors.

Final local commands passed:

```sh
.venv/bin/python -m unittest discover -s tests -v
# 18 tests passed, including the extended real Chromium scenario
.venv/bin/python -m compileall -q app.py assignments.py tests
.venv/bin/mypy
# Success: no issues found in 5 source files (check_untyped_defs, not strict typing)
.venv/bin/ruff check app.py assignments.py tests
.venv/bin/ruff format --check app.py assignments.py tests
.venv/bin/python -m build
# sdist and wheel built; wheel explicitly checked to contain assignments.py
```

The README's real `python -m flask --app 'app:create_app(...)' refresh-projects --export ...` command was also executed against a disposable synthetic database/export, returned `Complete Project cache saved.`, and a fresh app readback returned HTTP 200 with the cached Project. Refresh is not an operational browser route. CI/build/typecheck/lint scope includes the new module; remote CI is not claimed green.

Prior Assignment red cycles were recorded during candidate development; the added export/browser coverage is regression evidence, not a new red-first implementation cycle. The initial extended browser run failed on an overly exact selector for a wrapping select label; correcting the test locator passed without changing application behavior.

This supplies ticket #3's Assignment/cache coverage, not completion of every parent acceptance criterion. Custody during outage and custody/Assignment independence after custody mutations require T4 integration verification. Shipment snapshots, importer/receipts/exceptions, general migrations/rollback, backup/restore, deployment boundaries and G1–G5 remain outside this candidate. No integration, real-data use, production activation or issue-checkbox changes were performed. Independent review remains the integrating agent's responsibility.

## Earlier ticket #2 checkpoint (historical evidence)

The principal seam is observable HTTP/UI against the real Flask app, temporary SQLite and core pinned to `f4b7618c2eb14bb5f85ea3c1fa597843448bc3af`. No fake store, mocked core, real inputs or live operational services are used.

- `tests/test_inventory.py` covers creation, stable permanent ID/reopen, full facts/review, unknown/T6/Studio ownership, text/review searches, accepted-only exact lookup, visibly uncertain candidates, invalid values, editing, serial normalization/correction/removal, explicit promotion and collisions. Sole-identity removal is rejected. Stale edits and stale promotion return 409 without replacing newer facts.
- Concurrent HTTP requests use separate clients and SQLite connections. Existing Drives attempting to swap occupied serials both fail with their complete detail facts unchanged, including after reopen. Existing Drives racing for one free serial produce one winner and one unchanged loser; concurrent creation likewise yields one accepted-serial owner. These tests cover the evidence currently stored, not future Assignment/custody/shipment histories.
- Browser writes are checked for CSRF tokens, Host/Origin rejection, non-mutating GETs and escaped HTML. Validation-error regressions verify submitted synthetic text is absent from the error response and a rejected edit offers only a reload link, not a blank replacement form. Reloading retrieves current stored facts and revision before retrying.
- `tests/test_browser.py` starts a real loopback HTTP server and Chromium using Playwright. It exercises create/detail/inventory/edit navigation, uncertain search, accepted-only lookup, unknown ownership, stale-form rejection and explicit promotion. The checkpoint run passed with Chromium `143.0.7499.4` and no browser console/page errors. Synthetic screenshots (`uncertain-inventory.png`, `edited-detail.png`), `browser-trace.zip` and `browser-result.json` are retained outside git when `BROWSER_ARTIFACTS` is set; see README for reproduction. Traces contain synthetic form/session data and are not public deployment evidence.

The following commands were rerun successfully with Python 3.12 at this checkpoint:

```sh
BROWSER_ARTIFACTS=<private-scratch-directory> .venv/bin/python -m unittest discover -s tests -v
# 16 tests passed, including the real Chromium scenario
.venv/bin/python -m compileall -q app.py tests
.venv/bin/mypy
# Success: no issues found in 3 source files (check_untyped_defs, not strict typing)
.venv/bin/ruff check app.py tests
.venv/bin/ruff format --check app.py tests
.venv/bin/python -m build
# Built sdist and wheel using the declared setuptools backend
```

A fresh Python 3.12 virtual environment installed `requirements.lock` through uv and the built wheel with `--no-deps`. An isolated (`python -I`) import of that installed wheel served a real loopback GET `/` returning HTTP 200. Installed core metadata recorded the exact required Git commit. Package caches may be used; this is not a cache-free network reproducibility claim. The isolated build fetched its declared backend successfully. Earlier DNS/socket restrictions no longer describe this checkpoint. GitHub Actions is configured to install locked runtime/dev dependencies and Chromium, then run tests, compilation, mypy, build and Ruff; remote CI has not been claimed green.

## Development provenance

Initial red/green commands used Python 3.12, an archive of the exact core merge from local Git history, and locally cached Flask dependencies. Scratch transcripts were retained outside the repository:

- `python -m unittest discover -s tests` failed before the app existed (`ModuleNotFoundError`), then passed the initial form test.
- The same command failed create/reopen at the missing form token, then passed after create and SQLite reopening were implemented.
- The same command failed full-facts/normalization behavior, then passed after the inventory implementation.
- Later security, collision, editing and concurrency scenarios were added against the implementation and passed on first execution. They are regression evidence, not separate red-first cycles. The requested strict one-failing-test-per-slice discipline was not fully met.
- A later red-first validation-error test exposed submitted synthetic text echoed in a 400 response; after clearing form values on errors it passed. Independent review then identified that the blank retry form could accidentally clear existing facts or carry a refreshed revision after invalid stale input. A new regression failed on the active cleared error form, then passed after validation errors were changed to offer only a fresh GET via a reload link.

## Acceptance boundaries

This checkpoint supplies ticket #2's inventory/fact/browser foundation for parent AC01, AC02, AC03, AC12 and AC18, not completion of those cross-slice parent criteria. There is no populated Assignment, custody or shipment history, so preservation across fact edits and rejected writes must be rechecked when those workflows exist. Project/status filtering, import concurrency/receipts/exceptions, historical snapshots/printing, general migrations/rollback, backup/restore, container persistence and operational isolation remain deferred. Only fresh initialization and additive revision/ownership columns for earlier synthetic stores are present; no production schema migration or rollback is established.

Committed fixtures and documentation use invented data only; browser artifacts and disposable stores stay outside git. No real workbooks, private source evidence or operational configuration were accessed for this checkpoint. Synthetic tests do not establish office-only access, authenticated attribution, recovery or real-data preservation. G1–G5 remain open. No accounts, role enforcement, live sync, automatic merging, deployment, real-data rehearsal, production import, workbook freeze or cutover is authorized by this slice. Do not check or close parent acceptance on this evidence.
