"""SQLite implementation of the pinned core DriveStore contract.

Uses the app's inventory, Assignment and append-only custody tables. Shipment
methods implement only the core protocol, not app historical display snapshots.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, replace
from datetime import date
from typing import Any
from uuid import uuid4

from t6.drives import (
    Assignment,
    CustodyEvent,
    CustodyKind,
    Drive,
    DriveStatus,
    DuplicateSerial,
    Party,
    Ref,
    UnknownDrive,
    derive_status,
    normalize_serial,
)

from app import Inventory, decode, encode
from assignments import Assignments
from custody import Custody, restore


def event_json(event: CustodyEvent) -> str:
    value = asdict(event)
    value["kind"] = event.kind.value
    value["on"] = event.on.isoformat() if event.on else None
    if event.party:
        value["party"]["kind"] = event.party.kind.value
    return json.dumps(value)


class SQLiteDriveStore:
    def __init__(
        self,
        database: str,
        *,
        checkpoint: Callable[[str], None] | None = None,
        receipt_keys: dict[str, str] | None = None,
    ):
        self.receipt_keys = receipt_keys
        self.inventory = Inventory(database)
        self.assignments = Assignments(database)
        self.custody = Custody(self.connect)
        self.checkpoint = checkpoint or (lambda stage: None)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS import_receipts (
                row_key TEXT PRIMARY KEY, drive_id TEXT NOT NULL REFERENCES drives(id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS import_observations (
                row_key TEXT PRIMARY KEY REFERENCES import_receipts(row_key), reason TEXT NOT NULL)""")

    def connect(self) -> sqlite3.Connection:
        db = self.inventory.connect()
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def get_drive(self, drive_id: str) -> Drive | None:
        record = self.inventory.get(drive_id)
        return record[0] if record else None

    def find_by_serial(self, serial: str) -> Drive | None:
        return self.inventory.exact(serial)

    def has_suspected_serial(self, serial: str) -> bool:
        wanted = normalize_serial(serial)
        return any(d.suspected_serial == wanted for d in self.inventory.all())

    @staticmethod
    def _drive(db: sqlite3.Connection, drive_id: str) -> Drive:
        row = db.execute("SELECT facts FROM drives WHERE id=?", (drive_id,)).fetchone()
        if row is None:
            raise UnknownDrive(drive_id)
        return decode(row[0])

    @staticmethod
    def _insert(db: sqlite3.Connection, drive: Drive) -> None:
        # Importer has no ownership evidence. Core None=T6 must not invent that fact.
        try:
            db.execute(
                "INSERT INTO drives(id,serial,facts,ownership_known) VALUES(?,?,?,?)",
                (drive.id, drive.serial, encode(drive), drive.owner_studio_id is not None),
            )
        except sqlite3.IntegrityError:
            if db.execute("SELECT 1 FROM drives WHERE id=?", (drive.id,)).fetchone():
                raise ValueError("Drive ID already recorded") from None
            raise DuplicateSerial(drive.serial or "") from None

    @staticmethod
    def _update(db: sqlite3.Connection, drive: Drive) -> None:
        try:
            db.execute(
                "UPDATE drives SET serial=?,facts=?,revision=revision+1 WHERE id=?",
                (drive.serial, encode(drive), drive.id),
            )
        except sqlite3.IntegrityError:
            raise DuplicateSerial(drive.serial or "") from None

    def add_drive(self, drive: Drive) -> Drive:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._insert(db, drive)
        return drive

    def _edit(self, drive_id: str, **values: Any) -> Drive:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            drive = replace(self._drive(db, drive_id), **values)
            self._update(db, drive)
        return drive

    def set_serial(self, drive_id: str, serial: str | None) -> Drive:
        return self._edit(drive_id, serial=serial)

    def set_suspected_serial(self, drive_id: str, serial: str | None) -> Drive:
        return self._edit(drive_id, suspected_serial=serial)

    def promote_suspected_serial(self, drive_id: str) -> Drive:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._drive(db, drive_id)
            if old.suspected_serial is None:
                raise ValueError("No suspected serial")
            drive = replace(old, serial=old.suspected_serial, suspected_serial=None)
            self._update(db, drive)
        return drive

    def set_review(self, drive_id: str, *, needs_review: bool, note: str | None = None) -> Drive:
        return self._edit(drive_id, needs_review=needs_review, review_note=note)

    def imported_drive(self, row_key: str) -> str | None:
        row_key = self.receipt_keys[row_key] if self.receipt_keys is not None else row_key
        with self.connect() as db:
            row = db.execute(
                "SELECT drive_id FROM import_receipts WHERE row_key=?", (row_key,)
            ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _event(db: sqlite3.Connection, drive_id: str, event: CustodyEvent) -> None:
        db.execute(
            "INSERT INTO custody_events(drive_id,event,recipient,actor_mode) VALUES(?,?,?,?)",
            (
                drive_id,
                event_json(event),
                event.party.ref.text if event.party else None,
                "imported / unknown authentication",
            ),
        )

    def import_row(
        self, row_key: str, drive: Drive, assignment: Assignment, events: Sequence[CustodyEvent]
    ) -> None:
        row_key = self.receipt_keys[row_key] if self.receipt_keys is not None else row_key
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM import_receipts WHERE row_key=?", (row_key,)).fetchone():
                return
            row = (
                db.execute("SELECT facts FROM drives WHERE serial=?", (drive.serial,)).fetchone()
                if drive.serial
                else None
            )
            additional_facts = False
            if row:
                held = decode(row[0])
                additional_facts = any(
                    getattr(drive, field) is not None
                    and getattr(drive, field) != getattr(held, field)
                    for field in (
                        "label",
                        "description",
                        "brand",
                        "model",
                        "capacity_tb",
                        "format",
                        "remarks",
                    )
                )
                notes = list(dict.fromkeys(filter(None, (held.review_note, drive.review_note))))
                held = replace(
                    held,
                    needs_review=held.needs_review or drive.needs_review,
                    review_note="; ".join(notes) or None,
                )
                self._update(db, held)
            else:
                held = drive
                self._insert(db, held)
            existing = [
                Assignments.decode(r[0])
                for r in db.execute("SELECT value FROM assignments WHERE drive_id=?", (held.id,))
            ]
            if assignment not in existing:
                db.execute(
                    "INSERT INTO assignments(drive_id,value) VALUES(?,?)",
                    (held.id, Assignments.encode(assignment)),
                )
            for event in events:
                self._event(db, held.id, event)
                self.checkpoint("event-written")
            db.execute("INSERT INTO import_receipts VALUES(?,?)", (row_key, held.id))
            if additional_facts:
                db.execute(
                    "INSERT INTO import_observations VALUES(?,?)",
                    (row_key, "additional drive facts"),
                )
            self.checkpoint("before-row-commit")

    def append_event(self, drive_id: str, event: CustodyEvent) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._drive(db, drive_id)
            self._event(db, drive_id, event)

    def list_events(self, drive_id: str) -> tuple[CustodyEvent, ...]:
        with self.connect() as db:
            self._drive(db, drive_id)
            return tuple(
                restore(row[0])
                for row in db.execute(
                    "SELECT event FROM custody_events WHERE drive_id=? ORDER BY id", (drive_id,)
                )
            )

    def status(self, drive_id: str) -> DriveStatus | None:
        return derive_status(self.list_events(drive_id))

    def release_shipment(
        self, drive_ids: Iterable[str], *, on: date, party: Party, project: Ref, logged_by: Ref
    ) -> str:
        keys = list(dict.fromkeys(drive_ids))
        if not keys:
            raise ValueError("Shipment needs Drives")
        shipment = "shipment-" + uuid4().hex
        event = CustodyEvent(
            kind=CustodyKind.RELEASED,
            on=on,
            party=party,
            project=project,
            logged_by=logged_by,
            shipment_id=shipment,
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key in keys:
                self._drive(db, key)
            for key in keys:
                self._event(db, key, event)
        return shipment

    def shipment_events(self, shipment_id: str) -> tuple[tuple[Drive, CustodyEvent], ...]:
        with self.connect() as db:
            return tuple(
                (self._drive(db, key), event)
                for key, raw in db.execute("SELECT drive_id,event FROM custody_events ORDER BY id")
                if (event := restore(raw)).shipment_id == shipment_id
            )

    def add_assignment(self, drive_id: str, assignment: Assignment) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._drive(db, drive_id)
            db.execute(
                "INSERT INTO assignments(drive_id,value) VALUES(?,?)",
                (drive_id, Assignments.encode(assignment)),
            )

    def assignment_history(self, drive_id: str) -> list[Assignment]:
        with self.connect() as db:
            self._drive(db, drive_id)
            values = [
                Assignments.decode(r[0])
                for r in db.execute(
                    "SELECT value FROM assignments WHERE drive_id=? ORDER BY id", (drive_id,)
                )
            ]
        return sorted(values, key=lambda a: a.start or date.min)

    def open_assignments(self, project: Ref) -> list[tuple[Drive, Assignment]]:
        def same(held: Ref) -> bool:
            if not held.page_id or not project.page_id:
                return held == project
            left = self.assignments.project(held.page_id)
            right = self.assignments.project(project.page_id)
            return held == project or (
                left is not None and right is not None and left["t6_id"] == right["t6_id"]
            )

        found = [
            (d, a)
            for d in self.inventory.all()
            for a in self.assignment_history(d.id)
            if a.is_open and same(a.project)
        ]
        return sorted(
            found, key=lambda pair: (pair[1].start or date.min, pair[1].label or "", pair[0].id)
        )
