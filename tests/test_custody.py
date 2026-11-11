"""Synthetic custody acceptance through HTTP and durable SQLite only."""

import re
import tempfile
import unittest
from pathlib import Path

from test_inventory import edit_data, token

from app import create_app


class CustodyTest(unittest.TestCase):
    def test_structured_references_free_text_unknowns_and_escaping_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            app = create_app(path, custody_actor_policy="self-declared-synthetic")
            client = app.test_client()
            location = client.post(
                "/new", data={"csrf": token(client), "description": "synthetic references"}
            ).headers["Location"]
            for kind in ("received", "released", "returned"):
                self.assertEqual(
                    client.post(
                        location + "/custody",
                        data={
                            "csrf": token(client),
                            "kind": kind,
                            "recipient": "<script>synthetic recipient</script>",
                            "actor": "<script>synthetic actor</script>",
                            "project_page_id": "synthetic-project-page",
                            "party_kind": "contact",
                            "party_page_id": "synthetic-contact-page",
                            "reason": "<script>synthetic reason</script>",
                        },
                    ).status_code,
                    303,
                )
            restarted = create_app(path).test_client()
            page = restarted.get(location).text
            for value in (
                "synthetic-project-page",
                "synthetic-contact-page",
                "Unknown date",
                "not authenticated",
                "&lt;script&gt;synthetic recipient",
                "&lt;script&gt;synthetic actor",
                "&lt;script&gt;synthetic reason",
            ):
                self.assertIn(value, page)
            self.assertNotIn("<script>", page)
            self.assertNotIn("Existing studio reference", page)
            self.assertEqual(
                client.post(
                    location + "/custody", data={"csrf": token(client), "kind": "received"}
                ).status_code,
                303,
            )
            page = client.get(location).text
            self.assertIn("Unknown recipient", page)
            self.assertIn("Project: Unknown (unresolved)", page)
            self.assertIn("Unknown actor", page)
            self.assertEqual(
                client.post(
                    location + "/edit",
                    data=edit_data(
                        client, location, capacity_tb="invalid", description="must not commit"
                    ),
                ).status_code,
                400,
            )
            self.assertEqual(page, client.get(location).text)
            self.assertEqual(
                client.post(location + "/custody/1/edit", data={"csrf": token(client)}).status_code,
                404,
            )
            self.assertEqual(
                client.delete(location + "/custody", data={"csrf": token(client)}).status_code, 405
            )
            self.assertEqual(page, client.get(location).text)

    def test_concurrent_appends_are_durable_and_cross_drive_correction_is_rejected(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            app = create_app(path)
            client = app.test_client()
            locations = [
                client.post(
                    "/new", data={"csrf": token(client), "description": f"synthetic {n}"}
                ).headers["Location"]
                for n in range(2)
            ]

            def submit(n):
                writer = app.test_client()
                return writer.post(
                    locations[0] + "/custody",
                    data={"csrf": token(writer), "kind": "received", "reason": f"synthetic {n}"},
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(submit, range(2))), [303, 303])
            page = create_app(path).test_client().get(locations[0]).text
            ids = re.findall(r'data-event-id="(\d+)"', page)
            self.assertEqual(len(ids), 2)
            self.assertIn("synthetic 0", page)
            self.assertIn("synthetic 1", page)
            before = client.get(locations[1]).data
            with self.assertNoLogs(app.logger):
                response = client.post(
                    locations[1] + "/custody",
                    data={"csrf": token(client), "kind": "returned", "correction_of": ids[0]},
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(before, client.get(locations[1]).data)

            # Assignment forms introduce per-session CSRF tokens on the detail page.
            def stable_detail(html):
                return re.sub(r'name="csrf" value="[^"]+"', 'name="csrf"', html)

            self.assertEqual(
                stable_detail(page),
                stable_detail(create_app(path).test_client().get(locations[0]).text),
            )

    def test_uncertain_drive_all_kinds_correction_fact_edit_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            client = create_app(path, custody_actor_policy="self-declared-synthetic").test_client()
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "description": "synthetic uncertain",
                    "needs_review": "on",
                },
            ).headers["Location"]
            self.assertIn("Custody status: unknown", client.get(location).text)
            for kind, status in (
                ("received", "in custody"),
                ("released", "released"),
                ("returned", "in custody"),
                ("retired", "retired"),
            ):
                response = client.post(
                    location + "/custody",
                    data={
                        "csrf": token(client),
                        "kind": kind,
                        "on": "2025-05-06",
                        "recipient": "synthetic recipient",
                        "project_text": "synthetic unresolved",
                        "actor": "synthetic operator",
                        "reason": "synthetic reason",
                    },
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn("Custody status: " + status, client.get(location).text)
                self.assertIn(location, client.get("/", query_string={"status": status}).text)
            before = client.get(location).text
            ids = re.findall(r'data-event-id="(\d+)"', before)
            self.assertEqual(len(ids), 4)
            response = client.post(
                location + "/custody",
                data={
                    "csrf": token(client),
                    "kind": "received",
                    "on": "2000-01-01",
                    "correction_of": ids[-1],
                    "reason": "synthetic correction",
                },
            )
            self.assertEqual(response.status_code, 303)
            page = client.get(location).text
            self.assertIn("Custody status: in custody", page)
            self.assertIn("Correction of event " + ids[-1], page)
            match = re.search(r'<section id="custody-history">.*?</section>', page, re.DOTALL)
            assert match is not None
            history = match.group()
            self.assertIn("self-declared", history)
            self.assertIn("Unknown actor", history)
            self.assertIn("Unknown date", client.get(location + "/custody").text)
            self.assertEqual(
                client.post(
                    location + "/edit",
                    data=edit_data(client, location, description="synthetic corrected facts"),
                ).status_code,
                303,
            )
            restarted = create_app(path).test_client().get(location).text
            self.assertIn(history, restarted)
            self.assertIn("Custody status: in custody", restarted)
            self.assertNotIn(location, client.get("/?status=retired").text)

    def test_rejected_writes_leave_all_facts_history_and_status_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "description": "synthetic retained",
                    "needs_review": "on",
                },
            ).headers["Location"]
            self.assertEqual(
                client.post(
                    location + "/custody", data={"csrf": token(client), "kind": "released"}
                ).status_code,
                303,
            )
            before = client.get(location).data
            for fields in (
                {"kind": "retired"},
                {"kind": "retired", "reason": "   "},
                {"kind": "received", "correction_of": "not-an-id"},
                {"kind": "invented"},
                {"kind": "received", "on": "private-invalid-date"},
                {"kind": "received", "correction_of": "999"},
                {"kind": "received", "actor": "unapproved actor"},
                {"kind": "released", "party_page_id": "synthetic-party"},
                {"kind": "released", "party_kind": "invented", "party_page_id": "synthetic-party"},
                {"kind": "released", "project_text": "text", "project_page_id": "page"},
            ):
                response = client.post(
                    location + "/custody", data={"csrf": token(client), **fields}
                )
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("private-invalid-date", response.text)
                self.assertEqual(before, client.get(location).data)
            self.assertEqual(
                client.post(
                    "/drive/missing/custody", data={"csrf": token(client), "kind": "received"}
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(location + "/custody", data={"kind": "received"}).status_code, 403
            )
            self.assertEqual(
                client.post(
                    location + "/custody",
                    data={"csrf": token(client), "kind": "received"},
                    headers={"Origin": "http://evil.test"},
                ).status_code,
                403,
            )
            self.assertEqual(before, client.get(location).data)
            self.assertEqual(client.get(location + "/custody?kind=received").status_code, 200)
            self.assertEqual(before, client.get(location).data)
