"""Synthetic shipments through HTTP with real SQLite (including fault injection)."""

import json
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from test_assignments import NEW, OLD, Reader, export
from test_inventory import edit_data, token
from werkzeug.datastructures import MultiDict

from app import create_app


class ShipmentTest(unittest.TestCase):
    def test_batch_deduplicates_and_saves_unknown_facts_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            client = create_app(path).test_client()
            locations = [
                client.post(
                    "/new", data={"csrf": token(client), "description": f"synthetic {n}"}
                ).headers["Location"]
                for n in range(2)
            ]
            ids = [location.rsplit("/", 1)[-1] for location in locations]
            response = client.post(
                "/shipments/new",
                data=MultiDict([("csrf", token(client)), *[("drive_id", x) for x in ids + ids]]),
            )
            self.assertEqual(response.status_code, 303)
            location = response.headers["Location"]
            page = client.get(location).text
            for value in ids + [
                "Unknown accepted serial",
                "Unknown suspected serial",
                "Unknown recipient",
                "Unknown date",
                "Unknown actor",
                "synthetic 0",
                "synthetic 1",
            ]:
                self.assertIn(value, page)
            self.assertEqual(page.count('data-drive-id="'), 2)
            self.assertEqual(page, create_app(path).test_client().get(location).text)
            for drive in locations:
                self.assertIn("Custody status: released", client.get(drive).text)
                self.assertEqual(client.get(drive).text.count('data-event-id="'), 1)
            with sqlite3.connect(path) as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM shipment_members").fetchone()[0], 2
                )
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_saved_output_survives_facts_bindings_recipient_changes_and_corrections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            reader = Reader(export())
            app = create_app(path, reader, custody_actor_policy="self-declared-synthetic")
            client = app.test_client()
            self.assertEqual(app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "description": "synthetic original description",
                    "serial": "accepted-original",
                    "suspected_serial": "candidate-original",
                    "label": "physical-original",
                    "capacity_tb": "4",
                    "needs_review": "on",
                    "review_note": "synthetic original review",
                },
            ).headers["Location"]
            drive_id = location.rsplit("/", 1)[-1]
            self.assertEqual(
                client.post(
                    location + "/assignments",
                    data={
                        "csrf": token(client),
                        "project_page": OLD,
                        "label": "assignment-original",
                        "start": "2024-01-01",
                    },
                ).status_code,
                303,
            )
            response = client.post(
                "/shipments/new",
                data={
                    "csrf": token(client),
                    "drive_id": drive_id,
                    "on": "2024-02-03",
                    "recipient": "<script>synthetic original recipient</script>",
                    "party_kind": "contact",
                    "party_page_id": "synthetic-original-contact",
                    "actor": "<script>synthetic original actor</script>",
                    "project_page_id": OLD,
                },
            )
            self.assertEqual(response.status_code, 303)
            shipment = response.headers["Location"]
            saved = client.get(shipment).data
            listing = client.get("/shipments").data
            for value in [
                "ACCEPTED-ORIGINAL",
                "CANDIDATE-ORIGINAL",
                "assignment-original",
                "4.0",
                "Synthetic show",
                "synthetic-original-contact",
                "2024-02-03",
                "synthetic original review",
                "&lt;script&gt;synthetic original recipient",
                "&lt;script&gt;synthetic original actor",
                "not authenticated",
            ]:
                self.assertIn(value, saved.decode())
            self.assertNotIn("<script>", saved.decode())
            history = re.search(
                r'<section id="custody-history">.*?</section>', client.get(location).text, re.DOTALL
            )
            assert history is not None
            self.assertEqual(
                client.post(
                    location + "/edit",
                    data=edit_data(
                        client,
                        location,
                        serial="accepted-corrected",
                        suspected_serial="candidate-corrected",
                        label="physical-corrected",
                        capacity_tb="8",
                        description="synthetic corrected description",
                        owner_studio_id="synthetic-studio",
                        ownership="studio",
                    ),
                ).status_code,
                303,
            )
            self.assertIn(history.group(), client.get(location).text)
            reader.payload = export(NEW, (OLD,), name="Synthetic changed description")
            self.assertEqual(app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
            reader.payload = RuntimeError("synthetic private upstream diagnostic")
            self.assertNotEqual(
                app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0
            )
            detail = client.get(location).text
            event_id = re.search(r'data-event-id="(\d+)"', detail)
            assert event_id is not None
            for kind in ("returned", "released"):
                self.assertEqual(
                    client.post(
                        location + "/custody",
                        data={
                            "csrf": token(client),
                            "kind": kind,
                            "recipient": "synthetic corrected recipient",
                            "project_text": "synthetic changed unresolved",
                            "correction_of": event_id.group(1),
                        },
                    ).status_code,
                    303,
                )
            self.assertIn(
                "Open Project use (not custody): Synthetic changed description",
                client.get("/").text,
            )
            self.assertIn("End: Open", client.get(location).text)
            self.assertEqual(client.get(location).text.count('data-event-id="'), 3)
            before = client.get(location).data
            self.assertEqual(
                client.post(
                    location + "/edit",
                    data=edit_data(
                        client, location, capacity_tb="NaN", description="must not commit"
                    ),
                ).status_code,
                400,
            )
            self.assertEqual(before, client.get(location).data)
            client.post(
                "/new", data={"csrf": token(client), "serial": "synthetic collision blocker"}
            )
            self.assertEqual(
                client.post(
                    location + "/edit",
                    data=edit_data(
                        client,
                        location,
                        serial="synthetic collision blocker",
                        description="must not collide",
                    ),
                ).status_code,
                400,
            )
            self.assertEqual(before, client.get(location).data)
            restarted = create_app(path).test_client()
            self.assertEqual(saved, restarted.get(shipment).data)
            self.assertEqual(listing, restarted.get("/shipments").data)
            # The retained cache is optional: unresolved handovers remain available in outage.
            response = restarted.post(
                "/shipments/new",
                data={
                    "csrf": token(restarted),
                    "drive_id": drive_id,
                    "project_text": "synthetic unresolved outage",
                },
            )
            self.assertEqual(response.status_code, 303)
            self.assertIn(
                "Unresolved: synthetic unresolved outage",
                restarted.get(response.headers["Location"]).text,
            )

    def test_invalid_batches_and_injected_failures_preserve_entire_populated_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            app = create_app(path)
            client = app.test_client()
            ids = [
                client.post("/new", data={"csrf": token(client), "description": f"synthetic {n}"})
                .headers["Location"]
                .rsplit("/", 1)[-1]
                for n in range(2)
            ]
            self.assertEqual(
                client.post(
                    "/shipments/new", data={"csrf": token(client), "drive_id": ids}
                ).status_code,
                303,
            )

            def dump():
                with sqlite3.connect(path) as db:
                    return "\n".join(db.iterdump())

            before = dump()
            for fields in (
                {},
                {"drive_id": [ids[0], "synthetic-unknown"]},
                {"drive_id": ids, "on": "private-invalid-date"},
                {"drive_id": ids, "party_kind": "contact"},
                {"drive_id": ids, "party_kind": "invented", "party_page_id": "private-id"},
                {"drive_id": ids, "project_page_id": OLD, "project_text": "private-conflict"},
                {"drive_id": ids, "actor": "private-unapproved-actor"},
            ):
                with self.assertNoLogs(app.logger):
                    response = client.post("/shipments/new", data={"csrf": token(client), **fields})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("private-", response.text)
                self.assertEqual(before, dump())
            for table, condition in (
                ("shipments", "1"),
                ("custody_events", f"NEW.drive_id='{ids[1]}'"),
                ("shipment_members", "NEW.position=1"),
            ):
                with sqlite3.connect(path) as db:
                    db.execute(
                        f"CREATE TRIGGER synthetic_failure BEFORE INSERT ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT, 'synthetic private persistence diagnostic'); END"
                    )
                before_fault = dump()
                with self.assertLogs(app.logger, level="ERROR") as logs:
                    response = client.post(
                        "/shipments/new", data={"csrf": token(client), "drive_id": ids}
                    )
                self.assertEqual(response.status_code, 500)
                self.assertNotIn("private", response.text + str(logs.output))
                self.assertEqual(before_fault, dump())
                with sqlite3.connect(path) as db:
                    db.execute("DROP TRIGGER synthetic_failure")
                self.assertEqual(before, dump())
            for kwargs in (
                {
                    "headers": {"Origin": "https://evil.test"},
                    "data": {"csrf": token(client), "drive_id": ids},
                },
                {"data": {"drive_id": ids}},
            ):
                self.assertEqual(client.post("/shipments/new", **kwargs).status_code, 403)
                self.assertEqual(before, dump())
            self.assertEqual(client.get("/shipments/new?drive_id=" + ids[0]).status_code, 200)
            self.assertEqual(client.get("/shipments/missing").status_code, 404)
            self.assertEqual(
                client.post("/shipments/missing", data={"csrf": token(client)}).status_code, 405
            )
            self.assertEqual(before, dump())

    def test_concurrent_handover_and_fact_edits_never_commit_partial_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            app = create_app(path)
            client = app.test_client()
            locations = [
                client.post(
                    "/new",
                    data={
                        "csrf": token(client),
                        "serial": f"synthetic-before-{n}",
                        "description": f"synthetic {n}",
                        "capacity_tb": "4",
                    },
                ).headers["Location"]
                for n in range(2)
            ]
            ids = [location.rsplit("/", 1)[-1] for location in locations]
            barrier = Barrier(3)

            def write(n):
                writer = app.test_client()
                csrf = token(writer)
                edit = edit_data(
                    writer,
                    locations[0],
                    serial="synthetic-after",
                    description="synthetic after",
                    capacity_tb="8",
                )
                barrier.wait(timeout=10)
                if n == 2:
                    return writer.post(locations[0] + "/edit", data=edit)
                return writer.post(
                    "/shipments/new",
                    data={
                        "csrf": csrf,
                        "drive_id": ids + ids,
                        "recipient": f"synthetic recipient {n}",
                    },
                )

            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(write, range(3)))
            self.assertEqual([r.status_code for r in results], [303, 303, 303])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM shipments").fetchone()[0], 2)
                self.assertEqual(
                    db.execute("SELECT count(*) FROM shipment_members").fetchone()[0], 4
                )
                self.assertEqual(db.execute("SELECT count(*) FROM custody_events").fetchone()[0], 4)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
                snapshots = [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM shipments")]
                for snapshot in snapshots:
                    self.assertEqual([line["drive_id"] for line in snapshot["lines"]], ids)
                    line = snapshot["lines"][0]
                    self.assertIn(
                        (line["serial"], line["description"], line["capacity_tb"]),
                        [
                            ("SYNTHETIC-BEFORE-0", "synthetic 0", 4.0),
                            ("SYNTHETIC-AFTER", "synthetic after", 8.0),
                        ],
                    )
                    members = db.execute(
                        "SELECT m.drive_id,e.drive_id,e.event FROM shipment_members m JOIN custody_events e ON e.id=m.event_id WHERE m.shipment_id=? ORDER BY m.position",
                        (snapshot["id"],),
                    ).fetchall()
                    self.assertEqual([m[0] for m in members], ids)
                    for selected, actual, raw in members:
                        self.assertEqual(selected, actual)
                        self.assertEqual(json.loads(raw)["shipment_id"], snapshot["id"])
            restarted = create_app(path).test_client()
            for response in results[:2]:
                self.assertEqual(
                    client.get(response.headers["Location"]).data,
                    restarted.get(response.headers["Location"]).data,
                )


if __name__ == "__main__":
    unittest.main()
