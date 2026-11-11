# Synthetic Drive inventory, Assignments and custody (tickets #2–4)

A local, server-rendered inventory tracer bullet for **synthetic data only**. Python 3.12 and uv are required. From this directory:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock
DRIVES_DB=/tmp/synthetic-drives.sqlite .venv/bin/python app.py
```

Open http://127.0.0.1:5000. Stop with Ctrl-C. The path is persistent across restarts; use a disposable path for synthetic tests. The server binds only loopback and accepts only localhost/127.0.0.1 on port 5000. Restarting invalidates existing form tokens; reload the form. Do not expose this development server or use real data.

Install the locked development tools and Chromium before running the full suite (on Linux, use `playwright install --with-deps chromium`):

```sh
uv pip install --python .venv/bin/python -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py assignments.py custody.py tests
.venv/bin/mypy
.venv/bin/ruff check app.py assignments.py custody.py tests
.venv/bin/ruff format --check app.py assignments.py custody.py tests
.venv/bin/python -m build
```

The browser test starts a disposable loopback server on port 5000, so stop any existing app first. Set `BROWSER_ARTIFACTS` to a private scratch directory to retain its synthetic screenshots, trace and JSON result; otherwise they are discarded with the test's temporary directory.

Core is pinned to PR #90 merge `f4b7618c2eb14bb5f85ea3c1fa597843448bc3af`. `requirements.lock` pins the runtime dependency graph; installation needs access to PyPI and the public core Git repository. No account or role enforcement exists.

`Inventory` is an app-owned **partial inventory interface**, not an implementation of the complete core `DriveStore`. Core's public store has no general fact edit. The adapter rehydrates the core immutable `Drive` and applies edits with `dataclasses.replace`, invoking the same core validation and normalization; SQLite transactions and a unique accepted-serial constraint serialize collisions. Edit and promotion forms carry a revision; stale or missing revisions return HTTP 409 without committing. The app additionally rejects non-finite capacities and invalid form values at the HTTP boundary. A suspected candidate is separate and promotion is explicit. Blank serial fields remove them; a bracket-only value is invalid. Ownership explicitly distinguishes unknown, T6 and an existing Studio reference. App-owned metadata preserves unknown ownership without changing core's `owner_studio_id=None` meaning T6; no Studio is created or synchronized. Existing stores from the earlier synthetic slice retain its explicit T6 interpretation.

## Offline Project cache and Assignments

From a trusted host shell, bootstrap or explicitly refresh the durable cache using an authorized complete JSON export (synthetic data only for this slice):

```sh
.venv/bin/python -m flask --app 'app:create_app("<disposable-synthetic-database-path>")' refresh-projects --export <authorized-export.json>
```

The export contract is `{"complete": true, "projects": [...]}`. Each Project contains a permanent `t6_id` (`t6p_` plus 32 lowercase hex digits), `name`, optional `code`, `active_page_id` (page reference or null) and `legacy_page_ids` (list of former page references). Codes/names are display facts, never identity. Permanent IDs and all prior page bindings must be retained in later complete exports, and legacy pages cannot become active again. A Project without an active page remains displayable/filterable but cannot be newly selected. The app never creates registry records or writes upstream. Failed/incomplete refreshes retain the last complete snapshot, timestamp and references and show a sanitized error. There is no refresh browser route; this command is a host-shell boundary, not authenticated application administration.

Drive details support adding cached-page or unresolved-text Assignments with optional labels, roles, dates and remarks, plus explicit validated ending without rewriting earlier labels. Unknown roles/start dates remain unknown. Open Project use is distinct from custody; inventory Project/text filters select **open** Assignments. Unresolved text is never matched to names/codes. Cache, Assignments and explicit ends survive restart; ordinary use does not call the external reader. `Assignments` is an app-owned interface because pinned core has add/history but no end mutation; it uses core `Assignment`, `Ref`, `Role` and shared date validation rather than claiming to implement the full `DriveStore`.

## Single-Drive custody

Drive details retain both Assignment forms/history and the Record custody link. Custody records receipt, release, return, retirement and append-only corrections; status is derived in append order, not business-date order. Unknown dates, actors and references remain explicit. Default actor policy is `unknown`; only an explicit `custody_actor_policy="self-declared-synthetic"` factory option accepts self-declared synthetic actor text, which is never authenticated identity. See [custody evidence](docs/custody-evidence.md).

Project-cache refresh failures do not block custody. Adding/ending Assignments does not mutate custody, and receipt/release/return does not close Assignments. Search, exact serial, review, Project, unresolved-text and custody-status filters combine with AND at the Drive level; the two Project-use filters may be satisfied by separate open Assignments. Fact edits retain both histories and reject stale/missing revisions. Unknown ownership stays unknown.

Shipment history, imports, general migrations/rollback, backup/restore and deployment are not implemented. Only fresh initialization, additive Assignment/cache/custody tables and revision/ownership columns for earlier synthetic stores are present. G1–G5 (host/access, attribution, write access, recovery, production cutover) remain unresolved; no operational use, real-data rehearsal, production import, workbook freeze or cutover is authorized by this slice. See [evidence](docs/evidence.md).
