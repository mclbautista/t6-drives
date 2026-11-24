"""Assignments on the Drive page: Project use, kept apart from custody."""

import unittest

from support import DriveAppCase


class AssignmentTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.one, self.two = self.api.project("Synthetic one"), self.api.project("Synthetic two")
        self.drive = self.api.add_drive(label="Synthetic A")
        self.page = "/drive/" + self.drive

    def assignments(self):
        return self.api.drive(self.drive)["assignments"]

    def add(self, **data):
        base = {"project": self.one, "label": "A1", "role": "master", "start": "2026-11-01"}
        return self.submit(self.page + "/assignments", form=self.page, **{**base, **data})

    def test_the_form_offers_registered_projects_and_the_roles_and_no_free_text_project(self):
        html = self.text(self.page)
        self.assertIn(f'<option value="{self.one}">Synthetic one', html)
        self.assertIn(f'value="{self.two}"', html)
        for role in ("master", "backup", "offline", "swing", "archive"):
            self.assertIn(f"<option>{role}</option>", html)
        self.assertNotIn("project_text", html)
        self.assertIn(
            "Project use is which Project a Drive is used for, not where the Drive is.", html
        )

    def test_adding_an_assignment_shows_it_with_its_project_label_role_and_dates(self):
        response = self.add(end="2026-12-01")
        self.assertEqual((response.status_code, response.headers["Location"]), (303, self.page))
        [held] = self.assignments()
        self.assertEqual(
            (
                held["number"],
                held["label"],
                held["role"],
                held["start"],
                held["end"],
                held["project"]["page_id"],
            ),
            (1, "A1", "master", "2026-11-01", "2026-12-01", self.one),
        )
        html = self.text(self.page)
        self.assertIn("Synthetic one", html)
        self.assertIn("Label: A1", html)
        self.assertIn("Role: master", html)
        self.assertIn("Start: 2026-11-01", html)
        self.assertIn("End: 2026-12-01", html)

    def test_a_refusal_is_shown_with_its_reason_and_adds_nothing(self):
        self.add()
        for data, reason in (
            ({}, "already has an open Project use"),
            ({"project": self.two, "label": ""}, "needs a label"),
            ({"project": self.two, "start": "not a date"}, "YYYY-MM-DD"),
            ({"project": "t6p_" + "0" * 24}, "Project"),
        ):
            response = self.add(**data)
            self.assertEqual(response.status_code, 400, data)
            self.assertIn(reason, response.get_data(as_text=True), data)
        self.assertEqual(len(self.assignments()), 1)

    def test_a_role_outside_the_vocabulary_is_refused_without_calling_the_api_to_write(self):
        self.assertEqual(self.add(role="boss").status_code, 400)
        self.assertEqual(self.assignments(), [])

    def test_ending_an_assignment_keeps_it_in_history_and_leaves_custody_alone(self):
        self.add()
        self.add(project=self.two, label="A2", role="backup")
        response = self.submit(
            f"{self.page}/assignments/1/end", form=self.page, project=self.one, end="2026-11-11"
        )
        self.assertEqual((response.status_code, response.headers["Location"]), (303, self.page))
        first, second = self.assignments()
        self.assertEqual((first["end"], first["open"], second["open"]), ("2026-11-11", False, True))
        html = self.text(self.page)
        self.assertIn("End: 2026-11-11", html)
        self.assertEqual(html.count("End Project use</button>"), 1)
        self.assertIn("Custody status: unknown", html)
        self.assertEqual(self.api.drive(self.drive)["events"], [])

    def test_ending_twice_or_before_the_start_is_refused_and_shown(self):
        self.add()
        url = f"{self.page}/assignments/1/end"
        early = self.submit(url, form=self.page, project=self.one, end="2026-10-01")
        self.assertEqual(early.status_code, 400)
        self.assertIn("cannot end", early.get_data(as_text=True))
        self.assertEqual(
            self.submit(url, form=self.page, project=self.one, end="2026-11-11").status_code, 303
        )
        again = self.submit(url, form=self.page, project=self.one, end="2026-11-12")
        self.assertEqual(again.status_code, 400)
        self.assertIn("No open Project use", again.get_data(as_text=True))
        self.assertEqual(self.assignments()[0]["end"], "2026-11-11")

    def test_a_double_submit_of_one_assignment_form_is_one_assignment(self):
        data = {**self.fields(self.page), "project": self.one, "label": "A1", "role": "master"}
        self.assertEqual(self.web.post(self.page + "/assignments", data=data).status_code, 303)
        self.assertEqual(self.web.post(self.page + "/assignments", data=data).status_code, 303)
        self.assertEqual(len(self.assignments()), 1)

    def test_an_assignment_with_project_text_only_is_shown_and_can_be_ended_by_its_number(self):
        self.api.legacy_assignment(self.drive, "SYN")
        html = self.text(self.page)
        self.assertIn("Unresolved: SYN", html)
        self.assertIn("Label: legacy", html)
        self.assertEqual(html.count("End Project use</button>"), 1)
        self.assertNotIn('name="project" value=', html)
        response = self.submit(f"{self.page}/assignments/1/end", form=self.page, end="2026-11-11")
        self.assertEqual((response.status_code, response.headers["Location"]), (303, self.page))
        [held] = self.assignments()
        self.assertEqual((held["end"], held["open"]), ("2026-11-11", False))
        self.assertNotIn("End Project use", self.text(self.page))

    def test_a_remark_on_an_assignment_is_sent_and_shown(self):
        self.assertIn('name="remarks"', self.text(self.page))
        self.add(remarks="spare for the edit suite")
        [held] = self.assignments()
        self.assertEqual(held["remarks"], "spare for the edit suite")
        self.assertIn("spare for the edit suite", self.text(self.page))

    def test_inventory_has_no_unresolved_project_text_filter(self):
        self.assertNotIn("project_text", self.text("/"))

    def test_assigning_to_an_unknown_drive_is_not_found(self):
        response = self.submit(
            "/drive/t6d_" + "0" * 32 + "/assignments",
            form=self.page,
            project=self.one,
            label="x",
            role="master",
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
