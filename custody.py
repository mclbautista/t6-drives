"""Custody screen: append receive, return and retire events through the t6 API. History comes with the Drive."""

from collections.abc import Callable

from flask import Flask, abort, render_template_string, request
from t6.verbs import DriveGetInput

from forms import HIDDEN, finish, new_key, notice, posted_key
from gateway import Gateway

VERBS = {"received": "drive_receive", "returned": "drive_return", "retired": "drive_retire"}

HISTORY = """<section id="custody-history"><h2>Custody history</h2>
<p>Append order determines status; business dates may be unknown or backdated. A correction is a later event.</p>
<ol>{% for e in events %}<li data-event-number="{{ loop.index }}">Event {{ loop.index }}: {{ e.kind }};
Business date: {{ e.on or 'Unknown date' }};
{% if e.party %}Party: {{ e.party.kind }}: {{ e.party.ref.page_id or e.party.ref.text }};{% endif %}
Project: {{ display(e.project) }};
Recorded by (the t6 API's authenticated Person): {{ e.logged_by.page_id or e.logged_by.text }};
Reason: {{ e.reason or 'Unknown reason' }}{% if e.shipment_id %}; <a href="/shipments/{{ e.shipment_id }}">Shipment {{ e.shipment_id }}</a>{% endif %}</li>{% endfor %}</ol></section>"""

FORM = (
    """<!doctype html><title>Record custody</title><h1>Record custody</h1>
<a href="/drive/{{ drive_id }}">Drive details</a>
<p>Events are recorded as the Person the t6 API authenticates. A release belongs to a shipment.</p>
<form method="post">"""
    + HIDDEN
    + """
<label>Kind <select name="kind" aria-label="Kind">{% for kind in kinds %}<option>{{ kind }}</option>{% endfor %}</select></label>
<label>Project <select name="project">{% for p in projects %}<option value="{{ p.t6_id }}">{{ p.name }} — {{ p.project_code or 'unknown code' }} — {{ p.t6_id }}</option>{% endfor %}</select></label>
<label>Business date (blank = today) <input name="on" type="date"></label>
<label>Existing party kind <select name="party_kind"><option value="">No party</option><option>contact</option><option>studio</option></select></label>
<label>Party (page ID or name) <input name="party"></label>
<label>Reason (required for retirement) <input name="reason"></label>
<button>Append custody event</button></form>"""
)


def register_custody(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    @app.route("/drive/<drive_id>/custody", methods=["GET", "POST"])
    def record_custody(drive_id: str):
        if request.method == "GET":
            gateway.read("drive_get", DriveGetInput(drive=drive_id))
            return render_template_string(
                FORM,
                drive_id=drive_id,
                kinds=VERBS,
                projects=gateway.projects(),
                csrf=csrf(),
                new_key=new_key,
            )
        form = request.form
        method = VERBS.get(form.get("kind", ""))
        if method is None:
            abort(400)
        values = {"drive": drive_id, "project": form.get("project", "").strip()}
        if method == "drive_retire":
            values["reason"] = form.get(
                "reason", ""
            ).strip()  # a missing reason is for the API to refuse
        for name in ("on",) if method == "drive_retire" else ("on", "party", "party_kind"):
            if form.get(name, "").strip():
                values[name] = form[name].strip()
        outcome = gateway.submit(method, values, posted_key(form))
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id),
        )
