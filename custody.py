"""Append-only custody boundary; not a complete core DriveStore or authentication."""

import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date

from flask import Flask, abort, redirect, render_template_string, request
from t6.drives import CustodyEvent, CustodyKind, Party, PartyKind, Ref, derive_status


@dataclass(frozen=True)
class RecordedEvent:
    id: int
    event: CustodyEvent
    recipient: str | None
    actor_mode: str
    correction_of: int | None


def reference(form: Mapping[str, str], prefix: str) -> Ref:
    page = form.get(prefix + "_page_id", "").strip() or None
    text = form.get(prefix + "_text", "").strip() or None
    return Ref(page_id=page, text=text if page or text else "Unknown")


def restore(raw: str) -> CustodyEvent:
    data = json.loads(raw)
    data["kind"] = CustodyKind(data["kind"])
    data["on"] = date.fromisoformat(data["on"]) if data["on"] else None
    data["project"] = Ref(**data["project"])
    data["logged_by"] = Ref(**data["logged_by"])
    if data["party"]:
        data["party"] = Party(PartyKind(data["party"]["kind"]), Ref(**data["party"]["ref"]))
    return CustodyEvent(**data)


class Custody:
    """No update/delete operations. Host administrators can still alter SQLite."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self.connect = connect
        with connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS custody_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drive_id TEXT NOT NULL REFERENCES drives(id),
                event TEXT NOT NULL, recipient TEXT,
                actor_mode TEXT NOT NULL,
                correction_of INTEGER REFERENCES custody_events(id))""")

    def history(self, drive_id: str) -> tuple[RecordedEvent, ...]:
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM drives WHERE id=?", (drive_id,)).fetchone():
                raise LookupError
            rows = db.execute(
                "SELECT id,event,recipient,actor_mode,correction_of FROM custody_events WHERE drive_id=? ORDER BY id",
                (drive_id,),
            ).fetchall()
        return tuple(RecordedEvent(r[0], restore(r[1]), r[2], r[3], r[4]) for r in rows)

    def status(self, drive_id: str) -> str:
        status = derive_status([r.event for r in self.history(drive_id)])
        return status.value if status else "unknown"

    def append(self, drive_id: str, form: Mapping[str, str], actor_policy: str) -> None:
        actor = form.get("actor", "").strip() or None
        if actor and actor_policy != "self-declared-synthetic":
            raise ValueError
        party_id = form.get("party_page_id", "").strip() or None
        party_kind = form.get("party_kind", "").strip() or None
        if bool(party_id) != bool(party_kind):
            raise ValueError
        event = CustodyEvent(
            kind=CustodyKind(form.get("kind", "")),
            on=date.fromisoformat(form["on"]) if form.get("on", "").strip() else None,
            party=Party(PartyKind(party_kind), Ref(page_id=party_id)) if party_kind else None,
            project=reference(form, "project"),
            logged_by=Ref(text=actor or "Unknown actor"),
            reason=form.get("reason"),
        )
        data = asdict(event)
        data["kind"] = event.kind.value
        data["on"] = event.on.isoformat() if event.on else None
        if event.party:
            data["party"]["kind"] = event.party.kind.value
        correction = int(form["correction_of"]) if form.get("correction_of") else None
        with self.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM drives WHERE id=?", (drive_id,)).fetchone():
                raise LookupError
            if (
                correction is not None
                and not db.execute(
                    "SELECT 1 FROM custody_events WHERE id=? AND drive_id=?", (correction, drive_id)
                ).fetchone()
            ):
                raise ValueError
            db.execute(
                "INSERT INTO custody_events(drive_id,event,recipient,actor_mode,correction_of) VALUES(?,?,?,?,?)",
                (
                    drive_id,
                    json.dumps(data),
                    form.get("recipient", "").strip() or None,
                    "self-declared (not authenticated)" if actor else "unknown",
                    correction,
                ),
            )


HISTORY = """<section id="custody-history"><h2>Custody history</h2>
<p>Append order determines status; business dates may be unknown or backdated.
Corrections append evidence and never replace the original. App/storage-interface append-only is not tamper-proof against a host administrator.</p>
<ol>{% for row in history %}<li data-event-id="{{ row.id }}">Event {{ row.id }}: {{ row.event.kind.value }};
Business date: {{ row.event.on or 'Unknown date' }};
Recipient text: {{ row.recipient or 'Unknown recipient' }};
{% if row.event.party %}Existing {{ row.event.party.kind.value }} reference: {{ row.event.party.ref.page_id or row.event.party.ref.text }};{% endif %}
Project: {{ row.event.project.page_id or row.event.project.text }}{% if row.event.project.unresolved %} (unresolved){% endif %};
Recorded actor: {{ row.event.logged_by.text or row.event.logged_by.page_id }} ({{ row.actor_mode }});
Reason: {{ row.event.reason or 'Unknown reason' }};
{% if row.correction_of %}Correction of event {{ row.correction_of }};{% endif %}
<a href="/drive/{{ drive_id }}/custody?correction_of={{ row.id }}">Append correction for event {{ row.id }}</a></li>{% endfor %}</ol></section>"""

FORM = """<!doctype html><title>Record custody</title><h1>Record custody</h1>
<a href="/drive/{{ drive_id }}">Drive details</a>
<p>G2 unresolved: synthetic use only. Self-declared actors are not authenticated; operational deployment remains gated.</p>
<form method="post"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Kind <select name="kind" aria-label="Kind">{% for kind in kinds %}<option>{{ kind.value }}</option>{% endfor %}</select></label>
<label>Business date (blank = Unknown date) <input name="on" type="date"></label>
<label>Recipient free text <input name="recipient"></label>
<label>Existing party kind <select name="party_kind"><option value="">Unknown / free text only</option><option>contact</option><option>studio</option></select></label>
<label>Existing party page ID <input name="party_page_id"></label>
<label>Project unresolved text <input name="project_text"></label>
<label>Existing Project page ID <input name="project_page_id"></label>
{% if actor_policy == 'self-declared-synthetic' %}<label>Self-declared actor (not authenticated; blank = unknown) <input name="actor"></label>{% else %}<p>Recorded actor: Unknown actor (G2 unresolved).</p>{% endif %}
<label>Reason (required for retirement) <input name="reason"></label>
<label>Correction of event (optional) <input name="correction_of" value="{{ correction }}"></label>
<button>Append custody event</button></form>"""


def register_custody(
    app: Flask, custody: Custody, csrf: Callable[[], str], actor_policy: str
) -> None:
    if actor_policy not in ("unknown", "self-declared-synthetic"):
        raise ValueError("Unsupported synthetic actor policy; G2 remains unresolved.")

    @app.route("/drive/<drive_id>/custody", methods=["GET", "POST"])
    def record_custody(drive_id: str):
        try:
            custody.history(drive_id)
        except LookupError:
            abort(404)
        if request.method == "POST":
            try:
                custody.append(drive_id, request.form, actor_policy)
            except LookupError:
                abort(404)
            except (ValueError, TypeError):
                return (
                    "Invalid custody event. Retirement requires a reason; use valid dates, references and correction IDs. Reload the form to retry.",
                    400,
                )
            return redirect("/drive/" + drive_id, code=303)
        return render_template_string(
            FORM,
            drive_id=drive_id,
            csrf=csrf(),
            kinds=CustodyKind,
            actor_policy=actor_policy,
            correction=request.args.get("correction_of", ""),
        )
