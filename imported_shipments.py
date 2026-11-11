"""Import provenance and assembly barrier for immutable reconstructed manifests."""

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from assignments import Assignments
from custody import restore
from shipments import Shipments


def shipment_key(lineage: str, source_key: str) -> str:
    return "t6s_" + hashlib.sha256(json.dumps([lineage, source_key]).encode()).hexdigest()


def initialize(connect: Callable[[], sqlite3.Connection], assignments: Assignments) -> None:
    Shipments(connect, assignments)
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS imported_event_sources (
            event_id INTEGER PRIMARY KEY REFERENCES custody_events(id),
            lineage TEXT NOT NULL, row_key TEXT NOT NULL,
            facts TEXT NOT NULL, assignment TEXT NOT NULL, shipment_id TEXT)""")


def finalize_shipments(
    db: sqlite3.Connection,
    lineage: str,
    assignments: Assignments,
    checkpoint: Callable[[str], None],
) -> None:
    """Called only after core finishes all rows. Failure rolls back all snapshots.

    Source facts are saved with each row, not inferred on retry from mutable
    Drive data. A distinct event is a manifest line even for the same Drive.
    """
    keys = db.execute(
        "SELECT DISTINCT shipment_id FROM imported_event_sources WHERE lineage=? AND shipment_id IS NOT NULL ORDER BY event_id",
        (lineage,),
    ).fetchall()
    for (key,) in keys:
        if db.execute("SELECT 1 FROM shipments WHERE id=?", (key,)).fetchone():
            continue
        rows = db.execute(
            "SELECT s.event_id,s.row_key,s.facts,s.assignment,e.drive_id,e.event FROM imported_event_sources s JOIN custody_events e ON e.id=s.event_id WHERE s.lineage=? AND s.shipment_id=? ORDER BY s.event_id",
            (lineage, key),
        ).fetchall()
        lines = []
        for event_id, row_key, raw, assignment, drive_id, event in rows:
            facts = json.loads(raw)
            lines.append(
                {
                    "drive_id": drive_id,
                    **{
                        field: facts[field]
                        for field in (
                            "serial",
                            "suspected_serial",
                            "label",
                            "description",
                            "capacity_tb",
                            "needs_review",
                            "review_note",
                        )
                    },
                    "source_row_key": row_key,
                    "source_recorded_facts": facts,
                    "source_assignment": json.loads(assignment),
                }
            )
        first = json.loads(rows[0][5])
        event_value = restore(rows[0][5])
        snapshot = {
            "id": key,
            "evidence": "Import-time reconstruction — source-recorded values, not contemporaneously verified historical evidence; unavailable original facts remain Unknown. Original physical facts, Project display at release and authenticated actor are not available.",
            "recorded_at": datetime.now(UTC).isoformat(),
            "on": first["on"],
            "recipient": event_value.party.ref.text if event_value.party else None,
            "party": first["party"],
            "project": first["project"],
            "project_display": assignments.project_display(event_value.project),
            "actor": first["logged_by"],
            "actor_mode": "imported / unknown authentication",
            "reason": event_value.reason,
            "lines": lines,
            "source_events": [json.loads(row[5]) for row in rows],
        }
        db.execute("INSERT INTO shipments VALUES(?,?)", (key, json.dumps(snapshot)))
        for position, row in enumerate(rows):
            db.execute(
                "INSERT INTO shipment_members VALUES(?,?,?,?)", (key, position, row[4], row[0])
            )
        checkpoint("imported-snapshot-written")
