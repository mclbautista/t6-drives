"""The Drive web app: a client of the t6 API. It owns no records."""

import logging
import math
import secrets
from collections.abc import Mapping
from typing import Any

from flask import Flask, abort, redirect, render_template_string, request, session
from t6.client import Client
from t6.verbs import DriveExceptionsInput, DriveGetInput, DriveListInput, Refused
from t6.vocabulary import DRIVE_ROLES
from werkzeug.exceptions import HTTPException, NotFound

from custody import HISTORY, register_custody
from forms import HIDDEN, finish, new_key, notice, posted_key
from gateway import ApiRefused, ApiUnavailable, Gateway
from outbox import Outbox
from shipments import register_shipments

# Everything `drive add` and `drive edit` take; `ownership` on the form stands for two inputs.
ADD_FIELDS = (
    "label",
    "serial",
    "suspected_serial",
    "review_note",
    "form_factor",
    "description",
    "brand",
    "model",
    "capacity_tb",
    "format",
    "owner_studio_id",
    "purchase_date",
    "warranty",
    "remarks",
)
LABELS = {
    "serial": "Accepted serial",
    "suspected_serial": "Suspected serial (uncertain)",
    "capacity_tb": "Capacity (TB)",
    "format": "Filesystem",
    "form_factor": "Form factor",
    "owner_studio_id": "Owner Studio ID (existing reference only)",
    "purchase_date": "Purchase date",
    "review_note": "Review note",
}

INDEX = """<!doctype html><title>Inventory</title><h1>Inventory</h1><a href="/exceptions">Import exceptions</a> <a href="/shipments">Shipments</a> <a href="/shipments/new">Release shipment</a> <a href="/new">New Drive</a> <a href="/outbox">Outbox</a><form><label>Search <input name="q" value="{{ q }}"></label><label>Exact accepted serial <input name="serial"></label><label>Review <select name="review"><option value="">All</option><option value="yes">Needs review</option><option value="no">Not flagged</option></select></label><label>Project (open use) <select name="project"><option value="">All Projects</option>{% for p in projects %}<option value="{{ p.t6_id }}" {% if project == p.t6_id %}selected{% endif %}>{{ p.name }} — code: {{ p.project_code or 'unknown' }} — {{ p.t6_id }}</option>{% endfor %}</select></label><label>Status <select name="status" aria-label="Status"><option value="">All</option>{% for choice in ["unknown", "in custody", "released", "retired"] %}<option {% if status == choice %}selected{% endif %}>{{ choice }}</option>{% endfor %}</select></label><button>Find</button></form><ul>{% for d in drives %}<li><a href="/drive/{{ d.id }}">{{ d.id }}</a> {{ d.label or '' }} {{ d.description or '' }} Custody status: {{ d.status }} {% if d.serial %}Accepted: {{ d.serial }}{% endif %} {% if d.suspected_serial %}Suspected (uncertain): {{ d.suspected_serial }}{% endif %} {% if d.needs_review %}Needs review{% endif %} Open Project use: {% for a in d.open_assignments %}{{ display(a.project) }} ({{ a.label or 'no label' }}, {{ a.role or 'unknown role' }}, from {{ a.start or 'unknown date' }}){% if not loop.last %}; {% endif %}{% else %}none{% endfor %}</li>{% endfor %}</ul>"""
FORM = (
    """<!doctype html><title>{{ title }}</title><h1>{{ title }}</h1>
<a href="/">Inventory</a>{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<form method="post">"""
    + HIDDEN
    + """
{% if editing %}<input type="hidden" name="expected_version" value="{{ version }}">{% endif %}
<p><label for="ownership">Ownership</label> <select id="ownership" name="ownership">{% for choice, label in [("unknown", "Unknown ownership"), ("t6", "T6"), ("studio", "Studio")] %}<option value="{{ choice }}" {% if values.get("ownership", "unknown") == choice %}selected{% endif %}>{{ label }}</option>{% endfor %}</select></p>
{% for name in fields %}<p><label>{{ labels.get(name, name|capitalize) }}
{% if name == 'form_factor' %}<select name="form_factor"><option value="">Unknown</option>{% for choice in ['portable', 'powered desktop'] %}<option value="{{ choice }}" {% if values.get(name) == choice %}selected{% endif %}>{{ choice }}</option>{% endfor %}</select>
{% else %}<input name="{{ name }}" value="{{ values.get(name) or '' }}">{% endif %}</label></p>{% endfor %}
<p><label><input type="checkbox" name="needs_review" {% if values.get('needs_review') %}checked{% endif %}> Needs review</label></p><button>Save</button></form>"""
)
DETAIL = (
    """<!doctype html><title>Drive</title><h1>Drive {{ d.id }}</h1><a href="/">Inventory</a> <a href="/drive/{{ d.id }}/edit">Edit</a> <a href="/drive/{{ d.id }}/custody">Record custody</a><p>Custody status: {{ d.status }}</p>{{ history|safe }}<dl>{% for name in fields %}{% if d.get(name) is not none %}<dt>{{ labels.get(name, name|capitalize) }}</dt><dd>{{ d[name] }}</dd>{% endif %}{% endfor %}<dt>Ownership</dt><dd>{{ ownership }}</dd><dt>Needs review</dt><dd>{{ 'Yes' if d.needs_review else 'No' }}</dd></dl>{% if d.suspected_serial %}<form method="post" action="/drive/{{ d.id }}/promote">"""
    + HIDDEN
    + """<button>Promote suspected serial to accepted</button></form>{% endif %}
<section><h2>Project use / Assignment history</h2><p>Assignments record Project use, not physical custody.</p>
<ul>{% for a in assignments %}<li data-assignment-id="{{ a.number }}">{{ display(a.project) }} — Label: {{ a.label or 'Unknown' }} — Role: {{ a.role or 'Unknown' }} — Start: {{ a.start or 'Unknown' }} — End: {{ a.end or 'Open' }} — {{ a.remarks or '' }}
{% if a.open %}<form method="post" action="/drive/{{ d.id }}/assignments/{{ a.number }}/end">"""
    + HIDDEN
    + """<label>End date <input name="end" type="date" required></label><button>End Assignment</button></form>{% endif %}</li>{% endfor %}</ul>
<h3>Add Assignment</h3><form method="post" action="/drive/{{ d.id }}/assignments">"""
    + HIDDEN
    + """
<label>Project <select name="project">{% for p in projects %}<option value="{{ p.t6_id }}">{{ p.name }} — {{ p.project_code or 'unknown code' }} — {{ p.t6_id }}</option>{% endfor %}</select></label>
<label>Assignment label <input name="label"></label><label>Role <select name="role">{% for role in roles %}<option>{{ role }}</option>{% endfor %}</select></label><label>Start date <input name="start" type="date"></label><label>End date (optional) <input name="end" type="date"></label><label>Remarks <input name="remarks"></label><button>Add Assignment</button></form></section>"""
)
EXCEPTIONS = """<!doctype html><title>Import exceptions</title><h1>Import exceptions</h1><a href="/">Inventory</a>
<p>Rows the Drive import could not read, kept for review. The import runs with the <code>t6</code> command, not here.</p>
{% if exceptions %}<table><thead><tr><th>Workbook</th><th>Sheet</th><th>Row</th><th>Reason</th><th>Detail</th></tr></thead><tbody>{% for x in exceptions %}<tr><td>{{ x.workbook }}</td><td>{{ x.sheet }}</td><td>{{ x.row }}</td><td>{{ x.reason }}</td><td>{{ x.detail }}</td></tr>{% endfor %}</tbody></table>{% else %}<p>No import exceptions.</p>{% endif %}"""
OUTBOX = """<!doctype html><title>Outbox</title><h1>Outbox</h1><a href="/">Inventory</a>
<p>Changes the t6 API could not take yet. Each is sent again, with its key, until the API answers; it is recorded once.</p>
{% if entries %}<ul>{% for e in entries %}<li data-key="{{ e.key }}">{% if e.state == 'failed' %}Failed{% else %}Queued{% endif %}: {{ e.verb|replace('_', ' ') }} ({{ e.queued_at }}){% for name, value in e.inputs.items() %} {{ name }}={{ value }};{% endfor %}
{% if e.state == 'failed' %}<p role="alert">The t6 API refused it: {{ e.reason }}</p><form method="post" action="/outbox/{{ e.key }}/dismiss"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="key" value="{{ new_key() }}"><button>Dismiss</button></form>{% endif %}</li>{% endfor %}</ul>{% else %}<p>Nothing is waiting.</p>{% endif %}"""
UNAVAILABLE = '<!doctype html><title>Unavailable</title><h1>Unavailable</h1><p role="alert">The t6 API is unavailable. Nothing was lost; try again shortly.</p><a href="/">Inventory</a>'


def create_app(client: Client, outbox: str, *, retry_interval: float = 5.0) -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=65536
    )
    gateway = Gateway(client, Outbox(outbox), retry_interval)

    @app.errorhandler(Exception)
    def safe_error(error):
        if isinstance(error, HTTPException):
            return error
        if isinstance(error, ApiUnavailable):
            return UNAVAILABLE, 503
        if isinstance(error, ApiRefused):
            if error.rule in ("drive", "shipment"):
                return NotFound()
            return render_template_string(
                '<!doctype html><title>Refused</title><p role="alert">{{ reason }}</p><a href="/">Inventory</a>',
                reason=error.reason,
            ), 502
        app.logger.error("Unexpected inventory error")
        return "Unexpected inventory error.", 500

    @app.before_request
    def guard():
        # Exact loopback authorities prevent DNS rebinding; never trust forwarded headers.
        authority = request.host.split(":")
        if authority[0] not in ("localhost", "127.0.0.1") or (
            len(authority) > 1
            and (
                len(authority) != 2
                or not authority[1].isascii()
                or not authority[1].isdigit()
                or not 1 <= int(authority[1]) <= 65535
            )
        ):
            abort(400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin is not None and origin != request.host_url.rstrip("/"):
                abort(403)
            if not secrets.compare_digest(
                session.get("csrf", ""), request.form.get("csrf", "")
            ) or not session.get("csrf"):
                abort(403)

    @app.before_request
    def retry_queued_writes():
        if request.path != "/health":
            gateway.retry()

    @app.get("/health")
    def health():
        # Process/startup readiness only: it never calls the API.
        return "", 204

    @app.get("/")
    def index():
        q = request.args.get("q", "").strip()
        status = request.args.get("status", "")
        project = request.args.get("project", "")
        review = request.args.get("review", "")
        filters: dict[str, Any] = {
            "q": q or None,
            "serial": request.args.get("serial", "").strip() or None,
            "needs_review": {"yes": True, "no": False}.get(review),
            "project": project or None,
            "status": status or None,
        }
        drives = gateway.read("drive_list", DriveListInput(**filters))["drives"]
        return render_template_string(
            INDEX,
            drives=drives,
            q=q,
            status=status,
            project=project,
            projects=gateway.projects(),
            display=gateway.project_display,
        )

    def facts(form: Mapping[str, str]) -> dict[str, Any]:
        """The form as `drive add` inputs; what the form itself cannot judge is left to the API."""
        values: dict[str, Any] = {name: form.get(name, "").strip() or None for name in ADD_FIELDS}
        if values["capacity_tb"] is not None:
            try:
                values["capacity_tb"] = float(values["capacity_tb"])
            except ValueError:
                raise ValueError("Capacity must be a finite positive number.") from None
            if not math.isfinite(values["capacity_tb"]) or values["capacity_tb"] <= 0:
                raise ValueError("Capacity must be a finite positive number.")
        ownership = form.get("ownership", "unknown")
        if ownership not in ("unknown", "t6", "studio") or (
            bool(values["owner_studio_id"]) != (ownership == "studio")
        ):
            raise ValueError("Choose unknown, T6, or Studio with an existing Studio ID.")
        values["ownership_known"] = ownership != "unknown"
        values["needs_review"] = form.get("needs_review") == "on"
        values["label"] = (
            values["label"] or ""
        )  # a missing label is for the API to refuse, with its reason
        return {name: value for name, value in values.items() if value is not None}

    def csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        return session["csrf"]

    def form_page(drive_id: str | None, values: Mapping[str, Any], error: str | None = None) -> str:
        return render_template_string(
            FORM,
            title="Edit Drive" if drive_id else "New Drive",
            editing=drive_id is not None,
            values=values,
            fields=ADD_FIELDS,
            version=values.get("expected_version") or values.get("version"),
            labels=LABELS,
            error=error,
            csrf=csrf(),
            new_key=new_key,
        )

    def drive_record(drive_id: str) -> dict[str, Any]:
        return gateway.read("drive_get", DriveGetInput(drive=drive_id))["drive"]

    @app.route("/new", methods=["GET", "POST"])
    def new_drive():
        if request.method == "GET":
            return form_page(None, {})
        try:
            values = facts(request.form)
        except ValueError as error:
            return form_page(None, request.form, str(error)), 400
        outcome = gateway.submit("drive_add", values, posted_key(request.form))
        return finish(
            outcome,
            lambda done: "/drive/" + done.result["drive_ids"][0],
            lambda error: form_page(None, request.form, error),
        )

    @app.route("/drive/<drive_id>/edit", methods=["GET", "POST"])
    def edit(drive_id):
        if request.method == "GET":
            return form_page(drive_id, drive_record(drive_id))
        try:
            checked = facts(request.form)
        except ValueError as error:
            return form_page(drive_id, request.form, str(error)), 400
        # A blank clears a fact, so every field is sent; `facts` only judged the form.
        values: dict[str, Any] = {name: request.form.get(name, "").strip() for name in ADD_FIELDS}
        values["ownership_known"] = checked["ownership_known"]
        values["needs_review"] = checked["needs_review"]
        values["expected_version"] = request.form.get("expected_version", "").strip() or None
        outcome = gateway.submit(
            "drive_edit", {"drive": drive_id, **values}, posted_key(request.form)
        )

        def again(error: str) -> str:
            if isinstance(outcome, Refused) and outcome.rule == "drive_changed":
                # The form is the Drive as it is now, with its new version; what was typed is not kept.
                error = "The Drive changed since you opened this form. It is shown again as it is now; check it and save again."
                return form_page(drive_id, drive_record(drive_id), error)
            return form_page(drive_id, request.form, error)

        return finish(outcome, lambda done: "/drive/" + drive_id, again)

    @app.get("/drive/<drive_id>")
    def detail(drive_id):
        found = gateway.read("drive_get", DriveGetInput(drive=drive_id))
        drive = found["drive"]
        ownership = {"t6": "T6", "unknown": "Unknown ownership"}.get(
            drive["ownership"], drive["owner_studio_id"]
        )
        display = gateway.project_display
        return render_template_string(
            DETAIL,
            d=drive,
            assignments=found["assignments"],
            ownership=ownership,
            fields=ADD_FIELDS,
            labels=LABELS,
            csrf=csrf(),
            new_key=new_key,
            history=render_template_string(HISTORY, events=found["events"], display=display),
            projects=gateway.projects(),
            display=display,
            roles=DRIVE_ROLES,
        )

    @app.post("/drive/<drive_id>/assignments")
    def add_assignment(drive_id):
        values = {"drive": drive_id}
        for name in ("project", "label", "role"):
            values[name] = request.form.get(name, "").strip()
        for name in ("start", "end", "remarks"):
            if request.form.get(name, "").strip():
                values[name] = request.form[name].strip()
        outcome = gateway.submit(
            "drive_assign",
            values,
            posted_key(request.form),
        )
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id),
        )

    @app.post("/drive/<drive_id>/assignments/<int:number>/end")
    def end_assignment(drive_id, number):
        values = {"drive": drive_id, "assignment": number}
        if request.form.get("end", "").strip():
            values["end"] = request.form["end"].strip()
        outcome = gateway.submit("drive_unassign", values, posted_key(request.form))
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id),
        )

    @app.post("/drive/<drive_id>/promote")
    def promote(drive_id):
        outcome = gateway.submit(
            "drive_promote_serial", {"drive": drive_id}, posted_key(request.form)
        )
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id),
        )

    register_custody(app, gateway, csrf)
    register_shipments(app, gateway, csrf)

    @app.get("/exceptions")
    def exceptions():
        listed = gateway.read("drive_exceptions", DriveExceptionsInput())["exceptions"]
        return render_template_string(EXCEPTIONS, exceptions=listed)

    @app.get("/outbox")
    def show_outbox():
        return render_template_string(
            OUTBOX, entries=gateway.outbox.entries(), csrf=csrf(), new_key=new_key
        )

    @app.post("/outbox/<key>/dismiss")
    def dismiss(key):
        dismissed = gateway.outbox.dismiss(key)
        if dismissed is None:
            abort(404)
        if not dismissed:
            abort(400)
        return redirect("/outbox", code=303)

    return app


if __name__ == "__main__":
    import os

    logging.getLogger("werkzeug").disabled = True
    create_app(Client(), os.environ.get("DRIVES_OUTBOX", "outbox.json")).run(
        host="127.0.0.1", port=5000, debug=False
    )
