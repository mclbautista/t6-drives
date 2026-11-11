"""App-owned Assignment mutations and read-only upstream Project snapshot cache.

Core DriveStore has add/history but no end mutation. This focused interface uses
core Assignment/Ref validation; it does not claim to implement DriveStore.
"""

import json
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from typing import Any, Protocol

import click
from flask import Flask, abort, redirect, render_template_string, request
from t6.drives import Assignment, Ref, Role
from t6.registry import normalize_page_id


class AlreadyEnded(Exception):
    """An explicit end is historical, not an editable date."""


class ProjectReader(Protocol):
    """One external boundary: authorized complete registry/descriptive export."""

    def read(self) -> dict[str, Any]: ...


class ExportReader:
    def __init__(self, path: str):
        self.path = path

    def read(self) -> dict[str, Any]:
        with open(self.path) as source:
            return json.load(source)


class Assignments:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS project_cache (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    snapshot TEXT NOT NULL, refreshed_at TEXT, refresh_error TEXT);
                INSERT OR IGNORE INTO project_cache VALUES(1, '[]', NULL, NULL);
                CREATE TABLE IF NOT EXISTS assignments (
                    id INTEGER PRIMARY KEY, drive_id TEXT NOT NULL REFERENCES drives(id),
                    value TEXT NOT NULL);
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def cache(self) -> tuple[list[dict[str, Any]], str | None, str | None]:
        with self.connect() as db:
            row = db.execute(
                "SELECT snapshot, refreshed_at, refresh_error FROM project_cache"
            ).fetchone()
        assert row is not None
        return json.loads(row[0]), row[1], row[2]

    def refresh(self, reader: ProjectReader) -> None:
        try:
            payload = reader.read()
            if payload.get("complete") is not True or not isinstance(payload.get("projects"), list):
                raise ValueError
            projects = payload["projects"]
            identities: set[str] = set()
            pages: dict[str, str] = {}
            for p in projects:
                if not re.fullmatch(r"t6p_[0-9a-f]{32}", p["t6_id"]) or p["t6_id"] in identities:
                    raise ValueError
                identities.add(p["t6_id"])
                if not isinstance(p["name"], str) or not p["name"].strip():
                    raise ValueError
                if p.get("code") is not None and not isinstance(p["code"], str):
                    raise ValueError
                if not isinstance(p["legacy_page_ids"], list):
                    raise TypeError
                # A registered Project without an active page is displayable, not selectable.
                active = p["active_page_id"]
                if active is not None:
                    p["active_page_id"] = normalize_page_id(active)
                p["legacy_page_ids"] = [normalize_page_id(x) for x in p["legacy_page_ids"]]
                for page in self.pages(p):
                    if not page or page in pages:
                        raise ValueError
                    pages[page] = p["t6_id"]
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = json.loads(db.execute("SELECT snapshot FROM project_cache").fetchone()[0])
                for p in old:
                    current = next((x for x in projects if x["t6_id"] == p["t6_id"]), None)
                    if current is None or any(
                        pages.get(page) != p["t6_id"] for page in self.pages(p)
                    ):
                        raise ValueError
                    # A replaced page cannot silently become active again.
                    if current["active_page_id"] in p["legacy_page_ids"]:
                        raise ValueError
                db.execute(
                    "UPDATE project_cache SET snapshot=?, refreshed_at=?, refresh_error=NULL",
                    (json.dumps(projects), datetime.now(UTC).isoformat()),
                )
        except Exception:  # noqa: BLE001 — sanitize arbitrary external-reader failures.
            with self.connect() as db:
                db.execute(
                    "UPDATE project_cache SET refresh_error=?",
                    ("Refresh failed; using last complete cache.",),
                )
            raise ValueError("Project refresh failed; last complete cache retained.") from None

    @staticmethod
    def pages(project: dict[str, Any]) -> list[str]:
        return ([project["active_page_id"]] if project["active_page_id"] else []) + project[
            "legacy_page_ids"
        ]

    def project(self, reference: str) -> dict[str, Any] | None:
        return next(
            (
                p
                for p in self.cache()[0]
                if reference == p["t6_id"] or normalize_page_id(reference) in self.pages(p)
            ),
            None,
        )

    def project_display(self, ref: Ref) -> str:
        if ref.text is not None:
            return "Unresolved: " + ref.text
        p = self.project(ref.page_id or "")
        return (
            f"{p['name']} (code: {p.get('code') or 'unknown'}; T6 Project ID: {p['t6_id']}; page reference: {ref.page_id})"
            if p
            else "Uncached page reference: " + (ref.page_id or "")
        )

    def history(self, drive_id: str) -> list[tuple[int, Assignment]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, value FROM assignments WHERE drive_id=? ORDER BY id", (drive_id,)
            ).fetchall()
        result = [(key, self.decode(raw)) for key, raw in rows]
        return sorted(result, key=lambda item: (item[1].start or date.min, item[0]))

    @staticmethod
    def encode(assignment: Assignment) -> str:
        data = asdict(assignment)
        data["role"] = assignment.role.value if assignment.role else None
        for name in ("start", "end"):
            data[name] = (
                getattr(assignment, name).isoformat() if getattr(assignment, name) else None
            )
        return json.dumps(data)

    def add(self, drive_id: str, values: dict[str, str]) -> None:
        page, text = values.get("project_page", "").strip(), values.get("project_text", "").strip()
        if page:
            project = self.project(page)
            if project is None or normalize_page_id(page) not in self.pages(project):
                raise ValueError
            page = normalize_page_id(page)
        assignment = Assignment(
            project=Ref(page_id=page or None, text=text or None),
            label=values.get("label"),
            role=Role(values["role"]) if values.get("role") else None,
            start=date.fromisoformat(values["start"]) if values.get("start") else None,
            end=date.fromisoformat(values["end"]) if values.get("end") else None,
            remarks=values.get("remarks"),
        )
        with self.connect() as db:
            db.execute(
                "INSERT INTO assignments(drive_id, value) VALUES (?, ?)",
                (drive_id, self.encode(assignment)),
            )

    @staticmethod
    def decode(raw: str) -> Assignment:
        data = json.loads(raw)
        data["project"] = Ref(**data["project"])
        data["role"] = Role(data["role"]) if data["role"] else None
        for field in ("start", "end"):
            data[field] = date.fromisoformat(data[field]) if data[field] else None
        return Assignment(**data)

    def end(self, drive_id: str, key: int, on: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT value FROM assignments WHERE id=? AND drive_id=?", (key, drive_id)
            ).fetchone()
            if row is None:
                raise LookupError
            assignment = self.decode(row[0])
            if not assignment.is_open:
                raise AlreadyEnded
            ended = replace(assignment, end=date.fromisoformat(on))
            db.execute("UPDATE assignments SET value=? WHERE id=?", (self.encode(ended), key))

    def matches(self, drive_id: str, reference: str, unresolved: str) -> bool:
        for _, assignment in self.history(drive_id):
            if not assignment.is_open:
                continue
            if unresolved and assignment.project.text == unresolved:
                return True
            if reference and assignment.project.page_id:
                project = self.project(assignment.project.page_id)
                if project and (
                    reference == project["t6_id"]
                    or normalize_page_id(reference) in self.pages(project)
                ):
                    return True
        return False

    def cache_html(self) -> str:
        projects, refreshed, error = self.cache()
        return render_template_string(
            """<section aria-label="Project cache"><p>Last complete cache: {{ refreshed or 'never initialized' }}</p>{% if error %}<p role="alert">{{ error }}</p>{% endif %}</section>
<label>Project (open use) <select name="project"><option value="">All Projects</option>{% for p in projects %}<option value="{{ p.t6_id }}" {% if selected == p.t6_id %}selected{% endif %}>{{ p.name }} — code: {{ p.code or 'unknown' }} — {{ p.t6_id }}</option>{% endfor %}</select></label><label>Unresolved Project text (exact, open use) <input name="project_text" value="{{ unresolved }}"></label>""",
            projects=projects,
            refreshed=refreshed,
            error=error,
            selected=request.args.get("project"),
            unresolved=request.args.get("project_text", ""),
        )

    def open_use(self, drive_id: str) -> list[str]:
        return [self.project_display(a.project) for _, a in self.history(drive_id) if a.is_open]

    def detail_html(self, drive_id: str, csrf: str) -> str:
        return render_template_string(
            DETAIL,
            drive_id=drive_id,
            history=self.history(drive_id),
            display=self.project_display,
            projects=self.cache()[0],
            roles=Role,
            csrf=csrf,
        )


DETAIL = """<section><h2>Project use / Assignment history</h2><p>Assignments record Project use, not physical custody.</p>
<ul>{% for key, a in history %}<li data-assignment-id="{{ key }}">{{ display(a.project) }} — Label: {{ a.label or 'Unknown' }} — Role: {{ a.role.value if a.role else 'Unknown' }} — Start: {{ a.start or 'Unknown' }} — End: {{ a.end or 'Open' }} — {{ a.remarks or '' }}
{% if a.is_open %}<form method="post" action="/drive/{{ drive_id }}/assignments/{{ key }}/end"><input type="hidden" name="csrf" value="{{ csrf }}"><label>End date <input name="end" type="date" required></label><button>End Assignment</button></form>{% endif %}</li>{% endfor %}</ul>
<h3>Add Assignment</h3><form method="post" action="/drive/{{ drive_id }}/assignments"><input type="hidden" name="csrf" value="{{ csrf }}">
<label>Cached Project <select name="project_page"><option value="">Unresolved text instead</option>{% for p in projects %}{% if p.active_page_id %}<option value="{{ p.active_page_id }}">{{ p.name }} — {{ p.code or 'unknown code' }} — {{ p.t6_id }}</option>{% endif %}{% endfor %}</select></label>
<label>Unresolved Project text <input name="project_text"></label><label>Assignment label <input name="label"></label><label>Role <select name="role"><option value="">Unknown</option>{% for role in roles %}<option>{{ role.value }}</option>{% endfor %}</select></label><label>Start date <input name="start" type="date"></label><label>End date (optional) <input name="end" type="date"></label><label>Assignment remarks <input name="remarks"></label><button>Add Assignment</button></form></section>"""


def install(app: Flask, database: str, reader: ProjectReader | None) -> Assignments:
    store = Assignments(database)

    @app.cli.command("refresh-projects")
    @click.option("--export", "export_path", type=click.Path(exists=True, dir_okay=False))
    def refresh_projects(export_path):
        """Host-admin only: bootstrap/refresh from an authorized complete export."""
        source = ExportReader(export_path) if export_path else reader
        if source is None:
            raise click.ClickException("Supply an authorized --export file.")
        try:
            store.refresh(source)
        except ValueError:
            raise click.ClickException(
                "Project refresh failed; last complete cache retained."
            ) from None
        click.echo("Complete Project cache saved.")

    @app.post("/drive/<drive_id>/assignments")
    def add_assignment(drive_id):
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM drives WHERE id=?", (drive_id,)).fetchone():
                abort(404)
        try:
            store.add(drive_id, request.form.to_dict())
        except (ValueError, sqlite3.IntegrityError):
            return (
                "Invalid Assignment: choose a cached page or unresolved text and valid role/dates.",
                400,
            )
        return redirect("/drive/" + drive_id, code=303)

    @app.post("/drive/<drive_id>/assignments/<int:key>/end")
    def end_assignment(drive_id, key):
        try:
            store.end(drive_id, key, request.form.get("end", ""))
        except LookupError:
            abort(404)
        except AlreadyEnded:
            return "Assignment already ended. Reload the detail.", 409
        except ValueError:
            return "Enter a valid end date, not before the known start date.", 400
        return redirect("/drive/" + drive_id, code=303)

    return store
