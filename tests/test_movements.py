"""Synthetic combined imports through the host CLI, real SQLite and HTTP."""

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from app import create_app
from import_store import SQLiteDriveStore
from imports import InventoryImports
from shipments import Shipments


def log_workbook(path):
    book = Workbook()
    inventory = book.active
    assert inventory is not None
    inventory.title = "Synthetic batch"
    inventory.append(["DATE: Jan 2, 2025"])
    inventory.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "SPACE", "T6 IN"])
    inventory.append(["Synthetic A", "SYN-A", "4TB", "YES"])
    inventory.append(["Synthetic B", "SYN-B", "8TB", "YES"])
    monthly = book.create_sheet("Synthetic month")
    monthly.append(["HARD DRIVE LABEL", "DATE", "IN/OUT", "RECEIVED BY", "REMARKS"])
    for _ in range(2):
        monthly.append(
            ["Source repeated label", "Jan 3, 2025", "OUT", "Synthetic person", "S/N: SYN-A"]
        )
    monthly.append(["Synthetic B", "Jan 3, 2025", "OUT", "Synthetic person", "S/N: SYN-B"])
    monthly.append(["Synthetic A", None, "IN", None, "S/N: SYN-A"])
    monthly.append(["<script>synthetic-secret</script>", None, "OUT", None, "missing marker"])
    monthly.append(["Synthetic prefix", None, "OUT", None, "S/N: SYN-A (old)"])
    book.save(path)
    book.close()


class MovementTests(unittest.TestCase):
    def test_log_cli_preserves_repeated_events_and_assembled_immutable_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            log_workbook(path)
            app = create_app(database)
            result = app.test_cli_runner().invoke(
                args=["import-log-sheet", "--lineage", "synthetic-log", "--workbook", str(path)]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            imports = InventoryImports(database)
            a = imports.store.find_by_serial("SYN-A")
            self.assertIsNotNone(a)
            assert a is not None
            self.assertEqual(
                [e.kind.value for e in imports.store.list_events(a.id)],
                ["received", "released", "released", "released", "received"],
            )
            client = app.test_client()
            shipments = Shipments(imports.store.connect, imports.store.assignments).all()
            self.assertEqual(sorted(len(s["lines"]) for s in shipments), [2, 3])
            repeated = next(s for s in shipments if len(s["lines"]) == 3)
            self.assertEqual([l["drive_id"] for l in repeated["lines"]].count(a.id), 2)
            self.assertEqual(repeated["lines"][0]["label"], "Source repeated label")
            rendered = {s["id"]: client.get("/shipments/" + s["id"]).text for s in shipments}
            for html in rendered.values():
                self.assertIn("Import-time reconstruction", html)
                self.assertIn("unavailable original facts", html)
            self.assertIn(
                "Unknown recipient",
                next(html for html in rendered.values() if "Synthetic person" not in html),
            )
            imports.store.set_serial(a.id, "SYN-CORRECTED")
            before = self.state(imports.store)
            for _ in range(2):
                restarted = create_app(database)
                result = restarted.test_cli_runner().invoke(
                    args=["import-log-sheet", "--lineage", "synthetic-log", "--workbook", str(path)]
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(self.state(SQLiteDriveStore(database)), before)
                for key, html in rendered.items():
                    self.assertEqual(restarted.test_client().get("/shipments/" + key).text, html)

    def test_private_exception_browser_is_searchable_escaped_read_only_and_restart_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            log_workbook(path)
            InventoryImports(database).run("synthetic-log", str(path), kind="log-sheet")
            client = create_app(database).test_client()
            page = client.get("/exceptions?q=synthetic-secret")
            self.assertEqual(page.status_code, 200)
            self.assertIn("&lt;script&gt;synthetic-secret&lt;/script&gt;", page.text)
            self.assertNotIn("<script>synthetic-secret</script>", page.text)
            self.assertIn("movement with no serial", page.text)
            self.assertIn("Synthetic month", page.text)
            self.assertIn(str(path), page.text)
            self.assertIn("Row 6", page.text)
            self.assertIn("Private source evidence", page.text)
            self.assertEqual(
                client.get("/exceptions?q=absent-synthetic-marker").text.count("<article>"), 0
            )
            self.assertEqual(client.post("/exceptions").status_code, 403)
            self.assertNotIn("synthetic-secret", client.get("/health").text)
            self.assertIn('href="/exceptions"', client.get("/").text)
            self.assertIn("Cache-Control", page.headers)

    def test_registered_source_cannot_be_reinterpreted_by_other_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            log_workbook(path)
            imports = InventoryImports(database)
            imports.run("synthetic-log", str(path), kind="log-sheet")
            with self.assertRaises(ValueError):
                imports.run("synthetic-log", str(path))

    def test_upgrade_retains_operational_snapshot_and_accepts_repeated_imported_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            log_workbook(path)
            app = create_app(database)
            imports = InventoryImports(database)
            store = Shipments(imports.store.connect, imports.store.assignments)
            with imports.store.connect() as db:
                db.execute("DROP TABLE shipment_members")
                db.execute(
                    "CREATE TABLE shipment_members(shipment_id TEXT NOT NULL REFERENCES shipments(id), position INTEGER NOT NULL, drive_id TEXT NOT NULL REFERENCES drives(id), event_id INTEGER NOT NULL UNIQUE REFERENCES custody_events(id), PRIMARY KEY(shipment_id,position), UNIQUE(shipment_id,drive_id))"
                )
            from t6.drives import Drive

            drive = imports.store.add_drive(Drive(label="Synthetic operational"))
            key = store.release([drive.id], {"project_text": "Synthetic project"}, "unknown")
            original = app.test_client().get("/shipments/" + key).text
            with imports.store.connect() as db:
                original_members = db.execute(
                    "SELECT * FROM shipment_members WHERE shipment_id=?", (key,)
                ).fetchall()
            result = app.test_cli_runner().invoke(
                args=["import-log-sheet", "--lineage", "synthetic-log", "--workbook", str(path)]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(
                create_app(database).test_client().get("/shipments/" + key).text, original
            )
            self.assertEqual(len(store.all()), 3)
            with imports.store.connect() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT * FROM shipment_members WHERE shipment_id=?", (key,)
                    ).fetchall(),
                    original_members,
                )
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_combined_failure_recovery_retains_every_row_and_no_partial_snapshot(self):
        from test_imports import synthetic_workbook

        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "synthetic-inventory.xlsx"
            log = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            synthetic_workbook(inventory, [["Original synthetic", "SYN-A", "YES"]])
            log_workbook(log)
            imports = InventoryImports(database)
            imports.run("synthetic-inventory", str(inventory))
            held = imports.store.find_by_serial("SYN-A")
            assert held is not None
            identity = held.id
            fired = False

            def fail(stage):
                nonlocal fired
                if stage == "imported-snapshot-written" and not fired:
                    fired = True
                    raise RuntimeError("synthetic private fault")

            imports = InventoryImports(database, store=SQLiteDriveStore(database, checkpoint=fail))
            with self.assertRaises(RuntimeError):
                imports.run("synthetic-log", str(log), kind="log-sheet")
            self.assertTrue(fired)
            self.assertEqual(imports.status("synthetic-log"), "failed")
            self.assertEqual(Shipments(imports.store.connect, imports.store.assignments).all(), [])
            with imports.store.connect() as db:
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM imported_event_sources WHERE lineage='synthetic-log'"
                    ).fetchone()[0],
                    8,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM import_evidence WHERE lineage='synthetic-log'"
                    ).fetchone()[0],
                    11,
                )
            imports.store.set_serial(identity, "SYN-CORRECTED")
            imports = InventoryImports(database)
            imports.run("synthetic-log", str(log), kind="log-sheet")
            self.assertEqual(len(imports.store.inventory.all()), 2)
            self.assertEqual(len(imports.store.list_events(identity)), 6)
            manifests = Shipments(imports.store.connect, imports.store.assignments).all()
            self.assertEqual(sorted(len(s["lines"]) for s in manifests), [2, 3])
            self.assertEqual(manifests[0]["lines"][0]["serial"], "SYN-A")
            before = self.state(imports.store)
            for lineage, path, kind in [
                ("synthetic-inventory", inventory, "inventory"),
                ("synthetic-log", log, "log-sheet"),
            ]:
                imports.run(lineage, str(path), kind=kind)
            self.assertEqual(self.state(imports.store), before)
            page = create_app(database).test_client().get("/drive/" + identity).text
            self.assertIn("Unknown date", page)
            self.assertIn("Original synthetic", page)

    @staticmethod
    def state(store):
        with store.connect() as db:
            return {
                table: db.execute("SELECT * FROM " + table + " ORDER BY rowid").fetchall()
                for table in [
                    "drives",
                    "assignments",
                    "custody_events",
                    "shipments",
                    "shipment_members",
                    "import_sources",
                    "imported_event_sources",
                    "import_observations",
                    "import_rejections",
                    "import_receipts",
                    "import_evidence",
                ]
            }
