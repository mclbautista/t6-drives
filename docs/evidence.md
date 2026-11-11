# Synthetic verification and remaining evidence

The automated seam is Flask's HTTP test client against a temporary SQLite file and the real core domain. No fake store, mocked core, real inputs or external services are used. Creation, permanent ID/reopen, full facts/review, searches including accepted-only exact lookup and visibly uncertain candidate, invalid values, editing, serial normalization, promotion and collisions, sole-identity removal, concurrent claims, escaped HTML, CSRF, Host, Origin and non-mutating GET are covered by the test suite. See `tests/test_inventory.py` for executable scenarios. The concurrency test makes two real HTTP requests with separate clients and separate SQLite connections.

During implementation the following red/green commands were run using Python 3.12, the exact core commit extracted from local Git history, and locally cached Flask dependencies (paths omitted here). Scratch transcripts were retained outside the repository:

- `python -m unittest discover -s tests` — failed before the app existed (`ModuleNotFoundError`), then passed the initial form test.
- The same command — failed create/reopen at the missing form token, then passed after create and SQLite reopening were implemented.
- The same command — failed full-facts/normalization behavior, then passed after the inventory implementation.
- Later security, collision, editing and concurrency scenarios were added against the implementation and passed on first execution. They are regression evidence, not claimed as separate red-first cycles. The requested strict one-failing-test-per-slice discipline was therefore not fully met.

The core checkout used for verification was shown by `git merge-base --is-ancestor f4b7618c2eb14bb5f85ea3c1fa597843448bc3af HEAD` to contain the required merge; tests used an archive of that exact merge, not its newer HEAD. The environment could not resolve GitHub, so a clean network installation from `requirements.lock` and CI have not been verified locally.

Remaining: browser-based navigation has not been independently exercised; tests use Flask's real in-process HTTP transport. There is no populated Assignment, custody or shipment history yet, so preservation across editing must be tested with those future slices. Project/status filtering, durable import/exception evidence, history, snapshots, migrations/rollback, backup/restore and operational isolation are deferred. Synthetic tests do not establish office-only access or real-data preservation. G1–G5 remain open; do not mark tickets or operational acceptance complete on this evidence.

A later red-first validation-error test exposed submitted synthetic text echoed in a 400 response; after clearing form values on errors it passed. This means a rejected form must be re-entered. An attempt to start the loopback server in this sandbox returned `Operation not permitted` while binding the socket; no live-browser or socket smoke test is claimed. The in-process HTTP suite passed. No type checker was configured or run for this untyped initial slice; compilation and Ruff are the configured static checks.
