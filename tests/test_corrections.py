"""Corrections, place and ownership, and the Exceptions page, against the real API."""

import re
from datetime import date, timedelta
from typing import Any

from support import PHOTO, DriveAppCase
from t6.verbs import (
    DriveCaptureListInput,
    DriveReleaseInput,
    DriveReloadReportInput,
    MovementListInput,
)
from test_projects import load_projects


class CorrectionCase(DriveAppCase):
    def released_drive(self) -> tuple[str, str]:
        """A Drive released by a recording post: its Drive ID and the movement the post made."""
        drive = self.api.add_drive(label="Synthetic master", serial="SYN-M")
        self.api.capture("OUT Invented Courier|SYN-M")
        return drive, self.movements(drive=drive)[0]["id"]

    def movements(self, **filters: Any) -> list[dict[str, Any]]:
        return list(self.api.read("movement_list", MovementListInput, **filters)["movements"])

    def status(self, drive: str) -> str:
        return str(self.api.drive(drive)["drive"]["status"])


class CorrectMovementTests(CorrectionCase):
    def test_a_correction_changes_the_derived_status_and_keeps_the_original_visible(self):
        drive, movement = self.released_drive()
        self.assertEqual(self.status(drive), "released")
        form = self.text(f"/movements/{movement}/correct")
        for shown in ("Synthetic master", "OUT", "Invented Courier", "2026-11-02"):
            self.assertIn(shown, form)
        response = self.submit(
            f"/movements/{movement}/correct", direction="IN", place="SHELF", person="Invented Clerk"
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], f"/movements?drive={drive}")
        self.assertEqual(self.status(drive), "in custody")
        self.assertEqual(self.api.drive(drive)["drive"]["place"], "SHELF")
        log = self.movements(drive=drive)
        self.assertEqual(
            [(m["direction"], m["corrects"]) for m in log], [("OUT", None), ("IN", movement)]
        )
        html = self.text(response.headers["Location"])
        self.assertIn(f'id="m-{movement}"', html)
        self.assertIn("Invented Courier", html)
        self.assertIn("Invented Clerk", html)
        self.assertIn("Corrected", html)
        self.assertIn(f'href="#m-{movement}">Correction of', html)
        self.assertIn(f'href="#m-{log[1]["id"]}"', html)
        # Only the latest entry of a chain offers a correction.
        self.assertNotIn(f'href="/movements/{movement}/correct"', html)
        self.assertIn(f'href="/movements/{log[1]["id"]}/correct"', html)

    def test_only_what_changed_is_sent_so_the_other_facts_stay(self):
        drive, movement = self.released_drive()
        self.submit(f"/movements/{movement}/correct", on="2026-11-05")
        corrected = self.movements(drive=drive)[1]
        self.assertEqual(
            (corrected["on"], corrected["direction"], corrected["person"]),
            ("2026-11-05", "OUT", "Invented Courier"),
        )

    def test_a_correction_can_give_the_movement_another_drive(self):
        _drive, movement = self.released_drive()
        other = self.api.add_drive(label="Synthetic other", serial="SYN-O")
        self.submit(f"/movements/{movement}/correct", drive=other)
        self.assertEqual(self.status(other), "released")
        self.assertEqual(self.movements(drive=other)[0]["corrects"], movement)

    def test_a_correction_with_nothing_changed_or_a_missing_drive_is_shown_not_done(self):
        drive, movement = self.released_drive()
        response = self.submit(f"/movements/{movement}/correct")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one correction", response.get_data(as_text=True))
        response = self.submit(f"/movements/{movement}/correct", drive="t6d_" + "0" * 32)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Drive not found", response.get_data(as_text=True))
        self.assertIn("Invented Courier", response.get_data(as_text=True))
        self.assertEqual(len(self.movements(drive=drive)), 1)

    def test_an_entry_that_was_already_corrected_is_refused_by_the_api(self):
        drive, movement = self.released_drive()
        self.submit(f"/movements/{movement}/correct", direction="IN")
        response = self.submit(f"/movements/{movement}/correct", person="Invented Other")
        self.assertEqual(response.status_code, 400)
        self.assertIn("latest entry", response.get_data(as_text=True))
        self.assertEqual(len(self.movements(drive=drive)), 2)

    def test_an_unknown_movement_is_not_found(self):
        self.assertEqual(self.web.get("/movements/t6m_" + "0" * 32 + "/correct").status_code, 404)
        self.assertEqual(self.web.get("/movements/not%20an%20id/correct").status_code, 404)

    def test_the_movement_log_links_to_corrections_and_filters_by_drive(self):
        drive, movement = self.released_drive()
        other = self.api.add_drive(label="Synthetic untouched", serial="SYN-U")
        self.api.capture("OUT Invented Courier|SYN-U", message=2)
        html = self.text(f"/movements?drive={drive}")
        self.assertIn(f'href="/movements/{movement}/correct"', html)
        self.assertIn("Synthetic master", html)
        self.assertNotIn("Synthetic untouched", html)
        self.assertIn(f'href="/movements?drive={drive}"', self.text(f"/drive/{drive}"))
        self.assertIn("Synthetic untouched", self.text("/movements"))
        self.assertNotEqual(other, drive)


class PlaceAndOwnershipTests(CorrectionCase):
    def search_line(self, drive: str) -> str:
        html = self.text("/")
        found = re.search(
            r"<li>\s*<div class=\"c-name\"><a class=\"c-id\" href=\"/drive/"
            + drive
            + r"\">.*?</li>",
            html,
            re.DOTALL,
        )
        assert found is not None
        return found.group()

    def test_place_and_t6_ownership_are_set_and_cleared_through_drive_edit_and_show_on_the_search_line(
        self,
    ):
        drive = self.api.add_drive(label="Synthetic shelf drive", ownership_known=False)
        self.assertNotIn("T6 owned", self.search_line(drive))
        form = self.text(f"/drive/{drive}")
        self.assertIn('name="place"', form)
        for place in ("TABLE", "CABINET", "SHELF", "BOX"):
            self.assertIn(f'<option value="{place}"', form)
        response = self.submit(
            f"/drive/{drive}/place", form=f"/drive/{drive}", place="SHELF", ownership="t6"
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], f"/drive/{drive}")
        record = self.api.drive(drive)["drive"]
        self.assertEqual((record["office_location"], record["ownership"]), ("SHELF", "t6"))
        line = self.search_line(drive)
        self.assertIn("SHELF", line)
        self.assertIn("T6 owned", line)
        self.assertIn("T6", self.text(f"/drive/{drive}"))
        self.submit(f"/drive/{drive}/place", form=f"/drive/{drive}", place="", ownership="unknown")
        record = self.api.drive(drive)["drive"]
        self.assertEqual((record["office_location"], record["ownership"]), (None, "unknown"))
        line = self.search_line(drive)
        self.assertNotIn("SHELF", line)
        self.assertNotIn("T6 owned", line)

    def test_a_place_the_form_does_not_offer_is_refused_and_a_studio_owner_is_left_alone(self):
        drive = self.api.add_drive(label="Synthetic studio drive", owner_studio_id="studio-1")
        form = self.text(f"/drive/{drive}")
        self.assertNotIn(
            'name="ownership"',
            form.split('action="/drive/' + drive + '/place"')[1].split("</form>")[0],
        )
        self.submit(f"/drive/{drive}/place", form=f"/drive/{drive}", place="BOX")
        record = self.api.drive(drive)["drive"]
        self.assertEqual(
            (record["office_location"], record["owner_studio_id"]), ("BOX", "studio-1")
        )
        self.assertEqual(
            self.submit(f"/drive/{drive}/place", form=f"/drive/{drive}", place="ATTIC").status_code,
            400,
        )
        self.assertEqual(self.api.drive(drive)["drive"]["office_location"], "BOX")

    def test_a_drive_changed_since_the_page_was_opened_is_refused_and_shown(self):
        drive = self.api.add_drive(label="Synthetic raced drive")
        hidden = self.fields(f"/drive/{drive}")
        self.api.edit_drive(drive, remarks="changed elsewhere")
        response = self.submit(f"/drive/{drive}/place", hidden=hidden, place="TABLE")
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed", response.get_data(as_text=True))
        self.assertIsNone(self.api.drive(drive)["drive"]["office_location"])

    def test_a_place_set_while_the_api_is_down_is_queued_and_lands_once(self):
        drive = self.api.add_drive(label="Synthetic queued place")
        hidden = self.fields(f"/drive/{drive}")
        self.api.mode = "down"
        self.assertEqual(
            self.submit(f"/drive/{drive}/place", hidden=hidden, place="CABINET").status_code, 202
        )
        self.api.mode = "up"
        self.web.get("/outbox")
        self.assertEqual(self.api.drive(drive)["drive"]["office_location"], "CABINET")


class ExceptionsPageTests(CorrectionCase):
    def test_an_unresolved_capture_shows_its_photo_and_text(self):
        capture = self.api.capture("OUT Invented Courier|SYN-NOWHERE")
        html = self.text("/exceptions")
        self.assertIn("Unresolved captures", html)
        self.assertIn("OUT Invented Courier|SYN-NOWHERE", html)
        self.assertIn(f'<img class="capture-photo" src="/captures/{capture}/photo"', html)
        self.assertIn("2026-11-02", html)
        self.assertIn(f'href="/captures/{capture}/drive"', html)
        photo = self.web.get(f"/captures/{capture}/photo")
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.data, PHOTO)
        self.assertEqual(photo.headers["Content-Type"], "image/png")
        self.assertEqual(photo.headers["Cache-Control"], "no-store")
        self.assertEqual(photo.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", photo.headers["Content-Security-Policy"])

    def test_text_a_sender_typed_is_escaped(self):
        self.api.capture("OUT <b>raw</b>|SYN-NOWHERE")
        html = self.text("/exceptions")
        self.assertIn("OUT &lt;b&gt;raw&lt;/b&gt;|SYN-NOWHERE", html)
        self.assertNotIn("<b>raw", html)

    def test_the_photo_of_an_unknown_or_malformed_capture_is_not_found(self):
        self.assertEqual(self.web.get("/captures/capture_" + "0" * 32 + "/photo").status_code, 404)
        self.assertEqual(self.web.get("/captures/..%2Fx/photo").status_code, 404)

    def test_no_unresolved_captures_say_so_and_leave_no_form(self):
        html = self.text("/exceptions")
        for empty in (
            "No unresolved captures.",
            "No movements need a Project.",
            "No import exceptions.",
        ):
            self.assertIn(empty, html)
        self.assertNotIn("<form", html)

    def test_a_capture_is_given_a_drive_picked_from_search(self):
        drive = self.api.add_drive(label="Synthetic picked", serial="SYN-P")
        self.api.add_drive(label="Synthetic other", serial="SYN-X")
        capture = self.api.capture("OUT Invented Courier|SYN-NOWHERE")
        page = self.text(f"/captures/{capture}/drive")
        self.assertIn("Synthetic picked", page)
        found = self.text(f"/captures/{capture}/drive?q=SYN-P")
        self.assertIn("Synthetic picked", found)
        self.assertNotIn("Synthetic other", found)
        self.assertIn(f'name="drive" value="{drive}"', found)
        response = self.submit(
            f"/captures/{capture}/drive", form=f"/captures/{capture}/drive?q=SYN-P", drive=drive
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/exceptions")
        listed = self.api.read("drive_capture_list", DriveCaptureListInput)
        self.assertEqual(listed["captures"], [])
        self.assertIn("was given a Drive", self.text("/exceptions"))
        self.assertNotIn("was given a Drive", self.text("/exceptions"))
        # t6's worker resolves it as a normal movement.
        self.api.resolve_capture(capture, revision=1)
        self.assertEqual(self.status(drive), "released")
        self.assertEqual(self.movements(drive=drive)[0]["capture"], capture)
        self.assertNotIn(capture, self.text("/exceptions"))

    def test_a_drive_that_is_not_there_is_refused_and_shown(self):
        self.api.add_drive(label="Synthetic listed", serial="SYN-L")
        capture = self.api.capture("OUT Invented Courier|SYN-NOWHERE")
        response = self.submit(f"/captures/{capture}/drive", drive="t6d_" + "0" * 32)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Drive not found", response.get_data(as_text=True))
        self.assertIn(capture, self.text("/exceptions"))
        self.assertEqual(self.web.get("/captures/capture_" + "0" * 32 + "/drive").status_code, 404)

    def test_setting_a_project_removes_a_movement_from_the_needs_a_project_list(self):
        project = self.api.project("Synthetic needs project")
        drive, movement = self.released_drive()
        html = self.text("/exceptions")
        self.assertIn("Needs a Project", html)
        self.assertIn(f'data-movement="{movement}"', html)
        self.assertIn("Synthetic master", html)
        self.assertIn(f'<option value="{project}"', html)
        response = self.submit(
            f"/movements/{movement}/project", form="/exceptions", project=project
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/exceptions")
        self.assertNotIn(f'data-movement="{movement}"', self.text("/exceptions"))
        self.assertEqual(
            self.api.read("movement_list", MovementListInput, needs_project=True)["movements"], []
        )
        log = self.movements(drive=drive)
        self.assertEqual((log[0]["project"], log[0]["corrects"]), (None, None))
        self.assertEqual(log[1]["project"], {"page_id": project, "text": None})
        self.assertEqual(log[1]["corrects"], movement)

    def test_a_project_that_is_not_registered_is_refused_and_the_movement_stays_listed(self):
        drive, movement = self.released_drive()
        response = self.submit(f"/movements/{movement}/project", form="/exceptions", project="")
        self.assertEqual(response.status_code, 400)
        self.assertIn(f'data-movement="{movement}"', self.text("/exceptions"))
        self.assertEqual(len(self.movements(drive=drive)), 1)

    def test_unmapped_project_names_and_the_time_of_the_last_reload_come_from_its_report(self):
        self.assertIn("No reload has run.", self.text("/exceptions"))
        load_projects(self)
        html = self.text("/exceptions")
        self.assertIn("Unmapped Project names", html)
        for name in ("Synthetic text project", "Synthetic violet"):
            self.assertIn(f"<li>{name}</li>", html)
        self.assertNotIn("No reload has run.", html)
        report = self.api.read("drive_reload_report", DriveReloadReportInput)
        self.assertIn(report["report"]["at"][:10], html)

    def test_rows_the_reload_could_not_load_are_listed(self):
        self.api.import_exception(
            workbook="synthetic.xlsx", sheet="Log", row=9, reason="bad row", detail="x"
        )
        html = self.text("/exceptions")
        self.assertIn("Rows the reload could not load", html)
        self.assertIn("bad row", html)

    def test_overdue_t6_drives_are_listed_from_the_reminder_read(self):
        project = self.api.project("Synthetic overdue project")
        drive = self.api.add_drive(label="Synthetic overdue", serial="SYN-OD")
        self.assertIn("No T6 drives are overdue.", self.text("/exceptions"))
        self.api.write(
            "drive_release",
            DriveReleaseInput,
            drives=[drive],
            project=project,
            party="Invented Recipient",
            party_kind="contact",
            on=(date.today() - timedelta(days=40)).isoformat(),
        )
        html = self.text("/exceptions")
        self.assertIn("Overdue T6 drives", html)
        self.assertIn("Synthetic overdue", html)
        self.assertIn("SYN-OD", html)
        self.assertIn("Invented Recipient", html)
        self.assertIn(f'href="/drive/{drive}"', html)
        self.assertIn("40 days", html)
