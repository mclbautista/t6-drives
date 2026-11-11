# Ticket #5: synthetic shipment candidate evidence

Scope: ticket #5 under parent spec #1, based on integration `c6f7102`. This records synthetic software verification, not owner acceptance or deployment authorization. No issue boxes are changed.

## Observable behavior and evidence

- `tests/test_shipments.py` uses real HTTP request handling and temporary SQLite with pinned core validation. Repeated interactive selections produce one released event and membership per permanent ID. Unknown or empty batches and invalid metadata leave the complete populated database dump unchanged.
- SQLite trigger fault injection separately rejects snapshot insertion, the second event insertion and the second membership insertion. HTTP returns sanitized 500 diagnostics; full database dumps, including earlier histories and snapshots, remain unchanged after each failed request. This exercises actual rollback rather than replacing persistence with a fake.
- Concurrent HTTP clients release two batches while another edits facts. Both shipments contain their full deduplicated memberships with matching event references, no foreign-key violations and internally consistent old-or-new facts; restart retains saved output. Independent legitimate requests can create separate shipments: this is not request-retry idempotency.
- Saved details/list output retains accepted and suspected serials separately, Assignment-derived available label, description, numeric capacity, review state/note, recipient text/reference, dates, actor mode and Project display. Fact corrections, rejected invalid/colliding edits, Project name/page-binding refreshes, recipient-related later custody entries and app restart do not rewrite the original. Corrections append custody evidence, not a new version of the manifest.
- Release/return preserves open Assignments. Cached Project choices survive reader outage; unresolved text also supports releases without an upstream dependency. Only the external Project reader is substituted.
- Cross-origin and missing-token writes are rejected; GET routes do not release Drives. Recipient and actor markup is escaped, missing facts are explicit, and failure responses/logs do not include synthetic private-marker payloads. Actor attribution remains unknown by default or explicitly self-declared in synthetic mode, never authenticated.
- `tests/test_shipments_browser.py` starts real loopback HTTP and Chromium. It navigates inventory, multi-Drive release, details and shipment list; invokes the native print button and Chromium PDF renderer; checks print navigation is hidden and unknown facts/serial distinctions are visible. After fact edits, return, Project refresh and server restart, the saved manifest HTML is identical and browser PDF rendering still succeeds. No console/page errors are permitted. PDF byte identity is not claimed because browser-generated metadata may vary.

## Reproduction and artifact policy

With the locked Python 3.12 environment and Chromium installed, run from the repository (stop another server on port 5000 first):

```sh
BROWSER_ARTIFACTS=<private-scratch-directory> .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py assignments.py custody.py shipments.py tests
.venv/bin/mypy
.venv/bin/ruff check app.py assignments.py custody.py shipments.py tests
.venv/bin/ruff format --check app.py assignments.py custody.py shipments.py tests
.venv/bin/python -m build
```

The candidate checkpoint passed 31 tests, including three real Chromium scenarios, plus compilation, mypy, Ruff lint/format and sdist/wheel build. Remote CI is not claimed green. Finalization reruns the same gates before committing.

Artifacts are retained outside git: `shipment-original.pdf`, `shipment-after-restart.pdf`, `shipment-print.png`, `shipment-trace.zip` and `shipment-result.json`. All application fixtures are invented. Raw traces can contain synthetic session tokens, source paths and browser metadata and are not public evidence; only this sanitized summary belongs in git. Temporary SQLite stores are discarded. No real workbook, recipient, client/Project export or operational account was used.

The implementation and regression tests were inherited for bounded finalization; this checkpoint does not independently establish red-first provenance for every scenario. It does not claim a new TDD cycle for documentation changes.

## Remaining boundaries

This is an app-owned shipment boundary, not the complete core DriveStore. Historical output deliberately avoids the core helper that reconstructs current physical facts. Snapshot update/delete triggers and absence of mutation routes protect app/storage behavior, not tamper resistance against a host administrator. Imported repeated movements and import-time reconstruction remain later work. Full independent review and integration remain the parent agent's responsibility. No push, merge, issue closure, real-input access, operational gate changes, deployment, backup/restore, real-data rehearsal, production import or workbook freeze is included. G1–G5 remain unresolved.
