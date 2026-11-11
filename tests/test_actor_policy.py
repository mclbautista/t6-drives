"""Approved required, unauthenticated attribution at the HTTP boundary."""

import tempfile
import unittest
from pathlib import Path

from test_inventory import token

from app import create_app


class ActorPolicyTest(unittest.TestCase):
    def test_required_actor_rejects_invalid_values_without_partial_events_or_shipments(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "db")
            client = create_app(
                database, custody_actor_policy="self-declared-required"
            ).test_client()
            location = client.post(
                "/new", data={"csrf": token(client), "label": "Synthetic actor drive"}
            ).headers["Location"]
            drive_id = location.rsplit("/", 1)[1]
            for route, fields in (
                (location + "/custody", {"kind": "received"}),
                ("/shipments/new", {"drive_id": drive_id}),
            ):
                self.assertIn("required", client.get(route).text)
                before = client.get(location).data
                shipments = client.get("/shipments").data
                for value in (
                    "",
                    "   ",
                    "x" * 121,
                    "Synthetic\noperator",
                    "\x00Synthetic",
                    "Synthetic\u2028operator",
                    "Synthetic\u2029operator",
                ):
                    with self.subTest(route=route, value=repr(value)):
                        response = client.post(
                            route, data={"csrf": token(client), "actor": value, **fields}
                        )
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(before, client.get(location).data)
                        self.assertEqual(shipments, client.get("/shipments").data)
                result = client.post(
                    route, data={"csrf": token(client), "actor": " Synthetic <operator> ", **fields}
                )
                self.assertEqual(result.status_code, 303)
                page = client.get(result.headers["Location"]).text
                self.assertIn("Synthetic &lt;operator&gt;", page)
                self.assertIn("not authenticated", page)
            restarted = create_app(
                database, custody_actor_policy="self-declared-required"
            ).test_client()
            self.assertIn("Synthetic &lt;operator&gt;", restarted.get(location).text)
            self.assertNotIn("G2 unresolved", restarted.get(location).text)
