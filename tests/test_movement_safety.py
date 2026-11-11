"""Unsafe movement associations and real concurrent import/serial writes."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import test_movements as movements
from openpyxl import load_workbook
from t6.drives import Drive
from test_imports import synthetic_workbook
from test_movements import log_workbook

from app import create_app
from import_store import SQLiteDriveStore
from imports import InventoryImports
from shipments import Shipments


class MovementSafetyTests(unittest.TestCase):
    def test_unsafe_movements_are_individual_exceptions_not_guessed_attachments(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "synthetic-inventory.xlsx"
            log = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            synthetic_workbook(
                inventory,
                [
                    ["Synthetic suspected", "SYN-C (old)"],
                    ["Synthetic shared one", "SYN-S"],
                    ["Synthetic shared two", "SYN-S"],
                    ["Synthetic missing", None],
                    ["Synthetic unreadable", "SYN-BAD", None, "not capacity"],
                ],
            )
            app = create_app(database)
            result = app.test_cli_runner().invoke(
                args=["import-inventory", "--lineage", "inventory", "--workbook", str(inventory)]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            store = SQLiteDriveStore(database)
            # Even an accepted candidate cannot override another Drive's suspicion.
            accepted = store.add_drive(Drive(serial="SYN-S", label="Synthetic accepted shared"))
            suspect = store.add_drive(
                Drive(suspected_serial="SYN-A", label="Synthetic uncertain A")
            )
            log_workbook(log)
            book = load_workbook(log)
            sheet = book["Synthetic month"]
            for remark in [
                "S/N: SYN-S",
                "S/N: SYN-C (old)",
                "S/N: SYN-C",
                "S/N: SYN-MISSING",
                "S/N: SYN-BAD",
                "S/N: SYN-A S/N: SYN-B",
            ]:
                sheet.append(["Synthetic A", None, "OUT", None, remark])
            sheet.append(["Synthetic unreadable movement", None, "INVALID", None, "S/N: SYN-B"])
            book.save(log)
            book.close()
            result = app.test_cli_runner().invoke(
                args=["import-log-sheet", "--lineage", "log", "--workbook", str(log)]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(store.list_events(accepted.id), ())
            self.assertEqual(store.list_events(suspect.id), ())
            a = store.find_by_serial("SYN-A")
            assert a is not None
            # All monthly A rows are unsafe; only the two inventory-derived events remain.
            self.assertEqual(len(store.list_events(a.id)), 2)
            evidence = InventoryImports(database).evidence("log")
            monthly = {e["row"]: e for e in evidence if e["sheet"] == "Synthetic month"}
            self.assertTrue({2, 3, 5, 6, 7, 8, 9, 13, 14}.issubset(monthly))
            for row in [8, 9, 13]:
                self.assertIn("serial", monthly[row]["reasons"])
                self.assertEqual(monthly[row]["status"], "exception")
            self.assertIn("unreadable", monthly[14]["reasons"])
            # Pinned core permits a fresh record from explicit clean S/N evidence.
            self.assertIsNotNone(store.find_by_serial("SYN-MISSING"))
            self.assertIsNotNone(store.find_by_serial("SYN-BAD"))
            self.assertEqual(len(store.inventory.all()), 10)
            before = movements.MovementTests.state(store)
            InventoryImports(database).run("log", str(log), kind="log-sheet")
            self.assertEqual(movements.MovementTests.state(store), before)
            page = create_app(database).test_client().get("/exceptions?q=SYN-S")
            self.assertIn("Synthetic month", page.text)
            self.assertIn(
                "Row 8", create_app(database).test_client().get("/exceptions?q=SYN-S").text
            )
            self.assertEqual(page.headers["Cache-Control"], "no-store")

    def test_log_import_and_serial_mutation_wait_for_row_then_rerun_without_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "synthetic-inventory.xlsx"
            log = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            synthetic_workbook(inventory, [["Synthetic original A", "SYN-A", "YES"]])
            log_workbook(log)
            imports = InventoryImports(database)
            imports.run("inventory", str(inventory))
            drive = imports.store.find_by_serial("SYN-A")
            assert drive is not None
            locked, release, editing = Event(), Event(), Event()

            def pause(stage):
                if stage == "before-row-commit" and not locked.is_set():
                    locked.set()
                    if not release.wait(10):
                        raise RuntimeError("Synthetic timeout")

            importer = InventoryImports(
                database, store=SQLiteDriveStore(database, checkpoint=pause)
            )
            writer = SQLiteDriveStore(database)
            parallel = InventoryImports(database)

            def edit():
                editing.set()
                writer.set_serial(drive.id, "SYN-CORRECTED")

            with ThreadPoolExecutor(3) as pool:
                run = pool.submit(importer.run, "log", str(log), kind="log-sheet")
                try:
                    self.assertTrue(locked.wait(10))
                    correction = pool.submit(edit)
                    self.assertTrue(editing.wait(10))
                    rerun = pool.submit(parallel.run, "log", str(log), kind="log-sheet")
                    self.assertFalse(correction.done())
                finally:
                    release.set()
                run.result(20)
                correction.result(20)
                rerun.result(20)
            corrected = writer.get_drive(drive.id)
            assert corrected is not None
            self.assertEqual(corrected.serial, "SYN-CORRECTED")
            before = movements.MovementTests.state(writer)
            restarted = InventoryImports(database)
            restarted.run("inventory", str(inventory))
            restarted.run("log", str(log), kind="log-sheet")
            self.assertEqual(movements.MovementTests.state(writer), before)
            self.assertEqual(restarted.status("log"), "complete")
            manifests = Shipments(writer.connect, writer.assignments).all()
            self.assertGreaterEqual(len(manifests), 1)
            self.assertTrue(
                any(line["serial"] == "SYN-A" for s in manifests for line in s["lines"])
            )
            self.assertIn(
                "SYN-CORRECTED", create_app(database).test_client().get("/drive/" + drive.id).text
            )
