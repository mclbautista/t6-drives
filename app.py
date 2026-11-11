"""Local synthetic inventory. Inventory is deliberately not a complete DriveStore."""

import json
import logging
import math
import secrets
import sqlite3
from dataclasses import asdict, replace
from datetime import date

from flask import Flask, abort, redirect, render_template_string, request, session
from t6.drives import Drive, FormFactor, normalize_serial
from werkzeug.exceptions import HTTPException

FIELDS = (
    "serial",
    "suspected_serial",
    "label",
    "description",
    "brand",
    "model",
    "capacity_tb",
    "format",
    "form_factor",
    "owner_studio_id",
    "purchase_date",
    "warranty",
    "remarks",
    "review_note",
)
LABELS = {
    "serial": "Accepted serial",
    "suspected_serial": "Suspected serial (uncertain)",
    "capacity_tb": "Capacity (TB)",
    "format": "Filesystem",
    "form_factor": "Form factor",
    "owner_studio_id": "Owner Studio ID (blank means T6)",
    "purchase_date": "Purchase date",
    "review_note": "Review note",
}


def encode(drive):
    data = asdict(drive)
    data["form_factor"] = drive.form_factor.value if drive.form_factor else None
    data["purchase_date"] = drive.purchase_date.isoformat() if drive.purchase_date else None
    return json.dumps(data)


def decode(raw):
    data = json.loads(raw)
    if data["form_factor"]:
        data["form_factor"] = FormFactor(data["form_factor"])
    if data["purchase_date"]:
        data["purchase_date"] = date.fromisoformat(data["purchase_date"])
    return Drive(**data)


def facts(form):
    values = {name: form.get(name, "").strip() or None for name in FIELDS}
    if values["capacity_tb"] is not None:
        try:
            values["capacity_tb"] = float(values["capacity_tb"])
        except ValueError:
            raise ValueError("Capacity must be a finite positive number.") from None
        if not math.isfinite(values["capacity_tb"]) or values["capacity_tb"] <= 0:
            raise ValueError("Capacity must be a finite positive number.")
    if values["purchase_date"]:
        try:
            values["purchase_date"] = date.fromisoformat(values["purchase_date"])
        except ValueError:
            raise ValueError("Purchase date must be a valid ISO date (YYYY-MM-DD).") from None
    if values["form_factor"]:
        try:
            values["form_factor"] = FormFactor(values["form_factor"])
        except ValueError:
            raise ValueError("Choose a valid form factor.") from None
    values["needs_review"] = form.get("needs_review") == "on"
    return values


class Inventory:
    """App-owned fact interface: replace immutable core Drive under a write lock.

    This only implements inventory; it is not the core DriveStore protocol.
    """

    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS drives (id TEXT PRIMARY KEY, serial TEXT UNIQUE, facts TEXT NOT NULL)"
            )

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, drive_id):
        with self.connect() as db:
            row = db.execute("SELECT facts FROM drives WHERE id=?", (drive_id,)).fetchone()
        return decode(row[0]) if row else None

    def all(self):
        with self.connect() as db:
            rows = db.execute("SELECT facts FROM drives ORDER BY rowid").fetchall()
        return [decode(row[0]) for row in rows]

    def exact(self, serial):
        with self.connect() as db:
            row = db.execute(
                "SELECT facts FROM drives WHERE serial=?", (normalize_serial(serial),)
            ).fetchone()
        return decode(row[0]) if row else None

    def save(self, values, drive_id=None, promote=False):
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if drive_id:
                    row = db.execute("SELECT facts FROM drives WHERE id=?", (drive_id,)).fetchone()
                    if not row:
                        raise LookupError
                    old = decode(row[0])
                    if promote:
                        if old.suspected_serial is None:
                            raise ValueError("No suspected serial to promote.")
                        values = {"serial": old.suspected_serial, "suspected_serial": None}
                    drive = replace(old, **values)
                    db.execute(
                        "UPDATE drives SET serial=?, facts=? WHERE id=?",
                        (drive.serial, encode(drive), drive.id),
                    )
                else:
                    drive = Drive(**values)
                    db.execute(
                        "INSERT INTO drives VALUES (?, ?, ?)",
                        (drive.id, drive.serial, encode(drive)),
                    )
            return drive
        except sqlite3.IntegrityError:
            raise ValueError("Accepted serial already belongs to another Drive.") from None


FORM = """<!doctype html><title>{{ title }}</title><h1>{{ title }}</h1>
<a href="/">Inventory</a>{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
{% for name in fields %}<p><label>{{ labels.get(name, name|capitalize) }}
{% if name == 'form_factor' %}<select name="form_factor"><option value="">Unknown</option>{% for choice in ['portable', 'powered desktop'] %}<option value="{{ choice }}" {% if values.get(name) == choice %}selected{% endif %}>{{ choice }}</option>{% endfor %}</select>
{% else %}<input name="{{ name }}" value="{{ values.get(name) or '' }}">{% endif %}</label></p>{% endfor %}
<p><label><input type="checkbox" name="needs_review" {% if values.get('needs_review') %}checked{% endif %}> Needs review</label></p><button>Save</button></form>"""


def create_app(database):
    app = Flask(__name__)
    app.secret_key = secrets.token_bytes(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", MAX_CONTENT_LENGTH=65536
    )
    inventory = Inventory(database)

    @app.errorhandler(Exception)
    def safe_error(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.error("Unexpected inventory error")
        return "Unexpected inventory error.", 500

    @app.before_request
    def guard():
        # Exact loopback authorities prevent DNS rebinding; never trust forwarded headers.
        if request.host not in ("localhost", "localhost:5000", "127.0.0.1", "127.0.0.1:5000"):
            abort(400)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin is not None and origin != request.host_url.rstrip("/"):
                abort(403)
            if not secrets.compare_digest(
                session.get("csrf", ""), request.form.get("csrf", "")
            ) or not session.get("csrf"):
                abort(403)

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        return session["csrf"]

    @app.get("/")
    def index():
        q = request.args.get("q", "").strip().casefold()
        serial = request.args.get("serial", "").strip()
        review = request.args.get("review", "")
        drives = [inventory.exact(serial)] if serial else inventory.all()
        drives = [
            d
            for d in drives
            if d
            and (
                not q
                or any(
                    q in (v or "").casefold()
                    for v in (d.id, d.serial, d.suspected_serial, d.label, d.description)
                )
            )
            and (review not in ("yes", "no") or d.needs_review == (review == "yes"))
        ]
        return render_template_string(
            """<!doctype html><title>Inventory</title><h1>Inventory</h1><a href="/new">New Drive</a><form><label>Search <input name="q" value="{{ q }}"></label><label>Exact accepted serial <input name="serial"></label><label>Review <select name="review"><option value="">All</option><option value="yes">Needs review</option><option value="no">Not flagged</option></select></label><button>Find</button></form><ul>{% for d in drives %}<li><a href="/drive/{{ d.id }}">{{ d.id }}</a> {{ d.label or '' }} {{ d.description or '' }} {% if d.serial %}Accepted: {{ d.serial }}{% endif %} {% if d.suspected_serial %}Suspected (uncertain): {{ d.suspected_serial }}{% endif %} {% if d.needs_review %}Needs review{% endif %}</li>{% endfor %}</ul>""",
            drives=drives,
            q=q,
        )

    @app.route("/new", methods=["GET", "POST"])
    @app.route("/drive/<drive_id>/edit", methods=["GET", "POST"])
    def edit(drive_id=None):
        drive = inventory.get(drive_id) if drive_id else None
        if drive_id and not drive:
            abort(404)
        values = json.loads(encode(drive)) if drive else {}
        error = None
        if request.method == "POST":
            values = request.form.to_dict()
            try:
                result = inventory.save(facts(request.form), drive_id)
                return redirect("/drive/" + result.id, code=303)
            except ValueError as exc:
                values = {}
                # Core exceptions can contain user data; return a fixed message.
                error = (
                    str(exc)
                    if str(exc)
                    in (
                        "Capacity must be a finite positive number.",
                        "Purchase date must be a valid ISO date (YYYY-MM-DD).",
                        "Choose a valid form factor.",
                        "Accepted serial already belongs to another Drive.",
                    )
                    else "Enter meaningful identifying detail and valid serial values."
                )
        return render_template_string(
            FORM,
            title="Edit Drive" if drive else "New Drive",
            values=values,
            fields=FIELDS,
            labels=LABELS,
            error=error,
            csrf=csrf(),
        ), (400 if error else 200)

    @app.get("/drive/<drive_id>")
    def detail(drive_id):
        drive = inventory.get(drive_id)
        if not drive:
            abort(404)
        values = json.loads(encode(drive))
        return render_template_string(
            """<!doctype html><title>Drive</title><h1>Drive {{ d.id }}</h1><a href="/">Inventory</a> <a href="/drive/{{ d.id }}/edit">Edit</a><dl>{% for name in fields %}{% if values.get(name) is not none %}<dt>{{ labels.get(name, name|capitalize) }}</dt><dd>{{ values[name] }}</dd>{% endif %}{% endfor %}<dt>Needs review</dt><dd>{{ 'Yes' if d.needs_review else 'No' }}</dd></dl>{% if d.suspected_serial %}<form method="post" action="/drive/{{ d.id }}/promote"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Promote suspected serial to accepted</button></form>{% endif %}""",
            d=drive,
            values=values,
            fields=FIELDS,
            labels=LABELS,
            csrf=csrf(),
        )

    @app.post("/drive/<drive_id>/promote")
    def promote(drive_id):
        try:
            inventory.save({}, drive_id, promote=True)
        except LookupError:
            abort(404)
        except ValueError:
            return "Cannot promote: no candidate or accepted serial already in use.", 400
        return redirect("/drive/" + drive_id, code=303)

    return app


if __name__ == "__main__":
    import os

    logging.getLogger("werkzeug").disabled = True
    create_app(os.environ.get("DRIVES_DB", "synthetic-inventory.sqlite")).run(
        host="127.0.0.1", port=5000, debug=False
    )
