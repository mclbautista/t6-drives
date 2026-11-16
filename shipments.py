"""Release a shipment, list shipments and show a shipment's manifest, all through the t6 API."""

from collections.abc import Callable

from flask import Flask, render_template_string, request
from t6.verbs import DriveListInput, DriveShipmentInput, DriveShipmentsInput

from forms import HIDDEN, finish, new_key, posted_key
from gateway import Gateway

FORM = (
    """<!doctype html><title>Release shipment</title><h1>Release shipment</h1>
<a href="/">Inventory</a> <a href="/shipments">Shipments</a>
<p>Events are recorded as the Person the t6 API authenticates.</p>{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<form method="post">"""
    + HIDDEN
    + """
<fieldset><legend>Select permanent Drive IDs for one handover</legend>
{% for d in drives %}<label><input type="checkbox" name="drive_id" value="{{ d.id }}" {% if d.id in chosen %}checked{% endif %}>{{ d.id }} — {{ d.label or d.description or 'Unknown label' }}</label><br>{% endfor %}</fieldset>
<label>Business date (blank = today) <input type="date" name="on" value="{{ form.get('on', '') }}"></label>
<label>Existing party kind <select name="party_kind"><option value="">Choose</option>{% for kind in ['contact', 'studio'] %}<option {% if form.get('party_kind') == kind %}selected{% endif %}>{{ kind }}</option>{% endfor %}</select></label>
<label>Party (page ID or name) <input name="party" value="{{ form.get('party', '') }}"></label>
<label>Project <select name="project">{% for p in projects %}<option value="{{ p.t6_id }}"{% if form.get('project') == p.t6_id %} selected{% endif %}>{{ p.name }} — {{ p.project_code or 'unknown code' }} — {{ p.t6_id }}</option>{% endfor %}</select></label>
<label>Recipient (who took the Drives, free text) <input name="recipient" value="{{ form.get('recipient', '') }}"></label>
<label>Reason <input name="reason" value="{{ form.get('reason', '') }}"></label>
<button>Release selected Drives</button></form>"""
)

MANIFEST = """<!doctype html><html><head><meta charset="utf-8"><title>Shipment {{ id }}</title>
<style>body{font-family:sans-serif}table{border-collapse:collapse;width:100%}th,td{border:1px solid;padding:.4em;text-align:left;overflow-wrap:anywhere}thead{display:table-header-group}tr{break-inside:avoid}@media print{.navigation{display:none}body{font-size:10pt}@page{margin:12mm}}</style></head><body>
<nav class="navigation"><a href="/">Inventory</a> <a href="/shipments">Shipments</a> <button type="button" onclick="window.print()">Print manifest</button></nav>
<main id="manifest"><h1>Shipment {{ id }}</h1>
<p>Recipient: {% if e.party %}{{ e.party.kind }}: {{ e.party.ref.page_id or e.party.ref.text }}{% else %}Unknown recipient{% endif %}</p>
{% if recipient %}<p>Taken by: {{ recipient }}</p>{% endif %}
<p>Business date: {{ e.on or 'Unknown date' }}</p>{% if e.reason %}
<p>Reason: {{ e.reason }}</p>{% endif %}
<p>Recorded by (the t6 API's authenticated Person): {{ e.logged_by.page_id or e.logged_by.text }}</p>
<p>Project: {{ display(e.project) }}</p>
<table><thead><tr><th>Permanent Drive ID</th><th>Accepted serial</th><th>Suspected serial (uncertain)</th><th>Label</th><th>Description</th><th>Capacity (TB)</th><th>Review state / note</th></tr></thead><tbody>
{% for r in releases %}{% set d = r.drive %}<tr data-drive-id="{{ d.id }}"><td>{{ d.id }}</td><td>{{ d.serial or 'Unknown accepted serial' }}</td><td>{{ d.suspected_serial or 'Unknown suspected serial' }}</td><td>{{ d.label or 'Unknown label' }}</td><td>{{ d.description or 'Unknown description' }}</td><td>{{ d.capacity_tb if d.capacity_tb is not none else 'Unknown capacity' }}</td><td>{{ 'Needs review' if d.needs_review else 'Not flagged' }}; {{ d.review_note or 'Unknown review note' }}</td></tr>{% endfor %}</tbody></table>
<p>{% if facts_at_release %}Drive facts are as they were at release, kept with the shipment{% else %}Drive facts were not kept for this shipment, which was released before the t6 API kept them; these are the facts as they stand now{% endif %}. The release events are the record of the handover. This is not open Project use.</p></main></body></html>"""

LIST = """<!doctype html><title>Shipments</title><h1>Shipments</h1><a href="/">Inventory</a> <a href="/shipments/new">Release shipment</a><ul>{% for s in shipments %}<li><a href="/shipments/{{ s.shipment_id }}">{{ s.shipment_id }}</a> — {% if s.party %}{{ s.party.ref.text or s.party.ref.page_id }}{% else %}Unknown recipient{% endif %} — {{ s.on or 'Unknown date' }} — {{ display(s.project) }} — {{ s.drive_count }} Drive{{ '' if s.drive_count == 1 else 's' }}</li>{% endfor %}</ul>"""


def register_shipments(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    def release_form(error: str | None = None):
        drives = gateway.read("drive_list", DriveListInput())["drives"]
        return render_template_string(
            FORM,
            drives=drives,
            chosen=request.form.getlist("drive_id"),
            form=request.form,
            projects=gateway.projects(),
            error=error,
            csrf=csrf(),
            new_key=new_key,
        )

    @app.route("/shipments/new", methods=["GET", "POST"])
    def release_shipment():
        if request.method == "GET":
            return release_form()
        form = request.form
        if not form.get("party_kind"):
            return release_form("Choose the party kind, contact or studio."), 400
        values = {
            "drives": list(dict.fromkeys(form.getlist("drive_id"))),
            "project": form.get("project", "").strip(),
            "party": form.get("party", "").strip(),
            "party_kind": form["party_kind"],
        }
        for name in ("on", "reason", "recipient"):
            if form.get(name, "").strip():
                values[name] = form[name].strip()
        outcome = gateway.submit("drive_release", values, posted_key(form))
        return finish(
            outcome,
            lambda done: "/shipments/" + done.result["shipment_id"],
            release_form,
            missing_is_404=False,
        )

    @app.get("/shipments")
    def shipment_list():
        listed = gateway.read("drive_shipments", DriveShipmentsInput())["shipments"]
        return render_template_string(LIST, shipments=listed, display=gateway.project_display)

    @app.get("/shipments/<key>")
    def shipment_detail(key: str):
        shipment = gateway.read("drive_shipment", DriveShipmentInput(shipment=key))
        return render_template_string(
            MANIFEST,
            id=shipment["shipment_id"],
            e=shipment["releases"][0]["event"],
            recipient=shipment["recipient"],
            facts_at_release=shipment["facts_at_release"],
            releases=shipment["releases"],
            display=gateway.project_display,
        )
