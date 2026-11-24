"""Recycle on the Drive page: a new label and Project for a reused Drive, previewed before it is confirmed."""

import re
import unittest

from support import DriveAppCase


class RecycleTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.old, self.new = (
            self.api.project("Synthetic old show"),
            self.api.project("Synthetic new show"),
        )
        self.drive = self.api.add_drive(label="Synthetic old label", serial="SYN-RECYCLE-1")
        self.api.assign(self.drive, self.old, "OLD-1", "master", start="2026-01-05")
        self.page = "/drive/" + self.drive
        self.form = self.page + "/recycle"

    def preview(self, **data):
        return self.submit(self.form + "/preview", form=self.form, **data)

    def confirm(self, **data):
        """Confirm from the preview page, with the hidden fields that page carries."""
        shown = self.preview(**data).get_data(as_text=True)
        hidden = dict(re.findall(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', shown))
        return self.web.post(self.form, data=hidden)

    def test_the_drive_page_has_a_recycle_button_to_a_form_with_the_registered_projects_by_name(
        self,
    ):
        self.assertIn(f'href="{self.form}"', self.text(self.page))
        html = self.text(self.form)
        self.assertIn('name="label"', html)
        self.assertIn(f'<option value="{self.new}">Synthetic new show', html)

    def test_the_preview_names_what_will_change_and_changes_nothing(self):
        before = self.api.drive(self.drive)
        shown = self.preview(project=self.new, label="NEW-1").get_data(as_text=True)
        for text in ("Synthetic old label", "NEW-1", "Synthetic new show", "Synthetic old show"):
            self.assertIn(text, shown)
        self.assertIn(f'action="{self.form}"', shown)
        self.assertEqual(self.api.drive(self.drive), before)

    def test_confirming_gives_the_new_label_and_project_and_keeps_the_old_label_in_the_history(
        self,
    ):
        response = self.confirm(project=self.new, label="NEW-1")
        self.assertEqual((response.status_code, response.headers["Location"]), (303, self.page))
        held = self.api.drive(self.drive)
        self.assertEqual(held["drive"]["label"], "NEW-1")
        old, new = held["assignments"]
        self.assertEqual((old["label"], old["open"]), ("OLD-1", False))
        self.assertEqual(
            (new["label"], new["open"], new["project"]["page_id"]), ("NEW-1", True, self.new)
        )
        html = self.text(self.page)
        self.assertIn("Label: OLD-1", html)
        self.assertIn("Previously labelled", html)

    def test_a_blank_label_or_the_same_label_and_project_is_refused_in_plain_words(self):
        before = self.api.drive(self.drive)
        for data in (
            {"label": "  ", "project": self.new},
            {"label": "Synthetic old label", "project": self.old},
        ):
            response = self.preview(**data)
            self.assertEqual(response.status_code, 400)
            self.assertIn('role="alert"', response.get_data(as_text=True))
        self.assertEqual(self.api.drive(self.drive), before)

    def test_a_retired_drive_is_refused(self):
        self.submit(
            self.page + "/retire", kind="retired", project=self.old, reason="dead", on="2026-03-01"
        )
        response = self.preview(project=self.new, label="NEW-1")
        self.assertEqual(response.status_code, 409)
        self.assertIn("retired", response.get_data(as_text=True))

    def test_a_confirm_made_while_the_api_is_down_waits_and_lands_once(self):
        shown = self.preview(project=self.new, label="NEW-1").get_data(as_text=True)
        hidden = dict(re.findall(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', shown))
        self.api.mode = "down"
        self.assertEqual(self.web.post(self.form, data=hidden).status_code, 202)
        self.api.mode = "up"
        self.web.get("/health")
        self.web.get("/")
        self.assertEqual(self.api.drive(self.drive)["drive"]["label"], "NEW-1")
        self.assertEqual(len(self.api.drive(self.drive)["assignments"]), 2)

    def test_an_unknown_drive_is_a_404(self):
        self.assertEqual(self.web.get("/drive/t6d_missing/recycle").status_code, 404)


if __name__ == "__main__":
    unittest.main()
