import re
import tempfile
import unittest
from pathlib import Path

from app import create_app


def token(client, url="/new"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(url).get_data(as_text=True)).group(
        1
    )


class InventoryTest(unittest.TestCase):
    def test_create_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "inventory.sqlite")
            client = create_app(path).test_client()
            response = client.post(
                "/new", data={"csrf": token(client), "description": "synthetic blue drive"}
            )
            self.assertEqual(response.status_code, 303)
            location = response.headers["Location"]
            self.assertRegex(location, r"/drive/t6d_[0-9a-f]{32}")
            reopened = create_app(path).test_client().get(location)
            self.assertIn("synthetic blue drive", reopened.get_data(as_text=True))

    def test_facts_validation_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            response = client.post(
                "/new",
                data={
                    "csrf": csrf,
                    "description": "synthetic box",
                    "serial": " ab  c [old]",
                    "suspected_serial": "candidate",
                    "capacity_tb": "2.5",
                    "form_factor": "portable",
                    "needs_review": "on",
                },
            )
            self.assertEqual(response.status_code, 303)
            detail = client.get(response.headers["Location"]).get_data(as_text=True)
            self.assertIn("AB C", detail)
            self.assertIn("CANDIDATE", detail)
            self.assertIn("2.5", detail)
            self.assertIn("uncertain", client.get("/?q=candidate").get_data(as_text=True).lower())
            self.assertNotIn(
                "synthetic box", client.get("/?serial=candidate").get_data(as_text=True)
            )
            self.assertEqual(client.post("/new", data={"csrf": csrf}).status_code, 400)
            self.assertEqual(
                client.post(
                    "/new", data={"csrf": csrf, "description": "x", "capacity_tb": "nan"}
                ).status_code,
                400,
            )

    def test_edits_promotion_and_security(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            first = client.post(
                "/new", data={"csrf": csrf, "serial": "taken", "description": "first"}
            ).headers["Location"]
            second = client.post(
                "/new", data={"csrf": csrf, "suspected_serial": "TAKEN", "description": "second"}
            ).headers["Location"]
            self.assertEqual(client.post(second + "/promote", data={"csrf": csrf}).status_code, 400)
            self.assertIn("TAKEN", client.get(second).get_data(as_text=True))
            self.assertEqual(
                client.post(
                    second + "/edit",
                    data={"csrf": csrf, "serial": "taken", "description": "changed"},
                ).status_code,
                400,
            )
            self.assertIn("second", client.get(second).get_data(as_text=True))
            self.assertEqual(client.post(first + "/edit", data={"csrf": csrf}).status_code, 400)
            self.assertEqual(client.post("/new", data={"description": "blocked"}).status_code, 403)
            self.assertEqual(
                client.post(
                    "/new",
                    data={"csrf": csrf, "description": "blocked"},
                    headers={"Origin": "http://evil.test"},
                ).status_code,
                403,
            )
            self.assertEqual(client.get("/new", headers={"Host": "evil.test"}).status_code, 400)
            self.assertEqual(client.get(second + "/promote").status_code, 405)
            escaped = client.post(
                "/new", data={"csrf": csrf, "description": "<script>bad</script>"}
            ).headers["Location"]
            self.assertNotIn("<script>bad</script>", client.get(escaped).get_data(as_text=True))

    def test_invalid_dates_form_factors_and_removal_of_only_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            for extra in (
                {"capacity_tb": "inf"},
                {"capacity_tb": "0"},
                {"capacity_tb": "-1"},
                {"purchase_date": "2024-02-30"},
                {"form_factor": "invented"},
                {"serial": "[note]"},
            ):
                self.assertEqual(
                    client.post(
                        "/new", data={"csrf": csrf, "description": "fixture", **extra}
                    ).status_code,
                    400,
                )
            location = client.post("/new", data={"csrf": csrf, "serial": "only"}).headers[
                "Location"
            ]
            self.assertEqual(client.post(location + "/edit", data={"csrf": csrf}).status_code, 400)
            self.assertIn("ONLY", client.get(location).get_data(as_text=True))

    def test_full_facts_restart_and_successful_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            client = create_app(path).test_client()
            csrf = token(client)
            fields = {
                "label": "label synthetic",
                "description": "description synthetic",
                "brand": "brand synthetic",
                "model": "model synthetic",
                "capacity_tb": "3.5",
                "format": "filesystem synthetic",
                "form_factor": "powered desktop",
                "owner_studio_id": "studio synthetic",
                "purchase_date": "2022-04-03",
                "warranty": "warranty synthetic",
                "remarks": "remarks synthetic",
                "review_note": "note synthetic",
                "needs_review": "on",
                "suspected_serial": "candidate",
            }
            location = client.post("/new", data={"csrf": csrf, **fields}).headers["Location"]
            self.assertEqual(
                client.post(location + "/promote", data={"csrf": csrf}).status_code, 303
            )
            restarted = create_app(path).test_client()
            page = restarted.get(location).get_data(as_text=True)
            for value in fields.values():
                if value not in ("on", "candidate"):
                    self.assertIn(value, page)
            self.assertIn("CANDIDATE", page)
            self.assertIn(location.split("/")[-1], page)
            self.assertIn(
                "description synthetic", restarted.get("/?serial=candidate").get_data(as_text=True)
            )
            self.assertIn(
                "description synthetic", restarted.get("/?review=yes").get_data(as_text=True)
            )
            self.assertNotIn(
                "description synthetic", restarted.get("/?review=no").get_data(as_text=True)
            )

    def test_concurrent_accepted_serial_claims_are_atomic(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(str(Path(directory) / "db"))

            def submit(number):
                client = app.test_client()
                csrf = token(client)
                return client.post(
                    "/new",
                    data={
                        "csrf": csrf,
                        "serial": "race synthetic",
                        "description": f"drive {number}",
                    },
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, [1, 2]))
            self.assertEqual(sorted(results), [303, 400])
            page = app.test_client().get("/?serial=race%20synthetic").get_data(as_text=True)
            self.assertEqual(page.count("RACE SYNTHETIC"), 1)

    def test_edit_corrects_facts_and_retains_id(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            location = client.post(
                "/new",
                data={"csrf": csrf, "serial": "old serial", "description": "old description"},
            ).headers["Location"]
            response = client.post(
                location + "/edit",
                data={
                    "csrf": csrf,
                    "serial": "new serial (note)",
                    "description": "new description",
                    "brand": "new brand",
                },
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["Location"], location)
            page = client.get(location).get_data(as_text=True)
            self.assertIn("NEW SERIAL", page)
            self.assertIn("new brand", page)
            self.assertNotIn("old description", page)
            self.assertNotIn(
                "new description", client.get("/?serial=old%20serial").get_data(as_text=True)
            )
            self.assertIn(
                "new description", client.get("/?serial=new%20serial").get_data(as_text=True)
            )

    def test_validation_error_does_not_echo_submitted_private_text(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            result = client.post(
                "/new",
                data={
                    "csrf": csrf,
                    "description": "synthetic private marker",
                    "capacity_tb": "invalid",
                },
            )
            self.assertEqual(result.status_code, 400)
            self.assertNotIn("synthetic private marker", result.get_data(as_text=True))
