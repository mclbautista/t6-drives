# Drive inventory, custody and shipments

A server-rendered web app for the Drive custodian, for **synthetic data only**. It is a client of the t6 API (`t6.client`): it owns no records and no database. Python 3.12 and uv are required.

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock
T6_API_URL=http://127.0.0.1:8000 T6_API_SECRET=... DRIVES_OUTBOX=/tmp/outbox.json .venv/bin/python app.py
```

Open http://127.0.0.1:5000. The development server binds loopback only. See [deployment](docs/deployment.md) for the settings, the container and what happens when the API is down.

## Office network

By default the app is reachable from its own host only. Two optional settings open it to the office network (decided in #17):

| Name | Meaning |
| --- | --- |
| `DRIVES_BIND` | Host address compose publishes on (default `127.0.0.1`). Set it to the office-network address of the host, never `0.0.0.0`. |
| `DRIVES_ALLOWED_HOST` | The one host name or IP address, besides `localhost` and `127.0.0.1`, that the app accepts in the `Host` header: no port, scheme, list or wildcard. Empty by default; a malformed value stops start-up. |

Decision (owner, 2026-11-16, for gates G1 to G3): the app runs on the studio server next to the t6 API and is reachable from the office network only. Everyone who can open it writes as the drive custodian, through the custodian's own t6 API secret; there is no per-user login.

The app speaks plain HTTP, so it must never be published beyond the office network.

Check: open it from another office machine, which works; a request whose `Host` is any other name is answered 400. See [deployment](docs/deployment.md#office-network).

## Screens

| Screen | t6 verb |
| --- | --- |
| `/` inventory and filters, with each Drive's open Project use | `drive list` |
| `/new` | `drive add` |
| `/drive/<id>` | `drive get` |
| `/drive/<id>/edit` | `drive edit` (every fact; the form sends the Drive's `version` as `expected_version`, and a Drive that changed meanwhile is shown again) |
| `/drive/<id>/promote` | `drive promote-serial` |
| Assignments on the Drive page | `drive assign` (with remarks), `drive unassign` (by Assignment number, so one whose Project is text only can be ended) |
| `/drive/<id>/custody` | `drive receive`, `drive return`, `drive retire` |
| `/shipments/new` | `drive release` (with a reason and a free-text recipient) |
| `/shipments`, `/shipments/<id>` | `drive shipments`, `drive shipment` (the manifest shows the facts stored at release) |
| `/exceptions` | `drive exceptions` |
| `/outbox` | writes waiting for the API, or refused by it |
| `/health` | none (process readiness) |

Who acts is the Person the API secret is bound to; the app sends no actor. Projects for the Assignment, custody and shipment forms and the inventory filter come from the API's project reads (kept 30 seconds in memory); only registered Projects can be chosen. A form carries an idempotency key made when it is rendered, so a double submit is one write.

## Checks

```sh
uv pip install --python .venv/bin/python -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py gateway.py forms.py outbox.py custody.py shipments.py runtime.py tools tests
.venv/bin/mypy
.venv/bin/ruff check app.py gateway.py forms.py outbox.py custody.py shipments.py runtime.py tools tests
.venv/bin/ruff format --check app.py gateway.py forms.py outbox.py custody.py shipments.py runtime.py tools tests
.venv/bin/python -m build
```

The tests serve t6's real `VerbAPI` on a temporary database (`tests/support.py`); nothing is faked. The API side needs `httpx`, `PyYAML` and `openpyxl`, which are development dependencies only. If Chromium is installed elsewhere, set `DRIVES_CHROMIUM` to its path.

t6 is pinned to release tag `v0.6.2`; `requirements.lock` pins the exact commit.

A refusal for a step that cannot follow the last custody step (rule `custody`), or for an edit of a Drive that changed (`drive_changed`), is answered 409 and shown to the person; it is never queued or sent again.
