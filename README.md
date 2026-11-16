# Drive inventory, custody and shipments

A server-rendered web app for the Drive custodian, for **synthetic data only**. It is a client of the t6 API (`t6.client`): it owns no records and no database. Python 3.12 and uv are required.

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock
T6_API_URL=http://127.0.0.1:8000 T6_API_SECRET=... DRIVES_OUTBOX=/tmp/outbox.json .venv/bin/python app.py
```

Open http://127.0.0.1:5000. The development server binds loopback only. See [deployment](docs/deployment.md) for the settings, the container and what happens when the API is down.

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

t6 is pinned to t6-core commit `f4d3e58549397053fbea3d4edbbbb10c40c51de0` (main, before the 0.6.2 tag); `pyproject.toml`, `uv.lock` and `requirements.lock` name it.

A refusal for a step that cannot follow the last custody step (rule `custody`), or for an edit of a Drive that changed (`drive_changed`), is answered 409 and shown to the person; it is never queued or sent again.
