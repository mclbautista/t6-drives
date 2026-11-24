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
| `/` search and filters, with each Drive's open Project use | `drive list` |
| `/projects` | `drive projects` (API counts, including text-only Projects) |
| `/projects/view?project=<id or text>` | `drive list`, `movement list` |
| `/movements` | `movement list` (Project, person, text and date-range filters) |
| `/new` serial first | `drive list` (exact accepted serial), then `drive add` |
| `/drive/<id>` | `drive get` |
| `/drive/<id>/edit` | `drive edit` (every fact; the form sends the Drive's `version` as `expected_version`, and a Drive that changed meanwhile is shown again) |
| `/drive/<id>/promote` | `drive promote-serial` (the "Confirm serial" button) |
| Project use on the Drive page | `drive assign` (with remarks), `drive unassign` (by Assignment number, so one whose Project is text only can be ended) |
| `/drive/<id>/qr`, `/qr/<code or id>` | `drive get`, `drive find`, `drive edit` (a printable QR label of the Drive's code; scanning it opens the Drive). A QR code is optional and is not on the forms: the "Add QR" button sets it equal to the Drive's ID, and a T6-owned Drive shows its label with no setup. |
| `/drive/<id>/catalog` | `drive catalog-snapshots`, `drive catalog-diff`, `drive catalog-at` (scans, what each changed, the contents on a date with `?on=YYYY-MM-DD`), and `drive catalog-snapshot` (upload a JSON manifest) |
| `/drive/<id>/retire` | `drive retire` |
| `/drive/<id>/custody` (legacy direct URL) | `drive receive`, `drive return`, `drive retire` |
| `/shipments/new` | `drive release` (with a reason and a free-text recipient) |
| `/shipments`, `/shipments/<id>` | `drive shipments`, `drive shipment` (the manifest shows the facts stored at release) |
| `/movements/<id>/correct` | `movement list`, `movement correct` (a correction is a new entry; the original stays in the log, with a link each way) |
| `/drive/<id>/place` | `drive edit` (place TABLE, CABINET, SHELF, BOX or none, and T6 ownership; the search line shows both) |
| `/exceptions` | a to-do list in four groups (give a photo a Drive, pick a Project, fix a row in the sheet, overdue T6 drives), each item one sentence and one button, with counts at the top: `drive capture list` (unresolved captures with text and photo), `drive capture photo`, `drive capture` with a Drive, `movement list` (needs a Project) and `movement correct` (set it), `drive overdue`, `drive reload report` (unmapped Project names), `drive exceptions` (rows the reload could not load) |
| `/captures/<id>/photo`, `/captures/<id>/drive` | `drive capture photo`, `drive list` and `drive capture` (pick a Drive for an unresolved capture) |
| Movement photos | a movement recorded from TRANSMITTAL shows its capture photo as a thumbnail that opens it (`drive capture photo`) |
| `/outbox` | "Waiting to send": writes waiting for the API, or refused by it |
| `/health` | none (process readiness) |

Who acts is the Person the API secret is bound to; the app sends no actor. Projects for the Assignment, custody and shipment forms and the inventory filter come from the API's project reads (kept 5 minutes in memory); only registered Projects can be chosen. A form carries an idempotency key made when it is rendered, so a double submit is one write.

## Checks

```sh
uv pip install --python .venv/bin/python -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app.py gateway.py forms.py outbox.py custody.py catalog.py shipments.py runtime.py glossary.py tools tests
.venv/bin/mypy
.venv/bin/ruff check app.py gateway.py forms.py outbox.py custody.py catalog.py shipments.py runtime.py glossary.py tools tests
.venv/bin/ruff format --check app.py gateway.py forms.py outbox.py custody.py catalog.py shipments.py runtime.py glossary.py tools tests
.venv/bin/python -m build
```

The tests serve t6's real `VerbAPI` on a temporary database (`tests/support.py`); nothing is faked. The API side needs `httpx`, `PyYAML` and `openpyxl`, which are development dependencies only. If Chromium is installed elsewhere, set `DRIVES_CHROMIUM` to its path.

t6 is pinned to release tag `v0.6.25`; `requirements.lock` pins the exact commit.

A scan manifest of about 8,000 files is 1 to 2 MB, so only `POST /drive/<id>/catalog` accepts a body up to 12 MB (every other form stays at 64 KB, and the Host, Origin and CSRF checks run first). A scan the API cannot take is not queued, because the Outbox page would print the whole manifest; the page says the API is unavailable, and uploading again is the same write. The app waits up to 60 seconds for the API to save a scan. The t6 API accepts that large request on its tailnet listener only.

A refusal for a step that cannot follow the last custody step (rule `custody`), or for an edit of a Drive that changed (`drive_changed`), is answered 409 and shown to the person; it is never queued or sent again.

The main navigation is Search, Projects, Movements, Exceptions and Waiting to send. Recording happens in
TRANSMITTAL; the legacy custody and release URLs remain available directly. Projects and Movements
pages show API reads, with movement logs newest first. Retire remains linked from each Drive.
Every rendered page uses the API's practice flag for its practice banner; no flag is guessed when
the API is unavailable.

A capture photo is read from the API for each request and passed on to the browser with `Cache-Control: no-store`; the app never writes it to disk or logs it, and sends it nowhere else. Like every page, it is open to anyone who can reach the app on the office network.

Search sends the typed text to `drive list`, which matches label, serial and Project code; each result line shows the status, `place` and `holder` the API returns, and nothing is derived in the app.

## Plain words

Pages show a Drive by its label, with its serial and Project underneath. An ID (anything like `t6d_` and hex) appears only in a link, a form value or a data attribute, and in the collapsed "Details" line of a Drive page; never in text or a drop-down. The plain words are in `glossary.py` (Project use, movement, serial, serial to check, Confirm serial, Waiting to send; Retire, Drive and Movement stay). `tests/test_plain_pages.py` renders every page with fixture data and fails on an ID or a system word (Assignment, custody event, accepted or suspected serial, Promote, Outbox, "[person") in the visible text. A message from the API goes through the `plain` filter before it is shown. A new page must be added to the route list in that test.
