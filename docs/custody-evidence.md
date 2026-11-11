# Ticket #4: synthetic custody verification

Scope: https://github.com/mclbautista/t6-drives/issues/4 under parent #1. Built from integration `221aee36ed7e2a04698af1107f8b00637c5bb69d` in an isolated worktree. This is single-Drive activity only; shipment/batch/snapshot work remains #5. No operational acceptance boxes are changed.

## Behavior delivered

- Drive details link to a server-rendered custody form. Receive, release, return and retire use permanent IDs and pinned core `CustodyEvent`, `Ref`, `Party`, `CustodyKind` and `derive_status` validation/derivation. No accepted serial/review clearance/state-transition prerequisites were added. Retirement requires core's nonblank reason.
- SQLite retains immutable event payloads in append order with stable event IDs, independent business dates, optional recipient text and correction links to an existing event on the same Drive. No event edit/delete interface exists. Each append validates under a SQLite write transaction before its single insert; no mutable status column exists.
- Details and inventory/status filter call the same derived-status function; empty history means unknown. History is displayed in numbered append order, explicitly distinguished from business dates (which may be unknown or backdated). A correction is a new ordinary core event linked visibly to its original, not a replacement or a new state-machine kind.
- Recipient free text is stored separately from an optional existing structured Contact/Studio reference. Typing a name does not fabricate an identity or select a PartyKind. Existing Project page references or unresolved text are retained under core's exactly-one Ref contract; blank Project input is explicitly shown as unknown/unresolved. This does not create a registry/cache, resolve names or provide #3's cached selection UI.
- Default actor policy is `unknown`. The app factory can explicitly select `custody_actor_policy="self-declared-synthetic"` for isolated synthetic tests; supplied actor text is stored as unresolved text, never a Notion account ID. Nonempty actor submissions under unknown policy are rejected. Each event retains whether its actor was unknown or self-declared/not authenticated, even after restart under a different policy.
- G2 remains unresolved: self-declared names are an implementation proposal/test mode, not owner-approved attribution. Forms/details say synthetic-only and operational deployment remains gated. G1–G5 and the existing loopback restriction remain in force. Append-only guarantees the app/storage interface, not resistance to a host administrator modifying SQLite.

## Executed candidate verification (historical)

Using the existing integration Python 3.12 virtual environment without changing its packages:

```sh
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
# 21 tests passed, including both real Chromium scenarios
.venv/bin/mypy
# Success: no issues found in 6 source files
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m compileall -q app.py custody.py tests
uv build
# sdist and wheel built; custody.py included
```

`tests/test_custody.py` uses HTTP, real temporary SQLite and pinned core: all four kinds on an uncertain Drive, missing retirement reason, invalid dates/kinds/references, rejected unknown-Drive writes, unknown dates/parties/actors, structured references, escaped recipient/reason/actor text, concurrent appends, rejected cross-Drive correction, backdated correction with append-order status, unchanged complete details after rejected mutation/fact edit, history unchanged by valid fact correction and app restart. Existing inventory tests retain optimistic revision/ownership coverage. CSRF/cross-Origin and non-mutating GET/edit/delete rejection are checked on custody routes. An invalid correction produces no application diagnostic log; error responses do not echo submitted invalid private-marker input.

`tests/test_custody_browser.py` runs real Chromium against a disposable loopback HTTP server and SQLite. It navigates from a review-flagged/no-serial Drive through all event forms, rejects retirement without a reason without appending, appends a backdated correction via its original's link, compares original rendered rows unchanged, checks explicit unknown actor and status filtering. All fixtures are invented; no real workbook, accounts, network operations or deployment inputs were used.

Red/green provenance: the initial HTTP tests failed at absent custody routes and unsupported actor-policy factory argument, then passed after implementation. The browser scenario initially failed to locate the Kind select by its accessible name, then passed after explicit accessible select labels were added. Additional reference, concurrent-write and escaping tests are regression coverage and passed on first execution, not separate red-first cycles.

## Remaining acceptance boundaries

The isolated candidate could not check #3 tables. The subsequent [combined integration checkpoint](evidence.md) verifies custody during cache outage and preservation/independence of both histories with #3 present. #5 owns shipment atomicity and historical snapshots. No importer/receipt/reconstruction API, production migration/rollback, container deployment, boundary verification, backup/restore or real-data rehearsal is claimed. The focused `Custody` adapter is app-owned and is **not** represented as a full core `DriveStore`. Actor import conventions require the explicit G2 decision; current synthetic unknown representation is not an approved operational convention. No real deployment is authorized by passing these tests.
