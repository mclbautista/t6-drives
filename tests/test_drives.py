"""New, edit, promote and detail screens against the test API."""

import re
import unittest
from typing import Any

from support import DriveAppCase


class NewDriveTests(DriveAppCase):
    def test_create_registers_one_drive_with_every_fact_and_opens_it(self):
        response = self.submit(
            "/new",
            label="Synthetic box",
            serial=" ab  c [old]",
            suspected_serial="candidate",
            description="blue",
            brand="Invented Brand",
            model="M1",
            capacity_tb="2.5",
            format="exFAT",
            form_factor="portable",
            purchase_date="2025-05-06",
            warranty="2 years",
            remarks="synthetic remark",
            needs_review="on",
            review_note="look",
        )
        self.assertEqual(response.status_code, 303)
        drive = self.only_drive()
        self.assertEqual(response.headers["Location"], "/drive/" + drive["id"])
        self.assertRegex(drive["id"], r"^t6d_[0-9a-f]{32}$")
        self.assertEqual(
            (
                drive["serial"],
                drive["suspected_serial"],
                drive["capacity_tb"],
                drive["form_factor"],
            ),
            ("AB C", "CANDIDATE", 2.5, "portable"),
        )
        self.assertEqual(
            (drive["purchase_date"], drive["needs_review"], drive["brand"]),
            ("2025-05-06", True, "Invented Brand"),
        )
        detail = self.text(response.headers["Location"])
        for shown in (
            "Synthetic box",
            "AB C",
            "CANDIDATE",
            "2.5",
            "Invented Brand",
            "exFAT",
            "2025-05-06",
            "look",
        ):
            self.assertIn(shown, detail)

    def test_ownership_unknown_is_distinct_from_t6_and_studio(self):
        for ownership, studio, shown, known, owner in (
            ("unknown", "", "Unknown ownership", "unknown", None),
            ("t6", "", "<dd>T6</dd>", "t6", None),
            (
                "studio",
                "synthetic-studio-ref",
                "synthetic-studio-ref",
                "studio",
                "synthetic-studio-ref",
            ),
        ):
            before = {d["id"] for d in self.api.drives()}
            response = self.submit(
                "/new", label="Owner " + ownership, ownership=ownership, owner_studio_id=studio
            )
            self.assertEqual(response.status_code, 303, ownership)
            drive = self.api.drive(response.headers["Location"].rsplit("/", 1)[1])["drive"]
            self.assertNotIn(drive["id"], before)
            self.assertEqual((drive["ownership"], drive["owner_studio_id"]), (known, owner))
            self.assertIn(shown, self.text(response.headers["Location"]))
            self.assertNotIn(
                "Unknown ownership" if ownership != "unknown" else "<dd>T6</dd>",
                self.text(response.headers["Location"]),
            )

    def test_conflicting_ownership_and_unreadable_numbers_are_refused_on_the_form_and_nothing_is_created(
        self,
    ):
        cases: list[dict[str, Any]] = [
            {"ownership": "unknown", "owner_studio_id": "must not invent"},
            {"ownership": "t6", "owner_studio_id": "conflicting"},
            {"ownership": "studio", "owner_studio_id": ""},
            {"capacity_tb": "nan"},
            {"capacity_tb": "-1"},
            {"capacity_tb": "big"},
        ]
        for entered in cases:
            response = self.submit("/new", label="Synthetic", **entered)
            self.assertEqual(response.status_code, 400, entered)
            self.assertIn('role="alert"', response.get_data(as_text=True))
        self.assertEqual(self.api.drives(), [])

    def test_the_apis_refusal_is_shown_on_the_form_with_what_was_typed(self):
        response = self.submit("/new", label="", description="no label", brand="Typed Brand")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 400)
        self.assertIn('<p role="alert">A Drive needs a label</p>', html)
        self.assertIn('value="Typed Brand"', html)
        self.assertEqual(self.api.drives(), [])

    def test_a_serial_already_recorded_opens_the_drive_that_holds_it(self):
        held = self.api.add_drive(label="Holder", serial="SYN-1")
        response = self.submit("/new", label="Second", serial="syn-1")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/drive/" + held)
        self.assertEqual(len(self.api.drives()), 1)

    def test_a_double_submit_of_one_rendered_form_is_one_drive(self):
        data = {**self.fields("/new"), "label": "Once"}
        first, second = self.web.post("/new", data=data), self.web.post("/new", data=data)
        self.assertEqual((first.status_code, second.status_code), (303, 303))
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertEqual(len(self.api.drives()), 1)

    def test_each_rendered_form_has_its_own_key(self):
        keys = {self.fields("/new")["key"] for _ in range(3)}
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{32}", key) for key in keys))

    def test_a_missing_or_malformed_key_is_not_accepted(self):
        csrf = self.fields("/new")["csrf"]
        for data in ({}, {"key": ""}, {"key": "not hex!"}):
            self.assertEqual(
                self.web.post("/new", data={"csrf": csrf, "label": "x", **data}).status_code, 400
            )
        self.assertEqual(self.api.drives(), [])

    def test_a_form_without_the_csrf_token_is_forbidden(self):
        self.assertEqual(
            self.web.post("/new", data={"label": "x", "key": "a" * 32}).status_code, 403
        )


class DriveDetailTests(DriveAppCase):
    def test_detail_shows_facts_status_and_ownership_and_links(self):
        drive = self.api.add_drive(label="Synthetic one", serial="SYN-9", ownership_known=False)
        html = self.text("/drive/" + drive)
        for shown in (
            f"Drive {drive}",
            "Synthetic one",
            "SYN-9",
            "Custody status: unknown",
            "Unknown ownership",
            "Needs review</dt><dd>No",
        ):
            self.assertIn(shown, html)
        for link in ("/", f"/drive/{drive}/edit", f"/drive/{drive}/retire"):
            self.assertIn(f'href="{link}"', html)

    def test_an_unknown_drive_is_not_found_and_so_are_its_subpages(self):
        for suffix in ("", "/edit", "/custody"):
            self.assertEqual(self.web.get("/drive/t6d_" + "0" * 32 + suffix).status_code, 404)

    def test_text_from_the_api_is_escaped(self):
        drive = self.api.add_drive(label="<b>bold</b>", remarks="<i>x</i>")
        html = self.text("/drive/" + drive)
        self.assertNotIn("<b>bold", html)
        self.assertIn("&lt;b&gt;bold", html)


FACTS = (
    "label",
    "serial",
    "suspected_serial",
    "review_note",
    "form_factor",
    "description",
    "brand",
    "model",
    "capacity_tb",
    "format",
    "owner_studio_id",
    "purchase_date",
    "warranty",
    "remarks",
    "needs_review",
)


class EditDriveTests(DriveAppCase):
    def drive(self, **facts):
        return self.api.add_drive(**{"label": "Synthetic one", **facts})

    def test_edit_form_is_filled_from_the_drive_and_carries_every_fact(self):
        drive = self.drive(
            serial="SYN-1",
            needs_review=True,
            review_note="n",
            form_factor="portable",
            brand="Invented Brand",
            capacity_tb=2.5,
            ownership_known=True,
            warranty="2 years",
        )
        html = self.text(f"/drive/{drive}/edit")
        for name in (*FACTS, "ownership"):
            self.assertIn(f'name="{name}"', html)
        for value in ("Synthetic one", "SYN-1", "Invented Brand", "2.5", "2 years"):
            self.assertIn(f'value="{value}"', html)
        self.assertIn('<option value="t6" selected>', html)
        self.assertIn("checked", html)
        version = self.api.drive(drive)["drive"]["version"]
        self.assertIn(f'<input type="hidden" name="expected_version" value="{version}">', html)

    def test_edit_changes_every_fact_and_a_blank_clears_one(self):
        drive = self.drive(
            serial="SYN-1", description="old", capacity_tb=2.5, brand="Old Brand", remarks="old"
        )
        data: dict[str, Any] = {
            "label": "Corrected label",
            "serial": "SYN-2",
            "suspected_serial": "cand",
            "review_note": "look",
            "form_factor": "powered desktop",
            "description": "new",
            "brand": "",
            "model": "M2",
            "capacity_tb": "",
            "format": "APFS",
            "ownership": "studio",
            "owner_studio_id": "synthetic-studio-ref",
            "purchase_date": "2025-05-06",
            "warranty": "3 years",
            "remarks": "",
            "needs_review": "on",
        }
        response = self.submit(f"/drive/{drive}/edit", **data)
        self.assertEqual(
            (response.status_code, response.headers["Location"]), (303, "/drive/" + drive)
        )
        after = self.api.drive(drive)["drive"]
        self.assertEqual(
            {name: after[name] for name in (*FACTS, "ownership")},
            {
                **{name: data[name] or None for name in FACTS},
                "serial": "SYN-2",
                "suspected_serial": "CAND",
                "needs_review": True,
                "ownership": "studio",
            },
        )
        self.assertEqual(len(self.api.drives()), 1)

    def test_ownership_can_be_set_back_to_unknown_or_t6_and_a_conflict_is_refused(self):
        drive = self.drive(ownership_known=True, owner_studio_id="synthetic-studio-ref")
        for ownership, studio, status in (
            ("t6", "", 303),
            ("unknown", "", 303),
            ("unknown", "must not invent", 400),
            ("studio", "", 400),
        ):
            response = self.submit(
                f"/drive/{drive}/edit",
                label="Synthetic one",
                ownership=ownership,
                owner_studio_id=studio,
            )
            self.assertEqual(response.status_code, status, (ownership, studio))
        after = self.api.drive(drive)["drive"]
        self.assertEqual((after["ownership"], after["owner_studio_id"]), ("unknown", None))

    def test_an_unreadable_capacity_is_refused_on_the_form_and_changes_nothing(self):
        drive = self.drive(capacity_tb=2)
        for capacity in ("nan", "-1", "big"):
            response = self.submit(
                f"/drive/{drive}/edit", label="Synthetic one", capacity_tb=capacity
            )
            self.assertEqual(response.status_code, 400, capacity)
        self.assertEqual(self.api.drive(drive)["drive"]["capacity_tb"], 2.0)

    def test_an_edit_of_a_drive_that_changed_since_the_form_was_opened_is_refused_and_the_form_reloaded(
        self,
    ):
        drive = self.drive(brand="Old Brand")
        url = f"/drive/{drive}/edit"
        opened = self.fields(url)
        self.api.edit_drive(drive, brand="Their Brand")
        response = self.submit(url, hidden=opened, label="Mine", brand="My Brand")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn("The Drive changed since you opened this form", html)
        self.assertIn('value="Their Brand"', html)  # the form is the Drive as it is now
        self.assertNotIn("My Brand", html)
        self.assertEqual(self.api.drive(drive)["drive"]["label"], "Synthetic one")
        again = self.fields(url)
        self.assertNotEqual(again["key"], opened["key"])
        reloaded = self.submit(url, hidden=again, label="Mine", brand="Their Brand")
        self.assertEqual(reloaded.status_code, 303)
        self.assertEqual(self.api.drive(drive)["drive"]["label"], "Mine")

    def test_edit_changes_the_named_facts_and_keeps_the_id_and_history(self):
        drive = self.drive(serial="SYN-1", needs_review=True, review_note="check")
        response = self.submit(
            f"/drive/{drive}/edit",
            label="Corrected label",
            serial="SYN-2",
            suspected_serial="",
            review_note="",
            form_factor="powered desktop",
        )
        self.assertEqual(
            (response.status_code, response.headers["Location"]), (303, "/drive/" + drive)
        )
        after = self.api.drive(drive)["drive"]
        self.assertEqual(
            (
                after["label"],
                after["serial"],
                after["needs_review"],
                after["review_note"],
                after["form_factor"],
            ),
            ("Corrected label", "SYN-2", False, None, "powered desktop"),
        )
        self.assertEqual(len(self.api.drives()), 1)

    def test_a_serial_held_by_another_drive_is_refused_on_the_form_and_changes_nothing(self):
        self.drive(serial="SYN-1")
        other = self.drive(serial="SYN-2", label="Other")
        response = self.submit(f"/drive/{other}/edit", label="Other", serial="syn-1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already recorded", response.get_data(as_text=True))
        self.assertEqual(self.api.drive(other)["drive"]["serial"], "SYN-2")

    def test_clearing_every_identifying_fact_is_refused_by_the_api_and_shown(self):
        drive = self.drive(label="Only label")
        response = self.submit(f"/drive/{drive}/edit", label="", serial="")
        self.assertEqual(response.status_code, 400)
        self.assertIn('role="alert"', response.get_data(as_text=True))
        self.assertEqual(self.api.drive(drive)["drive"]["label"], "Only label")

    def test_a_double_submit_of_an_edit_is_one_edit(self):
        drive = self.drive()
        data = {**self.fields(f"/drive/{drive}/edit"), "label": "Edited"}
        self.assertEqual(self.web.post(f"/drive/{drive}/edit", data=data).status_code, 303)
        self.assertEqual(self.web.post(f"/drive/{drive}/edit", data=data).status_code, 303)
        self.assertEqual(self.api.drive(drive)["drive"]["label"], "Edited")


class PromoteTests(DriveAppCase):
    def test_promotion_is_explicit_and_moves_the_suspected_serial_to_accepted(self):
        drive = self.api.add_drive(label="Synthetic", suspected_serial="cand 1")
        page = f"/drive/{drive}"
        self.assertIn("Promote suspected serial to accepted", self.text(page))
        self.assertEqual(self.api.drive(drive)["drive"]["serial"], None)
        response = self.submit(page + "/promote", form=page)
        self.assertEqual((response.status_code, response.headers["Location"]), (303, page))
        after = self.api.drive(drive)["drive"]
        self.assertEqual((after["serial"], after["suspected_serial"]), ("CAND 1", None))
        self.assertNotIn("Promote suspected serial to accepted", self.text(page))

    def test_a_serial_taken_by_another_drive_cannot_be_promoted(self):
        self.api.add_drive(label="Holder", serial="CAND 1")
        drive = self.api.add_drive(label="Synthetic", suspected_serial="cand 1")
        response = self.submit(f"/drive/{drive}/promote", form=f"/drive/{drive}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("already recorded", response.get_data(as_text=True))
        self.assertEqual(self.api.drive(drive)["drive"]["suspected_serial"], "CAND 1")

    def test_promoting_when_there_is_no_candidate_is_refused(self):
        drive = self.api.add_drive(label="Synthetic", serial="SYN-1")
        page = self.submit(f"/drive/{drive}/promote", form="/new")
        self.assertEqual(page.status_code, 400)
        self.assertIn("no suspected serial", page.get_data(as_text=True))

    def test_promoting_an_unknown_drive_is_not_found(self):
        drive = self.api.add_drive(label="Synthetic", suspected_serial="x1")
        response = self.submit("/drive/t6d_" + "0" * 32 + "/promote", form=f"/drive/{drive}")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
