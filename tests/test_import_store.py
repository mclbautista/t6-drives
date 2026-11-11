"""Persistent protocol coverage; no fake stores or production data."""

import tempfile
import unittest
from dataclasses import asdict
from datetime import date
from pathlib import Path

from t6.drives import (
    Assignment,
    CustodyEvent,
    CustodyKind,
    Drive,
    DriveStatus,
    DuplicateSerial,
    Party,
    PartyKind,
    Ref,
    UnknownDrive,
)

from import_store import SQLiteDriveStore


class StoreContractTests(unittest.TestCase):
    def test_receipt_commit_failure_rolls_back_every_relationship_then_retry_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "synthetic.sqlite")

            def fail(stage):
                if stage == "before-row-commit":
                    raise RuntimeError("Synthetic before-commit fault")

            store = SQLiteDriveStore(database, checkpoint=fail)
            held = store.add_drive(
                Drive(serial="SYN-A", label="Original synthetic", review_note="Earlier synthetic")
            )
            observed = Drive(
                serial="SYN-A",
                label="Additional synthetic",
                needs_review=True,
                review_note="New synthetic",
            )
            assignment = Assignment(
                label="Synthetic use", project=Ref(text="Synthetic show"), role=None, start=None
            )
            event = CustodyEvent(
                kind=CustodyKind.RECEIVED,
                on=None,
                party=None,
                project=assignment.project,
                logged_by=Ref(text="Synthetic actor"),
            )
            with self.assertRaises(RuntimeError):
                store.import_row("synthetic-row", observed, assignment, [event, event])
            self.assertEqual(store.get_drive(held.id), held)
            actual = store.get_drive(held.id)
            assert actual is not None
            self.assertEqual(asdict(actual), asdict(held))
            self.assertEqual(store.list_events(held.id), ())
            self.assertEqual(store.assignment_history(held.id), [])
            self.assertIsNone(store.imported_drive("synthetic-row"))
            with store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM import_observations").fetchone()[0], 0
                )
            store = SQLiteDriveStore(database)
            store.import_row("synthetic-row", observed, assignment, [event, event])
            actual = store.get_drive(held.id)
            assert actual is not None
            before = asdict(actual)
            self.assertEqual(actual.label, held.label)
            self.assertEqual(actual.review_note, "Earlier synthetic; New synthetic")
            store.set_serial(held.id, None)
            store.import_row("synthetic-row", observed, assignment, [event, event])
            self.assertEqual(len(store.inventory.all()), 1)
            self.assertEqual(store.list_events(held.id), (event, event))
            self.assertEqual(store.assignment_history(held.id), [assignment])
            self.assertEqual(store.imported_drive("synthetic-row"), held.id)
            store.set_serial(held.id, before["serial"])
            actual = store.get_drive(held.id)
            assert actual is not None
            self.assertEqual(asdict(actual), before)

    def test_serial_mutations_validate_atomically_without_history_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteDriveStore(str(Path(directory) / "synthetic.sqlite"))
            first = store.add_drive(Drive(serial=" syn-a ", label="Synthetic first"))
            second = store.add_drive(Drive(label="Synthetic second", suspected_serial="syn-a"))
            event = CustodyEvent(
                kind=CustodyKind.RECEIVED,
                on=None,
                party=None,
                project=Ref(text="Synthetic show"),
                logged_by=Ref(text="Synthetic actor"),
            )
            store.append_event(second.id, event)
            assignment = Assignment(
                project=Ref(text="Synthetic show"), label=None, role=None, start=None
            )
            store.add_assignment(second.id, assignment)
            held = store.get_drive(second.id)
            assert held is not None
            before = asdict(held)
            for change in [
                lambda: store.set_serial(second.id, "syn-a"),
                lambda: store.promote_suspected_serial(second.id),
            ]:
                with self.assertRaises(DuplicateSerial):
                    change()
                held = store.get_drive(second.id)
                assert held is not None
                self.assertEqual(asdict(held), before)
            with self.assertRaises(ValueError):
                store.set_serial(second.id, " ")
            with self.assertRaises(ValueError):
                store.add_drive(Drive(id=first.id, label="Duplicate ID"))
            store.set_serial(first.id, None)
            promoted = store.promote_suspected_serial(second.id)
            self.assertEqual(promoted.serial, "SYN-A")
            self.assertIsNone(promoted.suspected_serial)
            store.set_suspected_serial(second.id, "syn-b")
            store.set_review(second.id, needs_review=True, note="Synthetic uncertainty")
            self.assertTrue(store.has_suspected_serial(" syn-b "))
            self.assertIsNone(store.find_by_serial("syn-b"))
            self.assertEqual(store.list_events(second.id), (event,))
            self.assertEqual(store.assignment_history(second.id), [assignment])
            restarted = SQLiteDriveStore(store.inventory.path)
            reloaded, held = restarted.get_drive(second.id), store.get_drive(second.id)
            assert reloaded is not None and held is not None
            self.assertEqual(asdict(reloaded), asdict(held))
            with self.assertRaises(UnknownDrive):
                store.set_serial("missing", "SYN-Z")
            for query in [store.list_events, store.assignment_history, store.status]:
                with self.assertRaises(UnknownDrive):
                    query("missing")

    def test_protocol_shipments_preserve_order_dedup_and_unknown_atomicity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteDriveStore(str(Path(directory) / "synthetic.sqlite"))
            first = store.add_drive(Drive(label="Synthetic first"))
            second = store.add_drive(Drive(label="Synthetic second"))
            project = Ref(text="Synthetic show")
            actor = Ref(text="Synthetic actor")
            party = Party(kind=PartyKind.CONTACT, ref=Ref(text="Synthetic recipient"))
            for keys, error in [([], ValueError), ([first.id, "missing"], UnknownDrive)]:
                with self.assertRaises(error):
                    store.release_shipment(
                        keys, on=date(2025, 1, 2), party=party, project=project, logged_by=actor
                    )
                self.assertEqual(store.list_events(first.id), ())
            shipment = store.release_shipment(
                [second.id, first.id, second.id],
                on=date(2025, 1, 2),
                party=party,
                project=project,
                logged_by=actor,
            )
            self.assertEqual(
                [d.id for d, _ in store.shipment_events(shipment)], [second.id, first.id]
            )
            self.assertEqual(store.shipment_events("missing"), ())
            store.append_event(
                first.id,
                CustodyEvent(
                    kind=CustodyKind.RETURNED,
                    on=date(2024, 1, 1),
                    party=None,
                    project=project,
                    logged_by=actor,
                ),
            )
            self.assertEqual(store.status(first.id), DriveStatus.IN_CUSTODY)
            unknown = Assignment(project=project, label="Z", role=None, start=None)
            dated = Assignment(project=project, label="A", start=date(2025, 1, 1), role=None)
            closed = Assignment(
                project=project, end=date(2025, 1, 2), label=None, role=None, start=None
            )
            store.add_assignment(first.id, dated)
            store.add_assignment(second.id, unknown)
            store.add_assignment(first.id, closed)
            self.assertEqual(store.open_assignments(project), [(second, unknown), (first, dated)])
            self.assertEqual(store.open_assignments(Ref(text="Other synthetic show")), [])
