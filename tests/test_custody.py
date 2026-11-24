"""Custody screen: events are appended through the API as the custodian the API authenticates."""

import unittest
from pathlib import Path

from support import DriveAppCase
from t6.verbs import DriveReleaseInput


class CustodyTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.one, self.two = self.api.project("Synthetic one"), self.api.project("Synthetic two")
        self.drive = self.api.add_drive(label="Synthetic A")
        self.url = f"/drive/{self.drive}/custody"

    def events(self):
        return self.api.drive(self.drive)["events"]

    def record(self, kind, **data):
        return self.submit(self.url, **{"kind": kind, "project": self.one, **data})

    def test_the_form_has_the_three_kinds_a_project_choice_and_no_actor_or_correction_input(self):
        html = self.text(self.url)
        for kind in ("received", "returned", "retired"):
            self.assertIn(f"<option>{kind}</option>", html)
        self.assertIn(f'<option value="{self.one}">Synthetic one', html)
        for gone in ("actor", "correction_of", "recipient", "project_text", "Self-declared"):
            self.assertNotIn(gone, html)

    def test_receive_return_and_retire_append_events_and_status_follows_the_log(self):
        self.api.write("drive_release", DriveReleaseInput, **self.release())
        for kind, status in (
            ("returned", "in custody"),
            ("retired", "retired"),
        ):
            data = (
                {"on": "2026-11-02", "reason": "dead"}
                if kind == "retired"
                else {"on": "2026-11-02"}
            )
            response = self.record(kind, **data)
            self.assertEqual(
                (response.status_code, response.headers["Location"]),
                (303, "/drive/" + self.drive),
                kind,
            )
            self.assertIn("Custody status: " + status, self.text("/drive/" + self.drive))
        self.assertEqual([e["kind"] for e in self.events()], ["released", "returned", "retired"])
        self.assertEqual(self.events()[2]["reason"], "dead")
        self.assertEqual(self.events()[0]["project"]["page_id"], self.two)

    def release(self):
        return {
            "drives": [self.drive],
            "project": self.two,
            "party": "Invented Studio",
            "party_kind": "studio",
        }

    def test_a_step_that_cannot_follow_the_last_one_is_shown_in_plain_words_and_nothing_is_queued(
        self,
    ):
        self.record("received")
        response = self.record("received")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot receive a Drive (in custody)", html)
        self.assertIn("Nothing was recorded", html)
        self.assertEqual([e["kind"] for e in self.events()], ["received"])
        self.assertFalse(Path(self.outbox).exists())

    def test_every_kind_of_step_that_cannot_follow_is_refused(self):
        for kind, data, status in (
            ("returned", {}, 409),
            ("received", {}, 303),
            ("retired", {"reason": "dead"}, 303),
            ("retired", {"reason": "again"}, 409),
            ("received", {}, 409),
            ("returned", {}, 409),
        ):
            self.assertEqual(self.record(kind, **data).status_code, status, kind)
        self.assertEqual([e["kind"] for e in self.events()], ["received", "retired"])

    def test_history_lists_every_event_in_append_order_with_project_date_and_reason(self):
        self.record("received", on="2026-11-02")
        self.submit(self.url, kind="retired", project=self.two, reason="lost in transit")
        html = self.text("/drive/" + self.drive)
        first, second = html.index("Movement 1: received"), html.index("Movement 2: retired")
        self.assertLess(first, second)
        self.assertIn("Business date: 2026-11-02", html)
        self.assertIn("Synthetic two", html[second:])
        self.assertIn("Reason: lost in transit", html)
        self.assertNotIn("Append correction", html)

    def test_the_actor_is_the_custodian_the_api_authenticates_whatever_the_form_says(self):
        self.record("received", actor="Someone Else")
        [event] = self.events()
        self.assertEqual(event["logged_by"]["page_id"], self.api.custodian)
        self.assertNotIn("Someone Else", self.text("/drive/" + self.drive))

    def test_a_party_is_recorded_with_its_kind(self):
        self.api.write("drive_release", DriveReleaseInput, **self.release())
        self.record("returned", party_kind="studio", party="Invented Studio")
        event = self.events()[-1]
        self.assertEqual(
            (event["party"]["kind"], event["party"]["ref"]["text"]), ("studio", "Invented Studio")
        )
        self.assertIn("studio: Invented Studio", self.text("/drive/" + self.drive))

    def test_refusals_are_shown_with_their_reason_and_append_nothing(self):
        for kind, data, reason in (
            ("retired", {}, "needs a reason"),
            ("received", {"on": "13/13/2026"}, "YYYY-MM-DD"),
            ("received", {"party": "Invented Studio"}, "party_kind"),
            ("received", {"project": "t6p_" + "0" * 24}, "Project"),
        ):
            response = self.record(kind, **data)
            self.assertEqual(response.status_code, 400, data)
            self.assertIn(reason, response.get_data(as_text=True), data)
        self.assertEqual(self.events(), [])

    def test_an_unknown_kind_or_drive_is_not_accepted(self):
        self.assertEqual(self.record("released").status_code, 400)
        self.assertEqual(self.record("vanished").status_code, 400)
        missing = self.submit(
            "/drive/t6d_" + "0" * 32 + "/custody", form=self.url, kind="received", project=self.one
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(self.events(), [])

    def test_a_double_submit_of_one_form_is_one_event(self):
        data = {**self.fields(self.url), "kind": "received", "project": self.one}
        self.assertEqual(self.web.post(self.url, data=data).status_code, 303)
        self.assertEqual(self.web.post(self.url, data=data).status_code, 303)
        self.assertEqual(len(self.events()), 1)

    def test_text_from_the_api_is_escaped_in_history(self):
        self.record("received", party_kind="contact", party="<img src=x>")
        self.assertNotIn("<img src=x>", self.text("/drive/" + self.drive))


if __name__ == "__main__":
    unittest.main()
