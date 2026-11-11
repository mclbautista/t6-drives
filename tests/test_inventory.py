import re
import tempfile
import unittest
from pathlib import Path

from app import create_app


def token(client, url="/new"):
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(url).get_data(as_text=True))
    assert match is not None, "CSRF form token missing"
    return match.group(1)


def edit_data(client, location, **fields):
    page = client.get(location + "/edit").get_data(as_text=True)
    revision = re.search(r'name="revision" value="([^"]+)"', page)
    return {"csrf": token(client), "revision": revision.group(1) if revision else "", **fields}


class InventoryTest(unittest.TestCase):
    def test_stale_whole_form_is_rejected_without_overwriting_new_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            csrf = token(client)
            location = client.post(
                "/new", data={"csrf": csrf, "description": "original", "brand": "original brand"}
            ).headers["Location"]
            stale = edit_data(client, location, description="stale change", brand="original brand")
            fresh = edit_data(client, location, description="original", brand="corrected brand")
            self.assertEqual(client.post(location + "/edit", data=fresh).status_code, 303)
            before = client.get(location).data
            rejected = client.post(location + "/edit", data=stale)
            self.assertEqual(rejected.status_code, 409)
            self.assertIn("Reload", rejected.get_data(as_text=True))
            self.assertEqual(client.get(location).data, before)
            self.assertEqual(
                client.post(
                    location + "/edit", data={"csrf": csrf, "description": "no revision"}
                ).status_code,
                409,
            )

    def test_unknown_ownership_is_distinct_from_explicit_t6_and_studio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            client = create_app(path).test_client()
            csrf = token(client)
            location = client.post(
                "/new", data={"csrf": csrf, "description": "ownership synthetic"}
            ).headers["Location"]
            self.assertIn("Unknown ownership", client.get(location).get_data(as_text=True))
            self.assertNotIn("<dd>T6</dd>", client.get(location).get_data(as_text=True))
            for ownership, studio, expected in (
                ("t6", "", "<dd>T6</dd>"),
                ("studio", "synthetic-studio-ref", "synthetic-studio-ref"),
                ("unknown", "", "Unknown ownership"),
            ):
                response = client.post(
                    location + "/edit",
                    data=edit_data(
                        client,
                        location,
                        description="ownership synthetic",
                        ownership=ownership,
                        owner_studio_id=studio,
                    ),
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn(
                    expected, create_app(path).test_client().get(location).get_data(as_text=True)
                )
            before = client.get(location).data
            for ownership, studio in (
                ("unknown", "must not invent"),
                ("t6", "conflicting"),
                ("studio", ""),
            ):
                response = client.post(
                    location + "/edit",
                    data=edit_data(
                        client,
                        location,
                        description="changed",
                        ownership=ownership,
                        owner_studio_id=studio,
                    ),
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(before, client.get(location).data)

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
            self.assertEqual(
                client.post(second + "/promote", data=edit_data(client, second)).status_code, 400
            )
            self.assertIn("TAKEN", client.get(second).get_data(as_text=True))
            self.assertEqual(
                client.post(
                    second + "/edit",
                    data=edit_data(client, second, serial="taken", description="changed"),
                ).status_code,
                400,
            )
            self.assertIn("second", client.get(second).get_data(as_text=True))
            self.assertEqual(
                client.post(first + "/edit", data=edit_data(client, first)).status_code, 400
            )
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
            self.assertEqual(
                client.post(location + "/edit", data=edit_data(client, location)).status_code, 400
            )
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
                "ownership": "studio",
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
                client.post(location + "/promote", data=edit_data(client, location)).status_code,
                303,
            )
            restarted = create_app(path).test_client()
            page = restarted.get(location).get_data(as_text=True)
            for value in fields.values():
                if value not in ("on", "candidate", "studio"):
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
                data=edit_data(
                    client,
                    location,
                    serial="new serial (note)",
                    description="new description",
                    brand="new brand",
                ),
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

    def test_concurrent_existing_drives_cannot_swap_accepted_serials(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            app = create_app(path)
            clients = [app.test_client(), app.test_client()]
            locations = []
            forms = []
            for number, client in enumerate(clients):
                fields = {
                    "serial": f"existing {number}",
                    "description": f"synthetic {number}",
                    "brand": f"brand {number}",
                    "remarks": f"evidence {number}",
                    "needs_review": "on",
                    "review_note": f"note {number}",
                    "suspected_serial": f"candidate {number}",
                    "capacity_tb": "2.5",
                }
                location = client.post("/new", data={"csrf": token(client), **fields}).headers[
                    "Location"
                ]
                locations.append(location)
                forms.append(edit_data(client, location, **fields))
            before = [clients[0].get(location).data for location in locations]
            barrier = Barrier(2)

            def submit(number):
                barrier.wait(timeout=10)
                return (
                    clients[number]
                    .post(
                        locations[number] + "/edit",
                        data={
                            **forms[number],
                            "serial": f"existing {1 - number}",
                            "description": "must not commit",
                            "remarks": "must not overwrite evidence",
                        },
                    )
                    .status_code
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, [0, 1]))
            self.assertEqual(results, [400, 400])
            for location, original in zip(locations, before):
                self.assertEqual(clients[0].get(location).data, original)
                restarted = create_app(path).test_client().get(location).data
                restored_facts = re.search(b"<dl>.*?</dl>", restarted)
                original_facts = re.search(b"<dl>.*?</dl>", original)
                assert restored_facts is not None and original_facts is not None
                self.assertEqual(restored_facts.group(), original_facts.group())

    def test_concurrent_existing_drives_claim_one_free_serial_without_partial_loser_edit(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(str(Path(directory) / "db"))
            clients = [app.test_client(), app.test_client()]
            locations = [
                client.post(
                    "/new",
                    data={
                        "csrf": token(client),
                        "description": f"synthetic drive {n}",
                        "serial": f"old {n}",
                        "remarks": f"preserved evidence {n}",
                    },
                ).headers["Location"]
                for n, client in enumerate(clients)
            ]
            forms = [
                edit_data(
                    client,
                    location,
                    serial="free synthetic",
                    description=f"winner {n}",
                    remarks=f"preserved evidence {n}",
                )
                for n, (client, location) in enumerate(zip(clients, locations))
            ]
            before = [clients[0].get(location).data for location in locations]
            barrier = Barrier(2)

            def submit(n):
                barrier.wait(timeout=10)
                return clients[n].post(locations[n] + "/edit", data=forms[n]).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, [0, 1]))
            self.assertEqual(sorted(results), [303, 400])
            loser = results.index(400)
            winner = results.index(303)
            self.assertEqual(clients[0].get(locations[loser]).data, before[loser])
            page = clients[0].get(locations[winner]).get_data(as_text=True)
            self.assertIn(f"winner {winner}", page)
            self.assertIn(f"preserved evidence {winner}", page)
            self.assertIn(
                locations[winner],
                clients[0].get("/?serial=free%20synthetic").get_data(as_text=True),
            )

    def test_serial_removal_retains_other_facts_identity_and_restart_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "db")
            client = create_app(path).test_client()
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "serial": "remove synthetic",
                    "description": "remaining synthetic identity",
                    "brand": "retained brand",
                },
            ).headers["Location"]
            response = client.post(
                location + "/edit",
                data=edit_data(
                    client,
                    location,
                    serial="",
                    description="remaining synthetic identity",
                    brand="retained brand",
                ),
            )
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["Location"], location)
            restarted = create_app(path).test_client()
            self.assertNotIn(
                location, restarted.get("/?serial=remove%20synthetic").get_data(as_text=True)
            )
            self.assertIn(location, restarted.get("/?q=remaining").get_data(as_text=True))
            self.assertIn(
                location, restarted.get("/?q=" + location.split("/")[-1]).get_data(as_text=True)
            )
            page = restarted.get(location).get_data(as_text=True)
            self.assertIn("remaining synthetic identity", page)
            self.assertIn("retained brand", page)
            self.assertNotIn("REMOVE SYNTHETIC", page)

    def test_stale_promotion_does_not_promote_a_replaced_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "description": "synthetic",
                    "suspected_serial": "old candidate",
                },
            ).headers["Location"]
            stale = edit_data(client, location)
            response = client.post(
                location + "/edit",
                data=edit_data(
                    client, location, description="synthetic", suspected_serial="new candidate"
                ),
            )
            self.assertEqual(response.status_code, 303)
            before = client.get(location).data
            self.assertEqual(client.post(location + "/promote", data=stale).status_code, 409)
            self.assertEqual(client.get(location).data, before)

    def test_invalid_edit_requires_reload_without_offering_a_blank_replacement_form(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(str(Path(directory) / "db")).test_client()
            location = client.post(
                "/new",
                data={
                    "csrf": token(client),
                    "description": "synthetic retained identity",
                    "serial": "synthetic retained serial",
                    "brand": "synthetic retained brand",
                    "ownership": "t6",
                },
            ).headers["Location"]
            before = client.get(location).data
            response = client.post(
                location + "/edit",
                data=edit_data(
                    client,
                    location,
                    description="synthetic rejected private marker",
                    capacity_tb="invalid",
                ),
            )
            page = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 400)
            self.assertIn("Capacity must be a finite positive number.", page)
            self.assertIn("Reload the form", page)
            self.assertNotIn("<form", page)
            self.assertNotIn('name="revision"', page)
            self.assertNotIn("synthetic rejected private marker", page)
            self.assertEqual(client.get(location).data, before)
            reloaded = client.get(location + "/edit").get_data(as_text=True)
            self.assertIn("synthetic retained brand", reloaded)
            self.assertIn("SYNTHETIC RETAINED SERIAL", reloaded)

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
