"""Atomic interactive handovers and saved display evidence; no import reconstruction.

This app-owned boundary uses core custody/reference validation and the core batch
contract. It is not a full DriveStore; the core manifest's current-facts query is
intentionally not used for historical output.
"""

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from flask import Flask, abort, redirect, render_template_string, request
from t6.drives import CustodyEvent, CustodyKind, Party, PartyKind, Ref

from assignments import Assignments
from custody import reference


class Shipments:
    def __init__(self, connect: Callable[[], sqlite3.Connection], assignments: Assignments):
        self.connect = connect
        self.assignments = assignments
        with connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shipments (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS shipment_members (
                    shipment_id TEXT NOT NULL REFERENCES shipments(id),
                    position INTEGER NOT NULL,
                    drive_id TEXT NOT NULL REFERENCES drives(id),
                    event_id INTEGER NOT NULL UNIQUE REFERENCES custody_events(id),
                    PRIMARY KEY(shipment_id, position));
                CREATE TRIGGER IF NOT EXISTS immutable_shipment_update
                    BEFORE UPDATE ON shipments BEGIN
                    SELECT RAISE(ABORT, 'Shipment snapshots are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_shipment_delete
                    BEFORE DELETE ON shipments BEGIN
                    SELECT RAISE(ABORT, 'Shipment snapshots are immutable'); END;
            """)

        # Upgrade the original interactive-only membership constraint. Repeated
        # imported events have distinct event IDs/positions, not distinct Drives.
        # Interactive selection still deduplicates before creating any events.
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            unique_drive = any(
                [r[2] for r in db.execute(f'PRAGMA index_info("{index[1]}")')]
                == ["shipment_id", "drive_id"]
                for index in db.execute("PRAGMA index_list(shipment_members)")
                if index[2]
            )
            if unique_drive:
                db.execute("""CREATE TABLE shipment_members_next (
                    shipment_id TEXT NOT NULL REFERENCES shipments(id),
                    position INTEGER NOT NULL,
                    drive_id TEXT NOT NULL REFERENCES drives(id),
                    event_id INTEGER NOT NULL UNIQUE REFERENCES custody_events(id),
                    PRIMARY KEY(shipment_id, position))""")
                db.execute("INSERT INTO shipment_members_next SELECT * FROM shipment_members")
                db.execute("DROP TABLE shipment_members")
                db.execute("ALTER TABLE shipment_members_next RENAME TO shipment_members")

    def all(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT snapshot FROM shipments ORDER BY rowid DESC")
            ]

    def get(self, key: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT snapshot FROM shipments WHERE id=?", (key,)).fetchone()
        if row is None:
            raise LookupError
        return json.loads(row[0])

    def release(self, ids: Sequence[str], form: Mapping[str, str], actor_policy: str) -> str:
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise ValueError
        actor = form.get("actor", "").strip() or None
        if actor and actor_policy != "self-declared-synthetic":
            raise ValueError
        party_id = form.get("party_page_id", "").strip() or None
        party_kind = form.get("party_kind", "").strip() or None
        if bool(party_id) != bool(party_kind):
            raise ValueError
        key = "t6s_" + uuid4().hex
        event = CustodyEvent(
            kind=CustodyKind.RELEASED,
            on=date.fromisoformat(form["on"]) if form.get("on", "").strip() else None,
            party=Party(PartyKind(party_kind), Ref(page_id=party_id)) if party_kind else None,
            project=reference(form, "project"),
            logged_by=Ref(text=actor or "Unknown actor"),
            reason=form.get("reason"),
            shipment_id=key,
        )
        data = asdict(event)
        data["kind"] = event.kind.value
        data["on"] = event.on.isoformat() if event.on else None
        if event.party:
            data["party"]["kind"] = event.party.kind.value
        recipient = form.get("recipient", "").strip() or None
        mode = "self-declared (not authenticated)" if actor else "unknown"
        with self.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            lines = []
            for drive_id in ids:
                row = db.execute("SELECT facts FROM drives WHERE id=?", (drive_id,)).fetchone()
                if row is None:
                    raise LookupError
                facts = json.loads(row[0])
                # Same label selection as core shipment_manifest, captured now, not at read time.
                assignments = [
                    a
                    for _, a in self.assignments.history(drive_id)
                    if (event.on is None or a.start is None or a.start <= event.on)
                    and (a.end is None or (event.on is not None and a.end >= event.on))
                ]
                lines.append(
                    {
                        "drive_id": drive_id,
                        **{
                            name: facts[name]
                            for name in (
                                "serial",
                                "suspected_serial",
                                "description",
                                "capacity_tb",
                                "needs_review",
                                "review_note",
                            )
                        },
                        "label": (assignments[-1].label or facts["label"])
                        if assignments
                        else facts["label"],
                    }
                )
            # BEGIN IMMEDIATE also serializes Project refresh/Assignment/fact writes:
            # these separate read-only connections see the same committed state.
            snapshot = {
                "id": key,
                "evidence": "Contemporaneous interactive snapshot",
                "recorded_at": datetime.now(UTC).isoformat(),
                "on": data["on"],
                "recipient": recipient,
                "party": data["party"],
                "project": data["project"],
                "project_display": self.assignments.project_display(event.project),
                "actor": data["logged_by"],
                "actor_mode": mode,
                "reason": event.reason,
                "lines": lines,
            }
            db.execute("INSERT INTO shipments VALUES(?,?)", (key, json.dumps(snapshot)))
            for position, drive_id in enumerate(ids):
                cursor = db.execute(
                    "INSERT INTO custody_events(drive_id,event,recipient,actor_mode,correction_of) VALUES(?,?,?,?,NULL)",
                    (drive_id, json.dumps(data), recipient, mode),
                )
                db.execute(
                    "INSERT INTO shipment_members VALUES(?,?,?,?)",
                    (key, position, drive_id, cursor.lastrowid),
                )
        return key


FORM = """<!doctype html><title>Release shipment</title><h1>Release shipment</h1>
<a href="/">Inventory</a> <a href="/shipments">Shipments</a>
<p>G2 unresolved: synthetic use only. Actors are not authenticated; operational deployment remains gated.</p>
{{ cache|safe }}
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<fieldset><legend>Select permanent Drive IDs for one handover</legend>
{% for d in drives %}<label><input type="checkbox" name="drive_id" value="{{ d.id }}">{{ d.id }} — {{ d.label or d.description or 'Unknown label' }}</label><br>{% endfor %}</fieldset>
<label>Business date (blank = Unknown date) <input type="date" name="on"></label>
<label>Recipient free text <input name="recipient"></label>
<label>Existing party kind <select name="party_kind"><option value="">Unknown / free text only</option><option>contact</option><option>studio</option></select></label>
<label>Existing party page ID <input name="party_page_id"></label>
<label>Cached Project <select name="project_page_id"><option value="">Unresolved text instead</option>{% for p in projects %}{% if p.active_page_id %}<option value="{{ p.active_page_id }}">{{ p.name }} — {{ p.t6_id }}</option>{% endif %}{% endfor %}</select></label>
<label>Project unresolved text <input name="project_text"></label>
{% if actor_policy == 'self-declared-synthetic' %}<label>Self-declared actor (not authenticated) <input name="actor"></label>{% else %}<p>Recorded actor: Unknown actor (G2 unresolved).</p>{% endif %}
<label>Reason <input name="reason"></label><button>Release selected Drives</button></form>"""

MANIFEST = """<!doctype html><html><head><meta charset="utf-8"><title>Shipment {{ s.id }}</title>
<style>body{font-family:sans-serif}table{border-collapse:collapse;width:100%}th,td{border:1px solid;padding:.4em;text-align:left;overflow-wrap:anywhere}thead{display:table-header-group}tr{break-inside:avoid}@media print{.navigation{display:none}body{font-size:10pt}@page{margin:12mm}}</style></head><body>
<nav class="navigation"><a href="/">Inventory</a> <a href="/shipments">Shipments</a> <button type="button" onclick="window.print()">Print manifest</button></nav>
<main id="manifest"><h1>Shipment {{ s.id }}</h1><p>{{ s.evidence }}</p>
<p>Recipient text: {{ s.recipient or 'Unknown recipient' }}</p>
<p>Recipient reference: {% if s.party %}{{ s.party.kind }}: {{ s.party.ref.page_id or s.party.ref.text }}{% else %}Unknown reference{% endif %}</p>
<p>Business date: {{ s.on or 'Unknown date' }}; Recorded at (UTC): {{ s.recorded_at }}</p>
<p>Recorded actor: {{ s.actor.text or s.actor.page_id }} ({{ s.actor_mode }})</p>
<p>Project: {{ s.project_display }}</p><p>Reason: {{ s.reason or 'Unknown reason' }}</p>
<table><thead><tr><th>Permanent Drive ID</th><th>Accepted serial</th><th>Suspected serial (uncertain)</th><th>Label</th><th>Description</th><th>Capacity (TB)</th><th>Review state / note</th></tr></thead><tbody>
{% for line in s.lines %}<tr data-drive-id="{{ line.drive_id }}"><td>{{ line.drive_id }}</td><td>{{ line.serial or 'Unknown accepted serial' }}</td><td>{{ line.suspected_serial or 'Unknown suspected serial' }}</td><td>{{ line.label or 'Unknown label' }}</td><td>{{ line.description or 'Unknown description' }}</td><td>{{ line.capacity_tb if line.capacity_tb is not none else 'Unknown capacity' }}</td><td>{{ 'Needs review' if line.needs_review else 'Not flagged' }}; {{ line.review_note or 'Unknown review note' }}</td></tr>{% endfor %}</tbody></table>
<p>This saved snapshot is not current custody or open Project use. Corrections append custody evidence; they do not rewrite this manifest. G2 unresolved: no authenticated attribution. App/storage immutability is not tamper-proof against a host administrator.</p></main></body></html>"""


def register_shipments(
    app: Flask, store: Shipments, csrf: Callable[[], str], actor_policy: str
) -> None:
    @app.route("/shipments/new", methods=["GET", "POST"])
    def release_shipment():
        if request.method == "POST":
            try:
                key = store.release(request.form.getlist("drive_id"), request.form, actor_policy)
            except LookupError:
                return "Unknown Drive. No Drives released. Reload the form.", 400
            except (ValueError, TypeError):
                return (
                    "Invalid shipment: select Drives and valid dates/references. No Drives released.",
                    400,
                )
            return redirect("/shipments/" + key, code=303)
        with store.connect() as db:
            drives = [
                json.loads(row[0]) for row in db.execute("SELECT facts FROM drives ORDER BY rowid")
            ]
        return render_template_string(
            FORM,
            drives=drives,
            csrf=csrf(),
            actor_policy=actor_policy,
            cache=store.assignments.cache_html(),
            projects=store.assignments.cache()[0],
        )

    @app.get("/shipments")
    def shipment_list():
        return render_template_string(
            """<!doctype html><title>Shipments</title><h1>Shipments</h1><a href="/">Inventory</a> <a href="/shipments/new">Release shipment</a><ul>{% for s in shipments %}<li><a href="/shipments/{{ s.id }}">{{ s.id }}</a> — {{ s.recipient or 'Unknown recipient' }} — {{ s.on or 'Unknown date' }} — {{ s.project_display }}</li>{% endfor %}</ul>""",
            shipments=store.all(),
        )

    @app.get("/shipments/<key>")
    def shipment_detail(key: str):
        try:
            snapshot = store.get(key)
        except LookupError:
            abort(404)
        return render_template_string(MANIFEST, s=snapshot)
