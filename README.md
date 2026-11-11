# Synthetic Drive inventory (ticket #2)

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
.venv/bin/python -m compileall -q app.py tests
.venv/bin/mypy
.venv/bin/ruff check app.py tests
.venv/bin/ruff format --check app.py tests
.venv/bin/python -m build
```

The browser test starts a disposable loopback server on port 5000, so stop any existing app first. Set `BROWSER_ARTIFACTS` to a private scratch directory to retain its synthetic screenshots, trace and JSON result; otherwise they are discarded with the test's temporary directory.

Core is pinned to PR #90 merge `f4b7618c2eb14bb5f85ea3c1fa597843448bc3af`. `requirements.lock` pins the runtime dependency graph; installation needs access to PyPI and the public core Git repository. No account or role enforcement exists.

`Inventory` is an app-owned **partial inventory interface**, not an implementation of the complete core `DriveStore`. Core's public store has no general fact edit. The adapter rehydrates the core immutable `Drive` and applies edits with `dataclasses.replace`, invoking the same core validation and normalization; SQLite transactions and a unique accepted-serial constraint serialize collisions. Edit and promotion forms carry a revision; stale or missing revisions return HTTP 409 without committing. The app additionally rejects non-finite capacities and invalid form values at the HTTP boundary. A suspected candidate is separate and promotion is explicit. Blank serial fields remove them; a bracket-only value is invalid. Ownership explicitly distinguishes unknown, T6 and an existing Studio reference. App-owned metadata preserves unknown ownership without changing core's `owner_studio_id=None` meaning T6; no Studio is created or synchronized. Existing stores from the earlier synthetic slice retain its explicit T6 interpretation.

Assignments, custody, shipment history, Project and status filters, imports, general migrations/rollback, backup/restore and deployment are not implemented. Only fresh initialization and additive revision/ownership columns for earlier synthetic stores are present. In particular, preservation of populated histories after fact edits remains to be verified when those features exist. G1–G5 (host/access, attribution, write access, recovery, production cutover) remain unresolved; no operational use, real-data rehearsal, production import, workbook freeze or cutover is authorized by this slice. See [evidence](docs/evidence.md).
