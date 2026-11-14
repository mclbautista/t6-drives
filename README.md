# Synthetic Drive inventory, Assignments, custody and shipments (tickets #2–5)

A local, server-rendered inventory tracer bullet for **synthetic data only**. Python 3.12 and uv are required. From this directory:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock
DRIVES_DB=/tmp/synthetic-drives.sqlite .venv/bin/python app.py
```

Open http://127.0.0.1:5000. Stop with Ctrl-C. The path is persistent across restarts; use a disposable path for synthetic tests. The development server binds only loopback on port 5000; the application accepts exact localhost/127.0.0.1 authorities with valid ports for isolated dynamic-port checks. Restarting invalidates existing form tokens; reload the form. Do not expose this development server or use real data.

Install the locked development tools and Chromium before running the full suite (on Linux, use `playwright install --with-deps chromium`):

```sh
uv pip install --python .venv/bin/python -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py assignments.py custody.py shipments.py import_store.py imported_shipments.py imports.py runtime.py backups.py rehearsal.py tools tests
.venv/bin/mypy
.venv/bin/ruff check app.py assignments.py custody.py shipments.py import_store.py imported_shipments.py imports.py runtime.py backups.py rehearsal.py tools tests
.venv/bin/ruff format --check app.py assignments.py custody.py shipments.py import_store.py imported_shipments.py imports.py runtime.py backups.py rehearsal.py tools tests
.venv/bin/python -m build
```

The browser test starts a disposable loopback server on port 5000, so stop any existing app first. Set `BROWSER_ARTIFACTS` to a private scratch directory to retain its synthetic screenshots, trace and JSON result; otherwise they are discarded with the test's temporary directory.

Core is pinned to release tag `v0.3.4`. `requirements.lock` pins the runtime dependency graph; installation needs access to PyPI and the public core Git repository. No account or role enforcement exists.

`Inventory` is an app-owned **partial inventory interface**, not an implementation of the complete core `DriveStore`. Core's public store has no general fact edit. The adapter rehydrates the core immutable `Drive` and applies edits with `dataclasses.replace`, invoking the same core validation and normalization; SQLite transactions and a unique accepted-serial constraint serialize collisions. Edit and promotion forms carry a revision; stale or missing revisions return HTTP 409 without committing. The app additionally rejects non-finite capacities and invalid form values at the HTTP boundary. A suspected candidate is separate and promotion is explicit. Blank serial fields remove them; a bracket-only value is invalid. Ownership explicitly distinguishes unknown, T6 and an existing Studio reference. App-owned metadata preserves unknown ownership without changing core's `owner_studio_id=None` meaning T6; no Studio is created or synchronized. Existing stores from the earlier synthetic slice retain its explicit T6 interpretation.

## Offline Project cache and Assignments

From a trusted host shell, bootstrap or explicitly refresh the durable cache using an authorized complete JSON export (synthetic data only for this slice):

```sh
.venv/bin/python -m flask --app 'app:create_app("<disposable-synthetic-database-path>")' refresh-projects --export <authorized-export.json>
```

The export contract is `{"complete": true, "projects": [...]}`. Each Project contains a permanent `t6_id` (`t6p_` plus 32 lowercase hex digits), `name`, optional `code`, `active_page_id` (page reference or null) and `legacy_page_ids` (list of former page references). Codes/names are display facts, never identity. Permanent IDs and all prior page bindings must be retained in later complete exports, and legacy pages cannot become active again. A Project without an active page remains displayable/filterable but cannot be newly selected. The app never creates registry records or writes upstream. Failed/incomplete refreshes retain the last complete snapshot, timestamp and references and show a sanitized error. There is no refresh browser route; this command is a host-shell boundary, not authenticated application administration.

Drive details support adding cached-page or unresolved-text Assignments with optional labels, roles, dates and remarks, plus explicit validated ending without rewriting earlier labels. Unknown roles/start dates remain unknown. Open Project use is distinct from custody; inventory Project/text filters select **open** Assignments. Unresolved text is never matched to names/codes. Cache, Assignments and explicit ends survive restart; ordinary use does not call the external reader. `Assignments` is an app-owned interface because pinned core has add/history but no end mutation; it uses core `Assignment`, `Ref`, `Role` and shared date validation rather than claiming to implement the full `DriveStore`.

## Single-Drive custody

Drive details retain both Assignment forms/history and the Record custody link. Custody records receipt, release, return, retirement and append-only corrections; status is derived in append order, not business-date order. Unknown dates, actors and references remain explicit. Both launchers default to `self-declared-required`: custody and shipment writes require 1–120 printable self-declared actor characters, never authenticated identity. The factory retains `unknown` and `self-declared-synthetic` only for isolated synthetic compatibility. No accounts or roles are enforced. See [rehearsal preparation](docs/rehearsal.md) for approved G2/G3 limits. See [custody evidence](docs/custody-evidence.md).

Project-cache refresh failures do not block custody. Adding/ending Assignments does not mutate custody, and receipt/release/return does not close Assignments. Search, exact serial, review, Project, unresolved-text and custody-status filters combine with AND at the Drive level; the two Project-use filters may be satisfied by separate open Assignments. Fact edits retain both histories and reject stale/missing revisions. Unknown ownership stays unknown.

## Atomic shipments and historical manifests

Inventory links to Release shipment and Shipments. Select permanent Drive IDs for one atomic handover: duplicate selections release once, and empty/unknown batches or persistence failures release none. Details and browser printing read a saved display snapshot, not current physical facts. Accepted and suspected serials remain separate; missing facts are marked unknown. Later fact edits, Project refreshes and custody corrections do not rewrite the original. Corrections append custody evidence. Release/return does not end open Assignments. Cached Projects or unresolved text allow use during upstream outage, without live Contact/Studio access. See [shipment evidence](docs/shipment-evidence.md).

Inventory and movement imports are available through host-only CLI commands; see [import evidence](docs/import-evidence.md) and [movement evidence](docs/movement-evidence.md). Ticket #8 adds pinned container packaging, synthetic schema versioning and a disposable rollback rehearsal; see the [deployment runbook](docs/deployment.md). The local named-volume container smoke is currently **disk-blocked**, not accepted: the Docker VM reports zero available blocks and SQLite initialization fails with disk full. One private host-scratch bind probe passed synthetic SQLite startup and empty health, but does not count as default named-volume persistence, restart/replacement or full container recovery verification. CI retains the mandatory real named-volume smoke, with no skip or alternate-storage fallback. The untested host-backed-volume option was removed. Encrypted backup/restore mechanics and their synthetic verification are documented in the [recovery runbook](docs/recovery.md). [Ticket #10 preparation](docs/rehearsal.md) adds a host-only generated-input rehearsal and fail-closed real preflight; it is intentionally not included in the web container. G2/G3 are accepted as described there, but production G1, unconfigured G4 and separate G5 approval remain open. No real inputs may be accessed while recovery is unconfigured; synthetic success does not authorize operational use, production import or workbook freeze. See [evidence](docs/evidence.md).
