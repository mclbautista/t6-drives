"""The API down: reads say Unavailable, writes wait in the outbox, land once on recovery, and show if refused."""

import json
import re
import unittest
from pathlib import Path

from support import SECRET, DriveAppCase
from t6.client import Client

from app import create_app

READS = ("/", "/exceptions", "/shipments", "/shipments/new")


class UnavailableReadTests(DriveAppCase):
    def test_every_read_screen_says_unavailable_with_503_when_the_api_answers_5xx(self):
        drive = self.api.add_drive(label="Synthetic")
        self.api.mode = "down"
        for url in (
            *READS,
            f"/drive/{drive}",
            f"/drive/{drive}/edit",
            f"/drive/{drive}/custody",
            "/shipments/shipment-x",
        ):
            response = self.web.get(url)
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 503, url)
            self.assertIn('role="alert"', html)
            self.assertIn("unavailable", html.lower())
            self.assertNotIn("Traceback", html)
        self.assertEqual(self.web.get("/health").status_code, 204)

    def test_every_read_screen_says_unavailable_when_nothing_answers(self):
        self.api.stop()
        for url in READS:
            self.assertEqual(self.web.get(url).status_code, 503, url)

    def test_the_screens_work_again_when_the_api_is_back(self):
        self.api.mode = "down"
        self.assertEqual(self.web.get("/").status_code, 503)
        self.api.mode = "up"
        self.assertEqual(self.web.get("/").status_code, 200)

    def test_a_secret_the_api_does_not_accept_is_unavailable_and_a_write_is_refused_not_queued(
        self,
    ):
        app = create_app(
            Client(self.api.url, "wrong-" + SECRET, timeout=5), self.outbox, retry_interval=0
        )
        web = app.test_client()
        page = web.get("/new").get_data(as_text=True)
        self.assertEqual(web.get("/").status_code, 503)
        self.assertNotIn(SECRET, web.get("/").get_data(as_text=True))
        fields = dict(re.findall(r'<input type="hidden" name="(csrf|key)" value="([^"]*)"', page))
        response = web.post("/new", data={**fields, "label": "x"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("did not accept", response.get_data(as_text=True))
        self.assertFalse(Path(self.outbox).exists())


class OutboxTests(DriveAppCase):
    def queue_new(self, label="Queued drive", **data):
        response = self.submit("/new", label=label, **data)
        self.assertEqual(response.status_code, 202)
        return response

    def outbox_entries(self):
        return json.loads(Path(self.outbox).read_text())["entries"]

    def test_a_write_the_api_cannot_take_is_queued_and_the_page_says_so(self):
        self.api.stop()
        html = self.queue_new().get_data(as_text=True)
        self.assertIn("queued", html.lower())
        self.assertIn('href="/outbox"', html)
        self.assertIn("Queued drive", self.text("/outbox"))
        [entry] = self.outbox_entries()
        self.assertEqual(
            (entry["verb"], entry["state"], entry["inputs"]["label"]),
            ("drive_add", "queued", "Queued drive"),
        )
        self.assertRegex(entry["key"], r"^[0-9a-f]{32}$")

    def test_the_outbox_file_holds_no_secret_and_leaves_no_temporary_file(self):
        self.api.mode = "down"
        self.queue_new()
        self.assertNotIn(SECRET, Path(self.outbox).read_text())
        self.assertEqual(
            sorted(p.name for p in Path(self.directory).glob("outbox*")), ["outbox.json"]
        )

    def test_a_queued_write_lands_exactly_once_after_the_api_is_back_and_a_second_retry_changes_nothing(
        self,
    ):
        self.api.stop()
        self.queue_new(serial="SYN-1")
        self.assertEqual(self.web.get("/outbox").status_code, 200)  # still down: stays queued
        self.assertEqual(self.outbox_entries()[0]["state"], "queued")
        self.api.start()
        self.text("/outbox")  # the retry lands it
        drive = self.only_drive()
        self.assertEqual((drive["label"], drive["serial"]), ("Queued drive", "SYN-1"))
        self.assertEqual(self.outbox_entries(), [])
        for _ in range(2):
            self.text("/outbox")
            self.text("/")
        self.assertEqual(len(self.api.drives()), 1)

    def test_a_5xx_answer_queues_the_write_and_it_lands_once_when_the_api_recovers(self):
        self.api.mode = "down"
        self.queue_new()
        self.api.mode = "up"
        self.text("/")
        self.assertEqual(len(self.api.drives()), 1)
        self.assertEqual(self.outbox_entries(), [])

    def test_when_the_api_did_the_work_but_the_answer_was_lost_the_retry_does_not_do_it_twice(self):
        project = self.api.project("Synthetic one")
        drive = self.api.add_drive(label="Synthetic")
        url = f"/drive/{drive}/custody"
        opened = self.fields(url)
        self.api.mode = "lost"
        response = self.submit(
            url, hidden=opened, kind="received", project=project, on="2026-11-02"
        )
        self.assertEqual(response.status_code, 202)
        self.api.mode = "up"
        self.assertEqual(
            len(self.api.drive(drive)["events"]), 1
        )  # it did land, the app just never heard
        for _ in range(3):
            self.text("/outbox")
        self.assertEqual(len(self.api.drive(drive)["events"]), 1)
        self.assertEqual(self.outbox_entries(), [])

    def test_submitting_the_same_form_twice_while_down_queues_one_write(self):
        self.api.stop()
        data = {**self.fields("/new"), "label": "Twice"}
        self.assertEqual(self.web.post("/new", data=data).status_code, 202)
        self.assertEqual(self.web.post("/new", data=data).status_code, 202)
        self.assertEqual(len(self.outbox_entries()), 1)
        self.api.start()
        self.text("/outbox")
        self.assertEqual(len(self.api.drives()), 1)

    def test_other_writes_queue_too_and_wait_their_turn_in_order(self):
        project = self.api.project("Synthetic one")
        drive = self.api.add_drive(label="Synthetic")
        custody, detail, release = (
            self.fields(url)
            for url in (f"/drive/{drive}/custody", f"/drive/{drive}", "/shipments/new")
        )
        self.api.stop()
        self.assertEqual(
            self.submit(
                f"/drive/{drive}/custody", hidden=custody, kind="received", project=project
            ).status_code,
            202,
        )
        self.assertEqual(
            self.submit(
                f"/drive/{drive}/assignments",
                hidden=detail,
                project=project,
                label="A1",
                role="master",
            ).status_code,
            202,
        )
        self.assertEqual(
            self.submit(
                "/shipments/new",
                hidden=release,
                drive_id=[drive],
                project=project,
                party_kind="studio",
                party="Invented Studio",
            ).status_code,
            202,
        )
        self.assertEqual(
            [e["verb"] for e in self.outbox_entries()],
            ["drive_receive", "drive_assign", "drive_release"],
        )
        self.api.start()
        self.text("/outbox")
        record = self.api.drive(drive)
        self.assertEqual([e["kind"] for e in record["events"]], ["received", "released"])
        self.assertEqual(len(record["assignments"]), 1)

    def test_a_queued_write_the_api_refuses_later_is_shown_as_failed_with_its_reason_and_can_be_dismissed(
        self,
    ):
        self.api.stop()
        self.queue_new(label="Second", serial="SYN-1")
        self.api.start()
        held = self.api.add_drive(
            label="Holder", serial="SYN-1"
        )  # another write took the serial meanwhile
        html = self.text("/outbox")
        [entry] = self.outbox_entries()
        self.assertEqual(entry["state"], "failed")
        self.assertIn(held, entry["reason"])
        self.assertIn("Failed", html)
        self.assertIn("already recorded", html)
        self.assertIn("Dismiss", html)
        self.assertEqual(len(self.api.drives()), 1)
        for _ in range(2):  # a failed write is not tried again
            self.text("/outbox")
        self.assertEqual(len(self.api.drives()), 1)
        key = entry["key"]
        response = self.submit(f"/outbox/{key}/dismiss", form="/outbox")
        self.assertEqual((response.status_code, response.headers["Location"]), (303, "/outbox"))
        self.assertEqual(self.outbox_entries(), [])
        self.assertNotIn("already recorded", self.text("/outbox"))

    def test_a_queued_write_cannot_be_dismissed_and_an_unknown_one_is_not_found(self):
        self.api.stop()
        self.queue_new()
        key = self.outbox_entries()[0]["key"]
        self.assertEqual(self.submit(f"/outbox/{key}/dismiss", form="/new").status_code, 400)
        self.assertEqual(len(self.outbox_entries()), 1)
        self.assertEqual(
            self.submit("/outbox/" + "0" * 32 + "/dismiss", form="/new").status_code, 404
        )

    def test_an_empty_outbox_says_so(self):
        self.assertIn("Nothing is waiting", self.text("/outbox"))

    def test_a_write_waiting_when_the_app_restarts_is_still_retried(self):
        self.api.mode = "down"
        self.queue_new()
        self.api.mode = "up"
        restarted = create_app(self.api.client(), self.outbox, retry_interval=0).test_client()
        self.assertEqual(restarted.get("/outbox").status_code, 200)
        self.assertEqual(len(self.api.drives()), 1)

    def test_a_down_api_is_not_hammered_between_retry_intervals(self):
        app = create_app(self.api.client(), self.outbox, retry_interval=3600)
        web = app.test_client()
        self.api.mode = "down"
        page = web.get("/new").get_data(as_text=True)
        fields = dict(re.findall(r'<input type="hidden" name="(csrf|key)" value="([^"]*)"', page))
        self.assertEqual(web.post("/new", data={**fields, "label": "Patient"}).status_code, 202)
        self.api.mode = "up"
        web.get("/outbox")
        self.assertEqual(self.api.drives(), [])


if __name__ == "__main__":
    unittest.main()
