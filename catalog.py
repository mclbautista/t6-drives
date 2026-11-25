"""What a Drive holds and where it sits: the QR label, the QR scan lookup and the dated catalog of its contents.

The records are the t6 API's. A scan is uploaded as a JSON manifest; the API keeps only what changed since the Drive's
last scan, and this screen reads those scans back, a scan's changes, or the whole tree as of a date.
"""

import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

import segno
from flask import Flask, abort, redirect, render_template, request
from t6.verbs import (
    DriveCatalogAtInput,
    DriveCatalogDiffInput,
    DriveCatalogSnapshotsInput,
    DriveFindInput,
    DriveGetInput,
)

from forms import finish, new_key, notice, posted_key
from gateway import ApiRefused, Gateway
from glossary import looks_like_id

# A scan of about 8,000 files is 1 to 2 MB of JSON. Every other form of this app stays at 64 KB.
MANIFEST_BYTES = 12_000_000
SHOWN = 1000  # rows of a tree shown at once; a folder prefix narrows it
MANILA = timezone(timedelta(hours=8), "Asia/Manila")
_BARE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CATALOG_POST = re.compile(r"/drive/[^/]+/catalog")
_DRIVE_ID = re.compile(r"t6d_[0-9a-f]{32}")


def allow_manifest(path: str) -> None:
    """Raise the body limit for the one route that takes a manifest; call it before the form is read."""
    if _CATALOG_POST.fullmatch(path):
        request.max_content_length = MANIFEST_BYTES


def manifest_entries(raw: bytes) -> list[dict[str, Any]]:
    """The entries of an uploaded manifest: a JSON list, or an object with an `entries` list."""
    try:
        document = json.loads(raw)
    except ValueError:
        raise ValueError("The manifest is not valid JSON.") from None
    entries = document.get("entries") if isinstance(document, dict) else document
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("The manifest must be a list of entries, each with a path and a kind.")
    return entries


def validate_on(raw: str) -> None:
    """Accept a bare YYYY-MM-DD date or an ISO 8601 timestamp with a UTC offset."""
    if _BARE_DATE.fullmatch(raw):
        date.fromisoformat(raw)
        return
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("missing UTC offset")


def normalize_captured_on(raw: str) -> str:
    """Normalize an uploaded scan time: bare YYYY-MM-DD stays a date; naive datetime gets Manila +08:00."""
    value = raw.strip()
    if not value:
        raise ValueError("Enter the scan date and time.")
    if _BARE_DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("Enter the scan date and time.") from None
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Enter the scan date and time.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=MANILA)
    return parsed.isoformat()


def format_scan_time(value: str) -> str:
    """Show a scan timestamp in Manila time as 'YYYY-MM-DD HH:MM' (with ':SS' when non-zero); bare dates stay 'YYYY-MM-DD'."""
    if _BARE_DATE.fullmatch(value):
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        parsed = parsed.astimezone(MANILA)
    return parsed.strftime("%Y-%m-%d %H:%M:%S" if parsed.second else "%Y-%m-%d %H:%M")


def register_catalog(app: Flask, gateway: Gateway, csrf: Callable[[], str]) -> None:
    @app.get("/drive/<drive_id>/qr")
    def qr_label(drive_id: str):
        drive = gateway.read("drive_get", DriveGetInput(drive=drive_id))["drive"]
        # A T6 Drive needs no setup: its label is made from its ID, which a scan already accepts.
        code = drive["qr_code"] or (drive["id"] if drive["ownership"] == "t6" else None)
        picture = None
        if code:
            link = request.host_url + "qr/" + code
            picture = segno.make(link, error="m").svg_inline(scale=6, border=4)
        return render_template(
            "qr.html",
            d=drive,
            picture=picture,
            show_code=bool(drive["qr_code"]) and not looks_like_id(drive["qr_code"]),
            csrf=csrf(),
            new_key=new_key,
        )

    @app.post("/drive/<drive_id>/qr")
    def add_qr(drive_id: str):
        """Give the Drive its QR code, equal to its ID; a Drive that already has a code keeps it."""
        back = f"/drive/{drive_id}/qr"
        drive = gateway.read("drive_get", DriveGetInput(drive=drive_id))["drive"]
        if drive["qr_code"]:
            return redirect(back, code=303)
        outcome = gateway.submit(
            "drive_edit",
            {"drive": drive_id, "qr_code": drive["id"], "expected_version": drive["version"]},
            posted_key(request.form),
        )
        return finish(outcome, lambda done: back, lambda error: notice(error, back))

    @app.get("/qr/<code>")
    def scan(code: str):
        """What a scanned label opens: the Drive holding the code (or, for a label made from the ID, that Drive)."""
        found = gateway.read("drive_find", DriveFindInput(qr_code=code))["matches"]
        if found:
            return redirect("/drive/" + found[0]["id"], code=303)
        if _DRIVE_ID.fullmatch(code):
            gateway.read("drive_get", DriveGetInput(drive=code))
            return redirect("/drive/" + code, code=303)
        abort(404)

    def catalog_page(drive_id: str, error: str | None = None) -> str:
        drive = gateway.read("drive_get", DriveGetInput(drive=drive_id))["drive"]
        snapshots = gateway.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput(drive=drive_id)
        )["snapshots"]
        on = request.args.get("on", "").strip()
        on_date = ""
        prefix = request.args.get("prefix", "").strip()
        changes = tree = snapshot = None
        notice = None
        diff_id = request.args.get("diff", "").strip()
        if diff_id:
            try:
                found = gateway.read(
                    "drive_catalog_diff", DriveCatalogDiffInput(drive=drive_id, snapshot=diff_id)
                )
            except ApiRefused:
                abort(404)
            snapshot, changes = found["snapshot"], found["changes"]
        elif on:
            try:
                validate_on(on)
            except ValueError:
                error = error or "Enter the date as YYYY-MM-DD or an ISO timestamp with UTC offset."
            else:
                on_date = on[:10]
                try:
                    found = gateway.read(
                        "drive_catalog_at",
                        DriveCatalogAtInput(drive=drive_id, on=on, prefix=prefix or None),
                    )
                    snapshot, tree = found["snapshot"], found["entries"]
                except ApiRefused as refused:
                    if refused.rule != "snapshot":
                        raise
                    notice = f"This Drive had not been scanned by {format_scan_time(on)}."
        return render_template(
            "catalog.html",
            d=drive,
            snapshots=list(reversed(snapshots)),
            on=on,
            on_date=on_date,
            prefix=prefix,
            snapshot=snapshot,
            changes=changes,
            tree=tree[:SHOWN] if tree is not None else None,
            tree_total=len(tree) if tree is not None else 0,
            notice=notice,
            error=error,
            now=datetime.now(MANILA).strftime("%Y-%m-%dT%H:%M"),
            format_scan_time=format_scan_time,
            csrf=csrf(),
            new_key=new_key,
        )

    @app.get("/drive/<drive_id>/catalog")
    def catalog(drive_id: str):
        page = catalog_page(drive_id)
        return page, 400 if "Enter the date as" in page else 200

    @app.post("/drive/<drive_id>/catalog")
    def add_catalog(drive_id: str):
        form, upload = request.form, request.files.get("manifest")
        if upload is None or not upload.filename:
            return catalog_page(drive_id, "Choose the manifest file of the scan."), 400
        try:
            entries = manifest_entries(upload.read())
            captured_on = normalize_captured_on(form.get("captured_on", ""))
        except ValueError as error:
            return catalog_page(drive_id, str(error)), 400
        values = {
            "drive": drive_id,
            "captured_on": captured_on,
            "root_path": form.get("root_path", "").strip() or "/",
            "entries": entries,
        }
        outcome = gateway.submit("drive_catalog_snapshot", values, posted_key(form))
        return finish(
            outcome,
            lambda done: f"/drive/{drive_id}/catalog",
            lambda error: catalog_page(drive_id, error),
        )
