"""Release form, shipment list and manifest, and the import exception list, against the test API."""

import re
import unittest
from pathlib import Path

from support import DriveAppCase


class ShipmentTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.one = self.api.project("Synthetic one")
        self.a = self.api.add_drive(
            label="Synthetic A",
            serial="SYN-A",
            description="blue",
            capacity_tb=4,
            needs_review=True,
            review_note="check",
        )
        self.b = self.api.add_drive(label="Synthetic B", suspected_serial="MAYBE-B")

    def release(self, drives, **data):
        fields = {
            "project": self.one,
            "party_kind": "studio",
            "party": "Invented Studio",
            "on": "2026-11-03",
            **data,
        }
        return self.submit("/shipments/new", drive_id=drives, **fields)

    def test_the_form_lists_every_drive_and_registered_project_and_has_no_actor_input(self):
        html = self.text("/shipments/new")
        for drive in (self.a, self.b):
            self.assertIn(f'name="drive_id" value="{drive}"', html)
        self.assertIn(f'<option value="{self.one}">Synthetic one', html)
        for gone in ("actor", "Self-declared", "project_text"):
            self.assertNotIn(gone, html)
        for asked in ("reason", "recipient"):
            self.assertIn(f'name="{asked}"', html)

    def test_one_release_is_one_shipment_with_one_released_event_per_drive_and_duplicates_collapse(
        self,
    ):
        response = self.release([self.a, self.b, self.a])
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        self.assertRegex(location, r"^/shipments/shipment-[0-9a-f]{32}$")
        for drive in (self.a, self.b):
            record = self.api.drive(drive)
            self.assertEqual([e["kind"] for e in record["events"]], ["released"])
            self.assertEqual(record["events"][0]["shipment_id"], location.rsplit("/", 1)[1])
            self.assertEqual(record["drive"]["status"], "released")
            self.assertEqual(record["events"][0]["logged_by"]["page_id"], self.api.custodian)

    def test_the_manifest_shows_each_drive_its_facts_the_release_and_can_be_printed(self):
        location = self.release([self.a, self.b]).headers["Location"]
        html = self.text(location)
        for shown in (
            "Synthetic one",
            "studio: Invented Studio",
            "Business date: 2026-11-03",
            "SYN-A",
            "MAYBE-B",
            "Synthetic A",
            "blue",
            "4.0",
            "Needs review; check",
            "Unknown serial",
            "Unknown capacity",
            "Print manifest",
        ):
            self.assertIn(shown, html)
        self.assertEqual(len(re.findall(r"<tr data-drive-id=", html)), 2)
        self.assertIn(f'data-drive-id="{self.a}"', html)

    def test_the_reason_and_the_free_text_recipient_are_sent_and_shown_on_the_manifest(self):
        location = self.release(
            [self.a], reason="loan for the edit", recipient="Invented Courier"
        ).headers["Location"]
        [event] = self.api.drive(self.a)["events"]
        self.assertEqual(event["reason"], "loan for the edit")
        html = self.text(location)
        self.assertIn("Taken by: Invented Courier", html)
        self.assertIn("Reason: loan for the edit", html)
        plain = self.text(self.release([self.b]).headers["Location"])
        self.assertNotIn("Taken by", plain)
        self.assertNotIn("Reason:", plain)

    def test_the_manifest_shows_the_facts_stored_at_release_not_the_current_ones(self):
        location = self.release([self.a]).headers["Location"]
        self.api.edit_drive(self.a, label="Renamed later", description="red", capacity_tb="8")
        html = self.text(location)
        for shown in ("Synthetic A", "blue", "4.0", "as they were at release"):
            self.assertIn(shown, html)
        for current in ("Renamed later", "red", "8.0"):
            self.assertNotIn(current, html)
        self.assertEqual(self.api.drive(self.a)["drive"]["label"], "Renamed later")

    def test_a_shipment_without_stored_facts_shows_current_facts_and_says_so(self):
        self.api.legacy_shipment(self.a, self.one, "shipment-" + "1" * 32)
        html = self.text("/shipments/shipment-" + "1" * 32)
        self.assertIn("Synthetic A", html)
        self.assertIn("not kept for this shipment", html)
        self.assertNotIn("as they were at release", html)

    def test_the_shipment_list_shows_each_shipment_with_its_party_date_project_and_count(self):
        self.assertIn("<ul></ul>", self.text("/shipments"))
        first = self.release([self.a]).headers["Location"]
        second = self.release([self.b], party="Other Studio", on="2026-11-04").headers["Location"]
        html = self.text("/shipments")
        self.assertIn(f'href="{first}"', html)
        self.assertIn("Other Studio", html)
        self.assertIn("2026-11-04", html)
        self.assertIn("Synthetic one", html)
        self.assertLess(html.index(first), html.index(second))
        self.assertIn("1 Drive", html)

    def test_refusals_are_shown_on_the_form_and_release_nothing(self):
        for drives, data, reason in (
            ([], {}, "At least one Drive"),
            ([self.a], {"party": ""}, "needs the Contact or Studio"),
            ([self.a], {"party_kind": ""}, "party kind"),
            ([self.a], {"on": "soon"}, "YYYY-MM-DD"),
            ([self.a, "t6d_" + "0" * 32], {}, "not found"),
            ([self.a], {"project": "t6p_" + "0" * 24}, "Project"),
        ):
            response = self.release(drives, **data)
            self.assertEqual(response.status_code, 400, data)
            self.assertIn(reason, response.get_data(as_text=True), data)
            self.assertIn('<form method="post">', response.get_data(as_text=True))
        for drive in (self.a, self.b):
            self.assertEqual(self.api.drive(drive)["events"], [])

    def test_releasing_a_drive_already_released_is_refused_whole_in_plain_words_and_nothing_is_queued(
        self,
    ):
        self.assertEqual(self.release([self.a]).status_code, 303)
        response = self.release([self.a, self.b])
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot release a Drive (released)", html)
        self.assertIn("Nothing was recorded", html)
        self.assertIn('<form method="post">', html)
        self.assertEqual(self.api.drive(self.b)["events"], [])
        self.assertFalse(Path(self.outbox).exists())

    def test_a_double_submit_of_one_release_form_is_one_shipment(self):
        data = {
            **self.fields("/shipments/new"),
            "drive_id": [self.a],
            "project": self.one,
            "party_kind": "contact",
            "party": "Invented Person",
        }
        first, second = (
            self.web.post("/shipments/new", data=data),
            self.web.post("/shipments/new", data=data),
        )
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(len(self.api.drive(self.a)["events"]), 1)
        self.assertEqual(self.html_count("/shipments", 'href="/shipments/shipment-'), 1)

    def html_count(self, url, needle):
        return self.text(url).count(needle)

    def test_an_unknown_shipment_is_not_found(self):
        self.assertEqual(self.web.get("/shipments/shipment-" + "0" * 32).status_code, 404)


class ExceptionsTests(DriveAppCase):
    def test_the_import_exceptions_the_api_holds_are_listed_read_only_and_escaped(self):
        self.assertIn("No import exceptions", self.text("/exceptions"))
        self.api.import_exception(
            workbook="synthetic.xlsx",
            sheet="Log",
            row=7,
            reason="movement with no serial",
            detail="<b>raw</b> text",
        )
        html = self.text("/exceptions")
        for shown in (
            "synthetic.xlsx",
            "Log",
            "7",
            "movement with no serial",
            "&lt;b&gt;raw&lt;/b&gt;",
        ):
            self.assertIn(shown, html)
        self.assertNotIn("<b>raw", html)
        self.assertNotIn("<form", html)


if __name__ == "__main__":
    unittest.main()
