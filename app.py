"""The Drive web app: a client of the t6 API. It owns no records."""

import base64
import binascii
import logging
import math
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from flask import Flask, Response, abort, redirect, render_template, request, session
from t6.client import Client
from t6.verbs import (
    ContactFindInput,
    Done,
    DriveCaptureReadInput,
    DriveExceptionsInput,
    DriveGetInput,
    DriveInboxInput,
    DriveListInput,
    DriveOverdueInput,
    DriveProjectMovementsInput,
    DriveProjectsInput,
    DriveReloadReportInput,
    MovementListInput,
    PersonGetInput,
    Preview,
    Refused,
)
from t6.vocabulary import DRIVE_ROLES
from werkzeug.exceptions import HTTPException, NotFound

import glossary
from catalog import allow_manifest, register_catalog
from custody import register_custody
from forms import finish, new_key, notice, posted_key
from gateway import ApiRefused, ApiUnavailable, Gateway, Queued
from movement_photos import PhotoRefused, allow_photo, chosen_photo, register_photos, send_photo
from outbox import Outbox
from reload import register_reload
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
    "office_location",
)
LABELS = {
    "serial": "Serial",
    "suspected_serial": "Serial to check",
    "capacity_tb": "Capacity (TB)",
    "format": "Filesystem",
    "form_factor": "Form factor",
    "owner_studio_id": "Owner Studio (existing reference only)",
    "purchase_date": "Purchase date",
    "review_note": "Review note",
    "office_location": "Office location",
    "qr_code": "QR code",
}
PROJECT_SORTS = {
    "latest": "Latest movement",
    "az": "A to Z",
    "out": "Most drives out",
    "total": "Most drives in total",
}
PROJECT_KINDS = {"registered": "Registered", "name_only": "Name only, not linked to a Project yet"}
PLACES = ("TABLE", "CABINET", "SHELF", "BOX")
KEEP = "keep"  # a select choice that sends nothing for that fact
ID = re.compile(
    r"[A-Za-z0-9_-]{1,80}"
)  # what an ID in a path may look like; the API judges the rest
PHOTO_TYPES = ("image/png", "image/jpeg", "image/webp")
SHEET_ID = re.compile(r"[A-Za-z0-9_-]{20,}")


def sheet_url(workbook: str) -> str | None:
    """Where a Google Sheet opens, for a sheet problem; none for a workbook that is not a Google Sheet ID."""
    if SHEET_ID.fullmatch(workbook):
        return f"https://docs.google.com/spreadsheets/d/{workbook}/edit"
    return None


def todo_group(name: str, items: list[Any], one: str, many: str) -> dict[str, Any]:
    """One group of the Exceptions to-do list: its name, how many items, and the count in words."""
    return {"name": name, "count": len(items), "summary": one if len(items) == 1 else many}


def create_app(
    client: Client, outbox: str, *, retry_interval: float = 5.0, allowed_host: str = ""
) -> Flask:
    if allowed_host and (
        allowed_host == "0.0.0.0"
        or any(c in allowed_host for c in "/:*,")
        or allowed_host.split() != [allowed_host]
    ):
        raise RuntimeError(
            "DRIVES_ALLOWED_HOST must be one plain host name or IP address "
            "(no scheme, port, list, wildcard or 0.0.0.0)."
        )
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
            return render_template("unavailable.html"), 503
        if isinstance(error, ApiRefused):
            if error.rule in ("drive", "shipment"):
                return NotFound()
            return render_template("refused.html", reason=error.reason), 502
        app.logger.error("Unexpected inventory error")
        return "Unexpected inventory error.", 500

    @app.before_request
    def guard():
        # Exact loopback (plus one configured office host) prevents DNS rebinding;
        # never trust forwarded headers.
        authority = request.host.split(":")
        if (
            authority[0] not in ("localhost", "127.0.0.1")
            and not (allowed_host and authority[0].lower() == allowed_host.lower())
        ) or (
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
            allow_manifest(
                request.path
            )  # before the form is read: only the scan upload and a photo may be large
            allow_photo(request.path)
            origin = request.headers.get("Origin")
            if origin is not None and origin != request.host_url.rstrip("/"):
                abort(403)
            if not secrets.compare_digest(
                session.get("csrf", ""), request.form.get("csrf", "")
            ) or not session.get("csrf"):
                abort(403)

    @app.before_request
    def retry_queued_writes():
        gateway.practice.set(None)
        if request.path != "/health":
            gateway.retry()

    app.jinja_env.filters["plain"] = glossary.plain

    @app.context_processor
    def plain_words():
        return {"words": glossary}

    @app.context_processor
    def practice_banner():
        if gateway.practice.get() is None:
            try:
                gateway.read("drive_list", DriveListInput(limit=1))
            except (ApiUnavailable, ApiRefused):
                pass  # An unavailable API cannot supply the practice flag.
        return {"practice": gateway.practice.get()}

    @app.get("/health")
    def health():
        # Process/startup readiness only: it never calls the API.
        return "", 204

    PAGE_SIZE = 50

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
            "office_location": request.args.get("office_location", "").strip() or None,
        }
        filtered = any(value is not None for value in filters.values())
        raw_page = request.args.get("page", "").strip()
        page = (
            int(raw_page) if raw_page.isascii() and raw_page.isdigit() and int(raw_page) >= 1 else 1
        )
        start = (page - 1) * PAGE_SIZE
        listed = gateway.read(
            "drive_list", DriveListInput(**filters, limit=PAGE_SIZE, offset=start)
        )
        drives = listed["drives"]
        if "total" not in listed:
            # An API older than t6 0.6.22 ignores limit and offset and returns every Drive.
            total_drives = len(drives)
            total_pages = max(1, math.ceil(total_drives / PAGE_SIZE))
            page = min(page, total_pages)
            start = (page - 1) * PAGE_SIZE
            drives = drives[start : start + PAGE_SIZE]
        else:
            total_drives = listed["total"]
            total_pages = max(1, math.ceil(total_drives / PAGE_SIZE))
            if page > total_pages:
                page = total_pages
                start = (page - 1) * PAGE_SIZE
                drives = gateway.read(
                    "drive_list", DriveListInput(**filters, limit=PAGE_SIZE, offset=start)
                )["drives"]
        start_index = start + 1 if drives else 0
        end_index = start + len(drives)
        return render_template(
            "index.html",
            drives=drives,
            page=page,
            total_pages=total_pages,
            total_drives=total_drives,
            start_index=start_index,
            end_index=end_index,
            filtered=filtered,
            q=q,
            status=status,
            project=project,
            projects=gateway.projects(),
            display=gateway.project_display,
        )

    @app.get("/projects")
    def projects_list():
        q = request.args.get("q", "").strip()
        out = request.args.get("out") == "1"
        kind = request.args.get("kind", "")
        kind = kind if kind in PROJECT_KINDS else ""
        sort = request.args.get("sort", "")
        sort = sort if sort in PROJECT_SORTS else "latest"
        listed = gateway.read("drive_projects", DriveProjectsInput())["projects"]
        codes = {p["t6_id"]: p.get("project_code") or "" for p in gateway.projects()}
        rows = []
        for item in listed:
            ref = item["project"]
            registered = ref["page_id"] is not None
            name = gateway.project_display(ref)
            plain = ref["text"] if ref["text"] is not None else name
            code = codes.get(ref["page_id"], "")
            if q and q.casefold() not in f"{plain} {code}".casefold():
                continue
            if out and not item["released"]:
                continue
            if kind and (kind == "registered") != registered:
                continue
            rows.append(
                {**item, "key": ref["page_id"] or ref["text"], "name": name, "plain": plain}
            )

        def newest_first(row: Mapping[str, Any]) -> tuple[float, str]:
            """Newest movement first; a Project with none, or none that can be read, goes last."""
            try:
                when = datetime.fromisoformat(row.get("latest_movement") or "")
            except ValueError:
                return (math.inf, row["plain"].casefold())
            stamp = (when if when.tzinfo else when.replace(tzinfo=UTC)).timestamp()
            return (-stamp, row["plain"].casefold())

        ordering = {
            "latest": newest_first,
            "az": lambda r: (r["plain"].casefold(),),
            "out": lambda r: (-r["released"], r["plain"].casefold()),
            "total": lambda r: (-r["total"], r["plain"].casefold()),
        }
        rows.sort(key=ordering[sort])
        return render_template(
            "projects.html",
            projects=rows,
            q=q,
            out=out,
            kind=kind,
            sort=sort,
            sorts=PROJECT_SORTS,
            kinds=PROJECT_KINDS,
            filtered=bool(q or out or kind),
        )

    @app.get("/projects/view")
    def project_page():
        project = request.args.get("project", "").strip()
        if not project:
            abort(400)
        counts = gateway.read("drive_projects", DriveProjectsInput())["projects"]
        found = next(
            (p for p in counts if project in (p["project"]["page_id"], p["project"]["text"])),
            None,
        )
        if found is None:
            abort(404)
        drives = gateway.read("drive_list", DriveListInput(project=project))["drives"]
        movements = gateway.read(
            "drive_project_movements", DriveProjectMovementsInput(project=project)
        )["movements"]
        people: dict[str, str] = {}

        def person_name(person: str | None) -> str:
            if not person:
                return "Unknown"
            if not person.startswith("t6n_") and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", person):
                return person
            if person not in people:
                try:
                    people[person] = (
                        gateway.read("person_get", PersonGetInput(person_id=person))["person"][
                            "name"
                        ]
                        or "Unknown"
                    )
                except ApiRefused as error:
                    if error.rule not in {"person", "read"}:
                        raise
                    people[person] = "Unknown"
            return people[person]

        return render_template(
            "project.html",
            project=found["project"],
            drives=drives,
            movements=movements,
            display=gateway.project_display,
            project_history=True,
            person_name=person_name,
        )

    @app.get("/movements")
    def movements_page():
        filters: dict[str, Any] = {
            name: request.args.get(name, "").strip() or None
            for name in ("project", "drive", "person", "q", "start", "end")
        }
        listed = gateway.read("movement_list", MovementListInput(**filters))
        return render_template(
            "movements.html",
            movements=sorted(listed["movements"], key=lambda m: m["on"] or "", reverse=True),
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
        return render_template(
            "drive_form.html",
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

    def serial_page():
        return render_template("serial_form.html", csrf=csrf(), new_key=new_key)

    @app.route("/new", methods=["GET", "POST"])
    def new_drive():
        if request.method == "GET":
            return serial_page()
        serial = request.form.get("serial", "").strip()
        if serial:
            try:
                matches = gateway.read("drive_list", DriveListInput(serial=serial))["drives"]
            except ApiUnavailable:
                if "label" not in request.form:
                    raise
                # A completed form can still queue drive add; the API judges duplicates on retry.
            else:
                if matches:
                    return redirect("/drive/" + matches[0]["id"], code=303)
        if "label" not in request.form:
            return form_page(None, {"serial": serial})
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
        return render_template(
            "drive.html",
            d=drive,
            assignments=found["assignments"],
            ownership=ownership,
            fields=(*ADD_FIELDS, "qr_code"),
            labels=LABELS,
            csrf=csrf(),
            new_key=new_key,
            history=render_template("_history.html", events=found["events"], display=display),
            projects=gateway.projects(),
            display=display,
            roles=DRIVE_ROLES,
            places=PLACES,
        )

    @app.post("/drive/<drive_id>/place")
    def set_place(drive_id):
        """Place and T6 ownership: two facts changed through `drive edit`, nothing else on the Drive."""
        back = "/drive/" + drive_id
        values: dict[str, Any] = {"drive": drive_id}
        place = request.form.get("place", KEEP)
        if place in ("", *PLACES):
            values["office_location"] = place
        elif place != KEEP:
            return notice("Choose TABLE, CABINET, SHELF, BOX or no place.", back), 400
        if "ownership" in request.form:
            ownership = request.form["ownership"]
            if ownership not in ("t6", "unknown"):
                return notice("Choose T6 or unknown ownership.", back), 400
            values["ownership_known"] = ownership == "t6"
        values["expected_version"] = request.form.get("expected_version", "").strip() or None
        outcome = gateway.submit("drive_edit", values, posted_key(request.form))
        return finish(outcome, lambda done: back, lambda error: notice(error, back))

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

    @app.get("/drive/<drive_id>/recycle")
    def recycle_form(drive_id):
        drive = gateway.read("drive_get", DriveGetInput(drive=drive_id))["drive"]
        return render_template(
            "recycle.html",
            d=drive,
            projects=gateway.projects(),
            csrf=csrf(),
            new_key=new_key,
            preview=None,
        )

    def recycle_values(drive_id):
        return {
            "drive": drive_id,
            "project": request.form.get("project", "").strip(),
            "label": request.form.get("label", "").strip(),
        }

    @app.post("/drive/<drive_id>/recycle/preview")
    def recycle_preview(drive_id):
        values, key = recycle_values(drive_id), posted_key(request.form)
        outcome = gateway.preview("drive_recycle", values, key)
        if not isinstance(outcome, Preview):
            return finish(
                outcome,
                lambda done: "/drive/" + drive_id,
                lambda error: notice(error, "/drive/" + drive_id + "/recycle", "Back to Recycle"),
            )
        found = gateway.read("drive_get", DriveGetInput(drive=drive_id))
        drive, plan = found["drive"], outcome.plan
        ending = [gateway.project_display(a["project"]) for a in found["assignments"] if a["open"]]
        return render_template(
            "recycle.html",
            d=drive,
            preview={
                "old_label": plan.get("old_label"),
                "label": plan["label"],
                "project": gateway.project_display({"page_id": plan["project_id"], "text": None}),
                "ending": ending,
            },
            values=values,
            key=key,
            csrf=csrf(),
        )

    @app.post("/drive/<drive_id>/recycle")
    def recycle(drive_id):
        outcome = gateway.submit(
            "drive_recycle", recycle_values(drive_id), posted_key(request.form)
        )
        return finish(
            outcome,
            lambda done: "/drive/" + drive_id,
            lambda error: notice(error, "/drive/" + drive_id + "/recycle", "Back to Recycle"),
        )

    # People: Contacts found by name or handle, and a new Contact added after a preview and a confirm.
    BACK = re.compile(r"/(?:[^/\\\s][^\\\s]*)?")  # a page of this app, never another site

    def way_back() -> str:
        back = request.values.get("back", "")
        return back if BACK.fullmatch(back) else "/"

    @app.get("/people/find")
    def people_find():
        found = gateway.read(
            "contact_find", ContactFindInput(q=request.args.get("q", "").strip()[:80], limit=20)
        )
        return {
            "people": [
                {"name": c["name"], "handle": c.get("telegram_handle")} for c in found["contacts"]
            ]
        }

    @app.get("/people/new")
    def person_new():
        return render_template(
            "person_new.html",
            back=way_back(),
            csrf=csrf(),
            new_key=new_key,
            preview=None,
        )

    def person_values():
        values = {"name": request.form.get("name", "").strip()}
        for name in ("telegram_handle", "phone"):
            if request.form.get(name, "").strip():
                values[name] = request.form[name].strip()
        return values

    @app.post("/people/new/preview")
    def person_preview():
        values, key = person_values(), posted_key(request.form)
        outcome = gateway.preview("contact_create", values, key)
        if not isinstance(outcome, Preview):
            return finish(
                outcome,
                lambda done: "/",
                lambda error: notice(
                    error, "/people/new?" + urlencode({"back": way_back()}), "Back"
                ),
                missing_is_404=False,
            )
        return render_template(
            "person_new.html",
            back=way_back(),
            csrf=csrf(),
            key=key,
            values=values,
            preview=outcome.plan,
        )

    @app.post("/people/new")
    def person_add():
        outcome = gateway.submit("contact_create", person_values(), posted_key(request.form))
        back = way_back()

        def done(result: Any) -> str:
            name = result.result["contact"]["name"]
            return back + ("&" if "?" in back else "?") + urlencode({"person": name})

        return finish(
            outcome,
            done,
            lambda error: notice(error, "/people/new?" + urlencode({"back": back}), "Back"),
            missing_is_404=False,
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
    register_catalog(app, gateway, csrf)
    register_shipments(app, gateway, csrf)
    register_reload(app, gateway, csrf)

    def movement_log() -> list[dict[str, Any]]:
        return list(gateway.read("movement_list", MovementListInput())["movements"])

    def correction_page(
        movement: Mapping[str, Any], values: Mapping[str, str], error: str | None = None
    ) -> str:
        return render_template(
            "correction.html",
            m=movement,
            values=values,
            error=error,
            projects=gateway.projects(),
            places=PLACES,
            keep=KEEP,
            display=gateway.project_display,
            csrf=csrf(),
            new_key=new_key,
        )

    def original_of(movement_id: str) -> dict[str, Any]:
        if not ID.fullmatch(movement_id):
            abort(404)
        found = next((m for m in movement_log() if m["id"] == movement_id), None)
        if found is None:
            abort(404)
        return found

    @app.route("/movements/<movement_id>/correct", methods=["GET", "POST"])
    def correct_movement(movement_id):
        """A correction is a new entry; the original stays. Only what differs from the original is sent."""
        original = original_of(movement_id)
        form = request.form
        if request.method == "GET":
            return correction_page(
                original,
                {
                    "drive": original["drive"] or "",
                    "on": original["on"] or "",
                    "direction": original["direction"],
                    "person": request.args.get("person", "").strip() or original["person"] or "",
                },
            )
        try:
            photo = chosen_photo(request.files)
        except PhotoRefused as refused:
            return correction_page(original, form, str(refused)), 400
        key = posted_key(form)
        values: dict[str, Any] = {"movement": movement_id}
        for name in ("drive", "on", "person"):
            typed = form.get(name, "").strip()
            if typed and typed != (original[name] or ""):
                values[name] = typed
        if form.get("direction") in ("IN", "OUT") and form["direction"] != original["direction"]:
            values["direction"] = form["direction"]
        place = form.get("place", KEEP)
        if place == "none" and original["place"] is not None:
            values["place"] = None
        elif place in PLACES and place != original["place"]:
            values["place"] = place
        project = form.get("project", KEEP).strip()
        if project not in ("", KEEP):
            values["project"] = project
        if photo is not None and len(values) == 1:
            # Only a photo: it goes on this entry, and nothing is corrected.
            sent = send_photo(gateway, movement_id, photo, key + "-photo")
            if isinstance(sent, str):
                return correction_page(original, form, sent), 400
            return redirect("/movements/" + movement_id, code=303)
        outcome = gateway.submit("movement_correct", values, key)
        if photo is not None and isinstance(outcome, Queued):
            return notice(
                "The correction waits to be sent, and the photo was not kept. "
                "Add the photo on the movement once the correction is in the log.",
                "/movements",
                "Movement log",
            ), 202
        if photo is not None and isinstance(outcome, Done):
            corrected = str(outcome.result["movement"]["id"])
            try:
                sent = send_photo(gateway, corrected, photo, key + "-photo")
            except ApiUnavailable:
                sent = "The t6 API could not be reached."
            if isinstance(sent, str):
                return notice(
                    "The correction was saved, but the photo was not: " + sent,
                    "/movements/" + corrected,
                    "Add the photo again",
                ), 502

        def again(error: str) -> str:
            return correction_page(original, form, error)

        return finish(
            outcome,
            lambda done: "/movements?drive=" + (done.result["movement"]["drive"] or ""),
            again,
            missing_is_404=False,
        )

    register_photos(app, gateway, csrf, original_of, gateway.project_display)

    @app.post("/movements/<movement_id>/project")
    def set_movement_project(movement_id):
        """The needs-a-Project list: the Project is a correction that changes nothing else."""
        if not ID.fullmatch(movement_id):
            abort(404)
        values = {"movement": movement_id, "project": request.form.get("project", "").strip()}
        outcome = gateway.submit("movement_correct", values, posted_key(request.form))
        return finish(
            outcome,
            lambda done: "/exceptions",
            lambda error: notice(error, "/exceptions", "Exceptions"),
            missing_is_404=False,
        )

    @app.get("/captures/<capture_id>/photo")
    def capture_photo(capture_id):
        """The photo of a capture, read from the API for this one request: never stored or logged here."""
        if not ID.fullmatch(capture_id):
            abort(404)
        try:
            read = gateway.read("drive_capture_photo", DriveCaptureReadInput(capture=capture_id))
            data = base64.b64decode(read["photo"], validate=True)
        except ApiRefused as error:
            if error.rule == "capture":
                abort(404)
            raise
        except (binascii.Error, KeyError, TypeError):
            raise ApiUnavailable from None
        if read.get("media_type") not in PHOTO_TYPES:
            raise ApiUnavailable
        return Response(
            data,
            mimetype=read["media_type"],
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.get("/inbox")
    def inbox():
        items = gateway.read("drive_inbox", DriveInboxInput())["items"]
        drives: dict[str, dict[str, Any]] = {}
        for item in items:
            chat = str(item["chat_id"])
            topic = f"{item['topic_id']}/" if item["topic_id"] is not None else ""
            item["post_url"] = (
                f"https://t.me/c/{chat[4:]}/{topic}{item['message_id']}"
                if chat.startswith("-100")
                else None
            )
            for guess in item["guesses"]:
                drive_id = guess["drive"]
                if drive_id not in drives:
                    drives[drive_id] = gateway.read("drive_get", DriveGetInput(drive=drive_id))[
                        "drive"
                    ]
                guess["label"] = drives[drive_id]["label"] or "Drive without a label"
                guess["serial"] = drives[drive_id]["serial"]
        return render_template(
            "inbox.html",
            items=items,
            flash=session.pop("flash", None),
            csrf=csrf(),
            new_key=new_key,
        )

    @app.post("/inbox/<capture_id>/<int:item>/approve")
    def approve_item(capture_id, item):
        if not ID.fullmatch(capture_id):
            abort(404)
        values = {
            "capture": capture_id,
            "item": item,
            "choice": request.form.get("choice", ""),
            "drive": request.form.get("drive", "").strip() or None,
            "typed": request.form.get("typed", "").strip() or None,
        }
        outcome = gateway.submit("drive_approve", values, posted_key(request.form))

        def done(result: Done) -> str:
            session["flash"] = {
                "approved": "Approved. The item is no longer waiting.",
                "not_a_drive": "Marked as not a drive.",
                "open": "Left waiting. You can choose later.",
                "closed": "This item is closed.",
            }[result.result["state"]]
            return "/inbox"

        return finish(
            outcome,
            done,
            lambda error: notice(error, "/inbox", "Inbox"),
            missing_is_404=False,
        )

    @app.get("/exceptions")
    def exceptions():
        reads = gateway.read
        needs_project = reads("movement_list", MovementListInput(needs_project=True))["movements"]
        rows = reads("drive_exceptions", DriveExceptionsInput())["exceptions"]
        report = reads("drive_reload_report", DriveReloadReportInput())["report"]
        overdue = reads("drive_overdue", DriveOverdueInput())["overdue"]
        needs_project = sorted(needs_project, key=lambda m: m["on"] or "", reverse=True)
        rows = [
            {**x, "reason": glossary.plain(x["reason"]), "url": sheet_url(x["workbook"])}
            for x in rows
        ]
        groups = [
            todo_group(
                "projects",
                needs_project,
                "movement without a Project",
                "movements without a Project",
            ),
            todo_group("rows", rows, "row to fix in the sheet", "rows to fix in the sheet"),
            todo_group("overdue", overdue, "overdue T6 drive", "overdue T6 drives"),
        ]
        return render_template(
            "exceptions.html",
            groups=groups,
            todo_total=sum(g["count"] for g in groups),
            needs_project=needs_project,
            rows=rows,
            report=report,
            overdue=overdue,
            flash=session.pop("flash", None),
            projects=gateway.projects(),
            display=gateway.project_display,
            csrf=csrf(),
            new_key=new_key,
        )

    @app.get("/outbox")
    def show_outbox():
        return render_template(
            "outbox.html",
            entries=[glossary.waiting_entry(e) for e in gateway.outbox.entries()],
            csrf=csrf(),
            new_key=new_key,
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
