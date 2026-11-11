"""Synthetic host import acceptance against SQLite and observable HTTP."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Event

from t6.drives import Drive

from app import create_app
from import_store import SQLiteDriveStore
from imports import InventoryImports


def synthetic_workbook(path, rows):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Synthetic Show"
    sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "T6 IN", "TOTAL DISK SPACE", "BRAND NAME"])
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def complete_state(store):
    return [
        (
            asdict(d),
            [asdict(a) for a in store.assignment_history(d.id)],
            [asdict(e) for e in store.list_events(d.id)],
            store.inventory.get(d.id),
        )
        for d in store.inventory.all()
    ]


class InventoryImportTests(unittest.TestCase):
    def test_atomic_match_retains_conflicting_facts_even_if_core_lookup_missed_them(self):
        from t6.drives import Assignment, Ref

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "private.sqlite")
            imports = InventoryImports(database)
            held = imports.store.add_drive(Drive(serial="SYN-1", label="Original synthetic"))
            assignment = Assignment(
                project=Ref(text="Synthetic show"), label=None, role=None, start=None
            )
            imports.store.import_row(
                "synthetic-row",
                Drive(
                    serial="SYN-1",
                    label="New synthetic",
                    needs_review=True,
                    review_note="Synthetic uncertainty",
                ),
                assignment,
                [],
            )
            with imports.store.connect() as db:
                reason = db.execute(
                    "SELECT reason FROM import_observations WHERE row_key='synthetic-row'"
                ).fetchone()
            self.assertEqual(reason[0], "additional drive facts")
            actual = imports.store.get_drive(held.id)
            assert actual is not None
            self.assertEqual(actual.label, "Original synthetic")
            self.assertIn("Synthetic uncertainty", actual.review_note or "")

    def test_cached_project_resolution_preserves_ambiguity_and_page_less_projects(self):
        from assignments import Assignments

        class Reader:
            def read(self):
                return {
                    "complete": True,
                    "projects": [
                        {
                            "t6_id": "t6p_" + str(i) * 32,
                            "name": name,
                            "code": None,
                            "active_page_id": page,
                            "legacy_page_ids": [],
                        }
                        for i, name, page in [
                            (1, "Synthetic Resolved", "a" * 32),
                            (2, "Synthetic Ambiguous", "b" * 32),
                            (3, "Synthetic Ambiguous", "c" * 32),
                            (4, "Synthetic Pageless", None),
                        ]
                    ],
                }

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "private.sqlite")
            imports = InventoryImports(database)
            Assignments(database).refresh(Reader())
            for index, name in enumerate(
                ["Synthetic Resolved", "Synthetic Ambiguous", "Synthetic Pageless"]
            ):
                path = Path(directory) / f"synthetic-{index}.xlsx"
                synthetic_workbook(path, [["Synthetic portable", f"SYN-{index}"]])
                from openpyxl import load_workbook

                workbook = load_workbook(path)
                sheet = workbook.active
                assert sheet is not None
                sheet.title = name
                workbook.save(path)
                workbook.close()
                imports.run(f"synthetic-{index}", str(path))
                drive = imports.store.find_by_serial(f"SYN-{index}")
                assert drive is not None
                assignment = imports.store.assignment_history(drive.id)[0]
                detail = create_app(database).test_client().get("/drive/" + drive.id).text
                if index == 0:
                    self.assertEqual(assignment.project.page_id, "a" * 32)
                    self.assertIn("T6 Project ID: t6p_" + "1" * 32, detail)
                else:
                    self.assertEqual(assignment.project.text, name)
                    self.assertIn("Unresolved: " + name, detail)

    def test_cli_invalid_source_location_is_sanitized(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-private-") as directory:
            database = str(Path(directory) / "private.sqlite")
            result = (
                create_app(database)
                .test_cli_runner()
                .invoke(
                    args=["import-inventory", "--lineage", "synthetic", "--workbook", directory]
                )
            )
            self.assertNotEqual(result.exit_code, 0)
            self.assertNotIn(directory, result.output)

    def test_corrupt_source_and_parser_errors_never_leak_cli_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-private-marker.xlsx"
            raw = b"synthetic-secret-payload-not-a-workbook"
            path.write_bytes(raw)
            database = str(Path(directory) / "private.sqlite")
            result = (
                create_app(database)
                .test_cli_runner()
                .invoke(
                    args=[
                        "import-inventory",
                        "--lineage",
                        "synthetic-private-lineage",
                        "--workbook",
                        str(path),
                    ]
                )
            )
            self.assertNotEqual(result.exit_code, 0)
            for secret in [
                str(path),
                path.name,
                raw.decode(),
                "synthetic-private-lineage",
                "Traceback",
            ]:
                self.assertNotIn(secret, result.output)
            imports = InventoryImports(database)
            self.assertEqual(imports.status("synthetic-private-lineage"), "failed")
            with imports.store.connect() as db:
                self.assertEqual(db.execute("SELECT source FROM import_sources").fetchone()[0], raw)
            self.assertEqual(imports.store.inventory.all(), [])

    def test_import_reads_only_the_bytes_retained_before_source_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(path, [["Original synthetic", "SYN-ORIGINAL"]])
            original = path.read_bytes()

            def replace_source(stage):
                if stage == "evidence-written":
                    synthetic_workbook(path, [["Replacement synthetic", "SYN-REPLACED"]])

            imports = InventoryImports(
                database, store=SQLiteDriveStore(database, checkpoint=replace_source)
            )
            imports.run("synthetic", str(path))
            self.assertEqual(
                [d.label for d in imports.store.inventory.all()], ["Original synthetic"]
            )
            with imports.store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT source FROM import_sources").fetchone()[0], original
                )
            self.assertIn(
                "Original synthetic", str(imports.evidence("synthetic", exceptions_only=False))
            )
            with self.assertRaises(ValueError):
                InventoryImports(database).run("synthetic", str(path))

    def test_exception_evidence_is_monotonic_after_serial_removal_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(
                path, [["Additional label", "SYN-1", "YES", "4TB", "Synthetic brand"]]
            )
            store = SQLiteDriveStore(database)
            held = store.add_drive(
                Drive(
                    serial="SYN-1",
                    label="Original label",
                    needs_review=True,
                    review_note="Earlier uncertainty",
                )
            )
            imports = InventoryImports(database, store=store)
            imports.run("synthetic", str(path))
            initial = imports.evidence("synthetic")
            self.assertIn("additional drive facts", str(initial))
            stored = store.get_drive(held.id)
            assert stored is not None
            self.assertEqual(stored.label, "Original label")
            store.set_serial(held.id, None)
            before = complete_state(store)
            restarted = InventoryImports(database)
            restarted.run("synthetic", str(path))
            self.assertEqual(complete_state(restarted.store), before)
            self.assertEqual(restarted.evidence("synthetic"), initial)

    def test_failure_after_event_rolls_back_row_but_keeps_all_source_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(
                path,
                [
                    ["First", "SYN-A", "YES"],
                    ["Second", None, "YES"],
                    ["Unreadable", "SYN-B", None, "nonsense"],
                ],
            )
            count = 0

            def fail(stage):
                nonlocal count
                if stage == "event-written":
                    count += 1
                    if count == 2:
                        raise RuntimeError("Synthetic injected fault")

            store = SQLiteDriveStore(database, checkpoint=fail)
            imports = InventoryImports(database, store=store)
            with self.assertRaises(RuntimeError):
                imports.run("synthetic", str(path))
            restarted = InventoryImports(database)
            self.assertEqual(restarted.status("synthetic"), "failed")
            self.assertEqual(len(restarted.store.inventory.all()), 1)
            first = restarted.store.inventory.all()[0]
            self.assertEqual(first.label, "First")
            self.assertEqual(len(restarted.store.assignment_history(first.id)), 1)
            self.assertEqual(len(restarted.store.list_events(first.id)), 1)
            self.assertTrue(
                any("Unreadable" in e["evidence"] for e in restarted.evidence("synthetic"))
            )
            restarted.run("synthetic", str(path))
            before = complete_state(restarted.store)
            evidence = restarted.evidence("synthetic")
            self.assertEqual(len(before), 2)
            self.assertIn("unreadable", str(evidence))
            self.assertEqual(restarted.status("synthetic"), "complete")
            restarted.run("synthetic", str(path))
            self.assertEqual(complete_state(restarted.store), before)
            self.assertEqual(restarted.evidence("synthetic"), evidence)
            self.assertIn(
                "Unknown ownership",
                create_app(database).test_client().get("/drive/" + first.id).text,
            )

    def test_inputs_require_explicit_reconciliation_after_edit_move_or_new_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(path, [["Synthetic", "SYN-1"]])
            imports = InventoryImports(database)
            imports.run("synthetic", str(path))
            state = complete_state(imports.store)
            copied = Path(directory) / "moved.xlsx"
            copied.write_bytes(path.read_bytes())
            for lineage, input_path in [("synthetic", copied), ("new", copied), ("new", path)]:
                with self.assertRaises(ValueError):
                    imports.run(lineage, str(input_path))
            synthetic_workbook(path, [["Edited", "SYN-2"]])
            with self.assertRaises(ValueError):
                imports.run("synthetic", str(path))
            self.assertEqual(complete_state(imports.store), state)

    def test_missing_shared_annotated_and_unreadable_rows_reconcile_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(
                path,
                [
                    ["Missing", None, "YES"],
                    ["Annotated", "SYN-X (old)"],
                    ["Shared one", "SYN-S"],
                    ["Shared two", "SYN-S"],
                    ["Bad capacity", "SYN-B", None, "not a size"],
                    [None, None, None, "4TB"],
                ],
            )
            imports = InventoryImports(database)
            imports.run("synthetic", str(path))
            drives = imports.store.inventory.all()
            self.assertEqual(len(drives), 4)
            self.assertTrue(all(d.serial is None for d in drives))
            self.assertTrue(imports.store.has_suspected_serial("syn-x"))
            self.assertIsNone(imports.store.find_by_serial("syn-s"))
            evidence = imports.evidence("synthetic")
            self.assertEqual({e["row"] for e in evidence}, set(range(1, 8)))
            client = create_app(database).test_client()
            self.assertIn("Suspected (uncertain)", client.get("/?q=SYN-X").text)
            for d in drives:
                detail = client.get("/drive/" + d.id).text
                self.assertIn("Unresolved: Synthetic Show", detail)
                self.assertIn("Role: Unknown", detail)
                self.assertNotIn("not a size", detail)
            self.assertEqual(client.get("/?serial=SYN-X").text.count("/drive/"), 0)

    def test_import_serial_edit_and_parallel_rerun_serialize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            database = str(Path(directory) / "private.sqlite")
            synthetic_workbook(path, [["Synthetic", "SYN-1", "YES"]])
            locked, release, editing = Event(), Event(), Event()

            def pause(stage):
                if stage == "before-row-commit":
                    locked.set()
                    if not release.wait(10):
                        raise RuntimeError("Synthetic timeout")

            imports = InventoryImports(database, store=SQLiteDriveStore(database, checkpoint=pause))
            other = SQLiteDriveStore(database)
            parallel = InventoryImports(database)

            def edit():
                editing.set()
                # This writer must wait for the row to commit before matching/removing.
                with other.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT id FROM drives WHERE serial='SYN-1'").fetchone()
                other.set_serial(row[0], None)

            with ThreadPoolExecutor(3) as pool:
                run = pool.submit(imports.run, "synthetic", str(path))
                self.assertTrue(locked.wait(10))
                correction = pool.submit(edit)
                self.assertTrue(editing.wait(10))
                rerun = pool.submit(parallel.run, "synthetic", str(path))
                self.assertFalse(correction.done())
                release.set()
                run.result(15)
                correction.result(15)
                rerun.result(15)
            state = complete_state(other)
            self.assertEqual(len(state), 1)
            self.assertIsNone(state[0][0]["serial"])
            self.assertEqual(len(state[0][1]), 1)
            self.assertEqual(len(state[0][2]), 1)
            InventoryImports(database).run("synthetic", str(path))
            self.assertEqual(complete_state(other), state)

    def test_cli_import_is_durable_and_visible_without_http_upload(self):
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            assert sheet is not None
            sheet.title = "Synthetic Show"
            sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "T6 IN"])
            sheet.append(["Synthetic portable", None, "YES"])
            workbook.save(source)
            database = str(Path(directory) / "private.sqlite")
            app = create_app(database)
            result = app.test_cli_runner().invoke(
                args=["import-inventory", "--lineage", "synthetic-run", "--workbook", str(source)]
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn(str(source), result.output)
            client = create_app(database).test_client()
            listing = client.get("/?q=Synthetic portable").text
            self.assertIn("Synthetic portable", listing)
            self.assertIn("Needs review", listing)
            self.assertEqual(client.post("/import-inventory").status_code, 403)
            rerun = app.test_cli_runner().invoke(
                args=["import-inventory", "--lineage", "synthetic-run", "--workbook", str(source)]
            )
            self.assertEqual(rerun.exit_code, 0, rerun.output)
            self.assertEqual(client.get("/").text.count("Synthetic portable"), 1)
