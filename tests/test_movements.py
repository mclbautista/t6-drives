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
                    "import_receipts",
                    "import_evidence",
                ]
            }
