"""Deterministic scheduling of real pinned-core imports and HTTP fact edits."""

import json
import re
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import test_movements
from openpyxl import Workbook
from t6.drives import Drive
from test_assignments import Reader, export

from app import create_app
from import_store import SQLiteDriveStore
from imports import InventoryImports
from shipments import Shipments


class ImportReviewRegressions(unittest.TestCase):
    def test_log_structure_uses_log_grammar_without_hiding_unconsumed_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "synthetic-structure.xlsx"
            database = str(Path(d) / "synthetic.sqlite")
            book = Workbook()
            sheet = book.active
            assert sheet is not None
            sheet.title = "Synthetic inventory"
            sheet.append(["PROJECT NAME: Synthetic unattached", "SYN-ORPHAN"])
            sheet.append([None, "DATE: Jan 2, 2025"])
            sheet.append([None, "HARD DRIVE LABEL", "SERIAL #", "QUANTITY"])
            sheet.append([None, "Synthetic group", None, 2])
            sheet.append([None, "Synthetic accepted", "SYN-STRUCTURE", 1])
            book.save(path)
            book.close()
            app = create_app(database, project_reader=Reader(export(name="Synthetic group")))
            refresh = app.test_cli_runner().invoke(args=["refresh-projects"])
            self.assertEqual(refresh.exit_code, 0, refresh.output)
            imports = InventoryImports(database)
            imports.run("synthetic-structure", str(path), kind="log-sheet")
            rows = {
                e["row"]: e for e in imports.evidence("synthetic-structure", exceptions_only=False)
            }
            self.assertEqual(rows[1]["status"], "exception")
            self.assertIn("Unrecognized source row; not attached", rows[1]["reasons"])
            for number in (2, 3, 4):
                self.assertEqual(rows[number]["status"], "structure", rows[number])
                self.assertEqual(json.loads(rows[number]["reasons"]), [])
            self.assertIsNotNone(imports.store.find_by_serial("SYN-STRUCTURE"))
            state = test_movements.MovementTests.state(imports.store)
            for _ in range(2):
                imports = InventoryImports(database)
                imports.run("synthetic-structure", str(path), kind="log-sheet")
                self.assertEqual(test_movements.MovementTests.state(imports.store), state)
                page = create_app(database).test_client().get("/exceptions?q=SYN-ORPHAN")
                self.assertEqual(page.text.count("<article>"), 1)
                self.assertIn("Row 1", page.text)
                self.assertIn(rows[1]["row_key"], page.text)
                self.assertIn("Unrecognized source row; not attached", page.text)

    def test_http_correction_before_movement_write_is_an_exception(self):
        self.serial_race(correction_first=True)

    def test_http_correction_after_movement_write_preserves_original_snapshot(self):
        self.serial_race(correction_first=False)

    def test_unsafe_guard_survives_later_row_failure_and_retry_after_fact_change(self):
        self.serial_race(correction_first=True, fail_later=True)

    def serial_race(self, *, correction_first, fail_later=False):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "synthetic-race.xlsx"
            database = str(Path(d) / "synthetic.sqlite")
            book = Workbook()
            sheet = book.active
            assert sheet is not None
            sheet.title = "Synthetic month"
            sheet.append(["HARD DRIVE LABEL", "DATE", "IN/OUT", "RECEIVED BY", "REMARKS"])
            sheet.append(["Synthetic accepted", None, "OUT", None, "S/N: SYN-RACE"])
            if fail_later:
                sheet.append(["Synthetic safe", None, "OUT", None, "S/N: SYN-SAFE"])
            book.save(path)
            book.close()
            paused, proceed, editing = Event(), Event(), Event()

            def checkpoint(stage):
                if fail_later and stage == "event-written":
                    raise RuntimeError("synthetic later row failure")
                if not correction_first and stage == "before-row-commit":
                    paused.set()
                    if not proceed.wait(10):
                        raise RuntimeError("synthetic scheduling timeout")

            imports = InventoryImports(
                database, store=SQLiteDriveStore(database, checkpoint=checkpoint)
            )
            original = imports.store.add_drive(Drive(serial="SYN-RACE", label="Synthetic accepted"))
            client = create_app(database).test_client()
            url = "/drive/" + original.id + "/edit"
            form = client.get(url).text
            csrf = re.search(r'name="csrf" value="([^"]+)"', form)
            revision = re.search(r'name="revision" value="([^"]+)"', form)
            assert csrf and revision
            token = csrf.group(1)
            revision_value = revision.group(1)
            before = test_movements.MovementTests.state(imports.store)

            def trace(frame, event, arg):
                # _write is entered only after the real core's movement guard.
                if (
                    correction_first
                    and event == "call"
                    and frame.f_code.co_name == "_write"
                    and frame.f_code.co_filename.endswith("/t6/drives/importer.py")
                ):
                    paused.set()
                    if not proceed.wait(10):
                        raise RuntimeError("synthetic scheduling timeout")
                return trace

            def run():
                sys.settrace(trace)
                try:
                    imports.run("synthetic-race", str(path), kind="log-sheet")
                finally:
                    sys.settrace(None)

            def edit():
                editing.set()
                return client.post(
                    url,
                    data={
                        "csrf": token,
                        "revision": revision_value,
                        "label": "Synthetic accepted",
                        "serial": "SYN-CORRECTED",
                        "suspected_serial": "SYN-RACE",
                    },
                )

            with ThreadPoolExecutor(2) as pool:
                importing = pool.submit(run)
                try:
                    self.assertTrue(paused.wait(10))
                    correcting = pool.submit(edit)
                    self.assertTrue(editing.wait(10))
                    if correction_first:
                        self.assertEqual(correcting.result(10).status_code, 303)
                finally:
                    proceed.set()
                if fail_later:
                    with self.assertRaisesRegex(RuntimeError, "synthetic later row failure"):
                        importing.result(20)
                else:
                    importing.result(20)
                response = correcting.result(20)
            # In the import-first ordering the import legitimately updates
            # revision; retry the stale edit with a fresh HTTP form.
            if response.status_code == 409:
                form = client.get(url).text
                revision = re.search(r'name="revision" value="([^"]+)"', form)
                assert revision
                revision_value = revision.group(1)
                response = edit()
            self.assertEqual(response.status_code, 303)
            restarted = InventoryImports(database)
            if fail_later:
                self.assertEqual(restarted.status("synthetic-race"), "failed")
                failed = test_movements.MovementTests.state(restarted.store)
                self.assertEqual(len(failed["import_rejections"]), 1)
                for table in (
                    "assignments",
                    "custody_events",
                    "shipments",
                    "shipment_members",
                    "imported_event_sources",
                    "import_receipts",
                ):
                    self.assertEqual(failed[table], before[table], table)
                self.assertEqual(len(restarted.evidence("synthetic-race")), 3)
                restarted.store.set_suspected_serial(original.id, None)
                restarted.run("synthetic-race", str(path), kind="log-sheet")
                self.assertIsNone(restarted.store.find_by_serial("SYN-RACE"))
                self.assertEqual(restarted.store.list_events(original.id), ())
                self.assertEqual(len(restarted.store.inventory.all()), 2)
                recovered = test_movements.MovementTests.state(restarted.store)
                self.assertEqual(len(recovered["import_receipts"]), 1)
                self.assertEqual(len(recovered["custody_events"]), 1)
                self.assertEqual(len(recovered["shipments"]), 1)
                self.assertIn(
                    "ambiguous serial", restarted.evidence("synthetic-race")[0]["reasons"]
                )
                restarted = InventoryImports(database)
                restarted.run("synthetic-race", str(path), kind="log-sheet")
                self.assertEqual(test_movements.MovementTests.state(restarted.store), recovered)
                return
            self.assertEqual(len(restarted.store.inventory.all()), 1)
            self.assertIsNone(restarted.store.find_by_serial("SYN-RACE"))
            corrected = restarted.store.get_drive(original.id)
            assert corrected is not None
            self.assertEqual(corrected.serial, "SYN-CORRECTED")
            self.assertEqual(corrected.suspected_serial, "SYN-RACE")
            state = test_movements.MovementTests.state(restarted.store)
            manifests = Shipments(restarted.store.connect, restarted.store.assignments).all()
            if correction_first:
                for table in (
                    "assignments",
                    "custody_events",
                    "shipments",
                    "shipment_members",
                    "imported_event_sources",
                    "import_receipts",
                ):
                    self.assertEqual(state[table], before[table], table)
                evidence = restarted.evidence("synthetic-race")
                row = next(e for e in evidence if e["row"] == 2)
                self.assertEqual(row["status"], "exception")
                self.assertIn("ambiguous serial", row["reasons"])
                self.assertIn("S/N: SYN-RACE", row["evidence"])
                page = create_app(database).test_client().get("/exceptions?q=SYN-RACE")
                self.assertIn("ambiguous serial", page.text)
            else:
                self.assertEqual(len(state["import_receipts"]), 1)
                self.assertEqual(len(restarted.store.list_events(original.id)), 1)
                self.assertEqual(len(manifests), 1)
                self.assertEqual(manifests[0]["lines"][0]["drive_id"], original.id)
                self.assertEqual(manifests[0]["lines"][0]["serial"], "SYN-RACE")
            restarted.run("synthetic-race", str(path), kind="log-sheet")
            self.assertEqual(test_movements.MovementTests.state(restarted.store), state)
            self.assertEqual(
                Shipments(restarted.store.connect, restarted.store.assignments).all(), manifests
            )


if __name__ == "__main__":
    unittest.main()
