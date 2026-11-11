"""Host-only inventory import and private durable source reconciliation.

Capture evidence before core starts writing: even a failed importer leaves every
source row recoverable. A completed pass reconciles this journal with receipts
and core review reasons. The exception browser is read-only; administration
and raw-data diagnostics are never exposed as mutation endpoints.
"""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import click
from flask import Flask, make_response, render_template_string, request
from openpyxl import load_workbook
from t6.domain import Project
from t6.drives import DriveStore, Ref
from t6.drives.importer import import_log_sheet_workbook, import_per_project_workbook
from t6.lookup import Index, Lookup

from assignments import Assignments
from import_store import SQLiteDriveStore


def row_key(workbook: str, sheet: str, row: int) -> str:
    return hashlib.sha256(json.dumps([workbook, sheet, row]).encode()).hexdigest()


class CachedProjectReader:
    """Supply only cached Projects with an actual active page to core lookup.

    Ambiguous exact names remain multiple candidates, and page-less Projects
    remain unresolved. No registry writes, code guessing or live upstream I/O.
    """

    def __init__(self, database: str):
        self.database = database

    def lookup(self) -> Lookup:
        projects = tuple(
            Project(
                page_id=p["active_page_id"],
                name=p["name"],
                t6_id=p["t6_id"],
                project_code=p.get("code"),
            )
            for p in Assignments(self.database).cache()[0]
            if p["active_page_id"]
        )
        return Lookup(Index(projects=projects))


class InventoryImports:
    def __init__(self, database: str, *, store: SQLiteDriveStore | None = None):
        self.database = database
        self.store = store or SQLiteDriveStore(database)
        with self.store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS import_sources (
                    lineage TEXT PRIMARY KEY, workbook TEXT NOT NULL UNIQUE,
                    digest TEXT NOT NULL UNIQUE, source BLOB NOT NULL,
                    status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS import_evidence (
                    row_key TEXT PRIMARY KEY,
                    lineage TEXT NOT NULL REFERENCES import_sources(lineage),
                    workbook TEXT NOT NULL, sheet TEXT NOT NULL, row INTEGER NOT NULL,
                    evidence TEXT NOT NULL, status TEXT NOT NULL, reasons TEXT NOT NULL);
            """)

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if "kind" not in {r[1] for r in db.execute("PRAGMA table_info(import_sources)")}:
                db.execute(
                    "ALTER TABLE import_sources ADD COLUMN kind TEXT NOT NULL DEFAULT 'inventory'"
                )

    def evidence(self, lineage: str, *, exceptions_only: bool = True) -> list[dict[str, Any]]:
        """Private host boundary; callers must not send evidence to routine logs."""
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT row_key,workbook,sheet,row,evidence,status,reasons FROM import_evidence WHERE lineage=? ORDER BY sheet,row",
                (lineage,),
            ).fetchall()
        return [
            dict(zip(("row_key", "workbook", "sheet", "row", "evidence", "status", "reasons"), r))
            for r in rows
            if not exceptions_only or r[5] in ("pending", "exception")
        ]

    def status(self, lineage: str) -> str | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT status FROM import_sources WHERE lineage=?", (lineage,)
            ).fetchone()
        return row[0] if row else None

    def run(self, lineage: str, workbook: str, *, kind: str = "inventory") -> None:
        if kind not in ("inventory", "log-sheet"):
            raise ValueError("Unknown import kind")
        path = Path(workbook)
        if not path.is_absolute() or path.is_symlink() or not lineage.strip():
            raise ValueError("Use a stable absolute input and nonblank lineage.")
        path = path.resolve(strict=True)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        # Journal the original file itself before parsing; corrupt ZIP/XML is
        # therefore durable too. Paths/content never appear in CLI diagnostics.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            held = db.execute(
                "SELECT workbook,digest,kind FROM import_sources WHERE lineage=?", (lineage,)
            ).fetchone()
            if held and held != (str(path), digest, kind):
                raise ValueError("Input changed; explicit reconciliation required.")
            if (
                held
                and db.execute(
                    "SELECT status FROM import_sources WHERE lineage=?", (lineage,)
                ).fetchone()[0]
                == "complete"
            ):
                # A completed lineage is a receipt, not synchronization against
                # mutable facts/cache. Keep its original reconciliation stable.
                return
            if not held:
                if db.execute(
                    "SELECT 1 FROM import_sources WHERE workbook=? OR digest=?", (str(path), digest)
                ).fetchone():
                    raise ValueError("Input already registered; explicit reconciliation required.")
                db.execute(
                    "INSERT INTO import_sources VALUES(?,?,?,?,?,?)",
                    (lineage, str(path), digest, raw, "pending", kind),
                )
        try:
            source = load_workbook(BytesIO(raw), read_only=True, data_only=False)
            entries = []
            try:
                for sheet in source.worksheets:
                    for number, cells in enumerate(sheet.iter_rows(values_only=True), 1):
                        if any(value is not None and str(value).strip() for value in cells):
                            entries.append(
                                (
                                    row_key(str(path), sheet.title, number),
                                    lineage,
                                    str(path),
                                    sheet.title,
                                    number,
                                    json.dumps(cells, default=str, ensure_ascii=False),
                                    "pending",
                                    json.dumps(
                                        [{"reason": "Import not yet reconciled; source retained"}]
                                    ),
                                )
                            )
            finally:
                source.close()
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.executemany(
                    "INSERT OR IGNORE INTO import_evidence VALUES(?,?,?,?,?,?,?,?)", entries
                )
            # Complete evidence is durable before the first record/event write.
            self.store.checkpoint("evidence-written")
            # Core accepts paths only. Give it a private, per-run snapshot of the
            # exact journaled bytes, never reopen the mutable administrator input.
            # Translate core's temporary-path receipt keys back to the registered
            # original location; retries and concurrent runs keep permanent keys.
            with TemporaryDirectory(prefix="inventory-import-") as directory:
                snapshot = Path(directory).resolve() / "source.xlsx"
                snapshot.touch(mode=0o600)
                snapshot.write_bytes(raw)
                keys = {row_key(str(snapshot), entry[3], entry[4]): entry[0] for entry in entries}
                protocol: DriveStore = SQLiteDriveStore(
                    self.database,
                    checkpoint=self.store.checkpoint,
                    receipt_keys=keys,
                    source_lineage=lineage,
                )
                importer = (
                    import_log_sheet_workbook
                    if kind == "log-sheet"
                    else import_per_project_workbook
                )
                result = importer(
                    snapshot,
                    protocol,
                    lookup=CachedProjectReader(self.database).lookup(),
                    logged_by=Ref(text="Unknown imported actor"),
                )
            reasons: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for review in result.review:
                reasons[row_key(str(path), review.sheet, review.row)].append(
                    asdict(replace(review, workbook=str(path)))
                )
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for entry in entries:
                    key, _, _, _, _, evidence, _, _ = entry
                    receipt = db.execute(
                        "SELECT 1 FROM import_receipts WHERE row_key=?", (key,)
                    ).fetchone()
                    cells = json.loads(evidence)
                    first = str(cells[0] or "").strip() if cells else ""
                    # Mirror the pinned core per-project reader's grammar/search;
                    # it exposes no public structural-row recognizer.
                    structural = first.upper() == "HARD DRIVE LABEL" or bool(
                        re.search(r"PROJECT NAME\s*:\s*(.*)", first, re.IGNORECASE | re.DOTALL)
                    )
                    why = reasons.get(key, [])
                    observation = db.execute(
                        "SELECT reason FROM import_observations WHERE row_key=?", (key,)
                    ).fetchone()
                    # Evidence is append-only across reruns: a serial correction
                    # cannot erase an earlier conflicting observation.
                    previous = db.execute(
                        "SELECT status,reasons FROM import_evidence WHERE row_key=?", (key,)
                    ).fetchone()
                    if previous and previous[0] == "exception":
                        retained = json.loads(previous[1])
                        why = retained + [item for item in why if item not in retained]
                    if observation and not any(item["reason"] == observation[0] for item in why):
                        why.append({"reason": observation[0], "detail": "Source retained"})
                    if not receipt and not structural and not why:
                        why = [
                            {
                                "reason": "Unrecognized source row; not attached",
                                "detail": "Source retained",
                            }
                        ]
                    state = "exception" if why else ("usable" if receipt else "structure")
                    db.execute(
                        "UPDATE import_evidence SET status=?,reasons=? WHERE row_key=?",
                        (state, json.dumps(why, ensure_ascii=False), key),
                    )
                from imported_shipments import finalize_shipments

                finalize_shipments(db, lineage, self.store.assignments, self.store.checkpoint)
                db.execute(
                    "UPDATE import_sources SET status='complete' WHERE lineage=?", (lineage,)
                )
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE import_sources SET status='failed' WHERE lineage=?", (lineage,))
            raise


def register_imports(app: Flask, database: str) -> None:
    imports = InventoryImports(database)

    @app.get("/exceptions")
    def exception_list():
        # Same restricted office/host boundary as inventory. Not authentication:
        # no deployment may expose this data until the access gates are resolved.
        query = request.args.get("q", "").strip().casefold()
        with imports.store.connect() as db:
            rows = db.execute(
                "SELECT lineage,workbook,sheet,row,row_key,evidence,status,reasons FROM import_evidence WHERE status IN ('pending','exception') ORDER BY lineage,sheet,row"
            ).fetchall()
        entries = [
            dict(
                zip(
                    ("lineage", "workbook", "sheet", "row", "key", "evidence", "status", "reasons"),
                    row,
                )
            )
            for row in rows
            if not query or query in " ".join(map(str, row)).casefold()
        ]
        response = make_response(
            render_template_string(
                """<!doctype html><title>Import exceptions</title><h1>Import exceptions</h1><a href="/">Inventory</a><p>Private source evidence — read only. Access is not authenticated; operational deployment remains gated. Pending means a failed or incomplete run, not discarded evidence.</p><form><label>Search source, reason or evidence <input name="q" value="{{ q }}"></label><button>Find</button></form>{% for e in entries %}<article><h2>{{ e.lineage }} — {{ e.sheet }} — Row {{ e.row }}</h2><p>Workbook: {{ e.workbook }}</p><p>Source row key: {{ e.key }} — {{ e.status }}</p><pre>{{ e.reasons }}</pre><pre>{{ e.evidence }}</pre></article>{% endfor %}""",
                entries=entries,
                q=request.args.get("q", ""),
            )
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.cli.command("import-log-sheet")
    @click.option("--lineage", required=True)
    @click.option("--workbook", required=True)
    def import_log_sheet(lineage: str, workbook: str) -> None:
        """Host-only combined inventory/movement import. No HTTP upload."""
        try:
            InventoryImports(database).run(lineage, workbook, kind="log-sheet")
        except Exception:  # noqa: BLE001
            raise click.ClickException(
                "Import stopped. Inspect private evidence and retry unchanged inputs."
            ) from None
        click.echo("Log-sheet import complete; preservation still requires private reconciliation.")

    @app.cli.command("import-inventory")
    @click.option("--lineage", required=True)
    @click.option("--workbook", required=True)
    def import_inventory(lineage: str, workbook: str) -> None:
        """Host administrator only; no browser upload. Stable absolute inputs only."""
        try:
            InventoryImports(database).run(lineage, workbook)
        except Exception:  # noqa: BLE001 — sanitize arbitrary private parser/storage failures.
            raise click.ClickException(
                "Import stopped. Private source evidence retained when registered; inspect privately and retry unchanged input, or reconcile changed input explicitly."
            ) from None
        click.echo(
            "Inventory import complete; inspect usable inventory and private evidence before verification."
        )
