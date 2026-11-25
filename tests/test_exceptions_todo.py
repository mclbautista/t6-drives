"""The Exceptions page as a to-do list, and photos on movements, against the real API."""

import re

from support import PHOTO
from test_corrections import CorrectionCase
from test_projects import load_projects

SHEET_ID = "1SyntheticSheetId" + "x" * 20


def group(html: str, name: str) -> str:
    """The markup of one group of the to-do list."""
    found = re.search(rf'<section[^>]*data-group="{name}".*?</section>', html, re.DOTALL)
    assert found is not None, name
    return found.group(0)


def count(html: str, name: str) -> int:
    found = re.search(rf'<section[^>]*data-group="{name}"[^>]*data-count="(\d+)"', html)
    assert found is not None, name
    return int(found.group(1))


class TodoListTests(CorrectionCase):
    def test_capture_approvals_live_in_inbox_and_exceptions_keeps_sheet_and_data_problems(self):
        capture = self.api.waiting_capture("IN\nSynthetic waiting case")
        self.api.import_exception(
            workbook="synthetic.xlsx", sheet="Log", row=9, reason="bad row", detail="x"
        )
        html = self.text("/exceptions")
        self.assertNotIn('data-group="photos"', html)
        self.assertNotIn("Give a photo a Drive", html)
        self.assertNotIn(f'data-capture="{capture}"', html)
        self.assertIn("bad row", html)
        self.assertIn(f'data-capture="{capture}"', self.text("/inbox"))

    def test_the_items_are_grouped_by_what_to_do_with_a_count_for_each_group(self):
        project = self.api.project("Synthetic todo project")
        self.api.waiting_capture("OUT\nSYN-NOWHERE\nR: Invented Courier", message=5)
        self.api.waiting_capture("OUT\nSYN-NOWHERE-2\nR: Invented Courier", message=6)
        self.released_drive()
        html = self.text("/exceptions")
        self.assertNotIn('data-group="photos"', html)
        self.assertEqual(count(html, "projects"), 1)
        self.assertEqual(count(html, "rows"), 0)
        self.assertEqual(count(html, "overdue"), html.count('data-item="overdue"'))
        self.assertEqual(len(re.findall(r'data-item="project"', group(html, "projects"))), 1)
        self.assertIn(f'<option value="{project}"', group(html, "projects"))
        top = re.search(r'<ul class="todo-counts">.*?</ul>', html, re.DOTALL)
        assert top is not None
        self.assertIn("1 movement without a Project", top.group(0))
        self.assertNotIn("photos to give a Drive", top.group(0))

    def test_each_item_is_one_sentence_and_one_button(self):
        self.released_drive()
        html = self.text("/exceptions")
        for name, button in (("projects", "Set Project"),):
            items = re.findall(r"<li[^>]*data-item=.*?</li>", group(html, name), re.DOTALL)
            self.assertEqual(len(items), 1, name)
            self.assertEqual(len(re.findall(r'<p class="todo-sentence">', items[0])), 1, name)
            controls = re.findall(r'class="btn[^"]*"', items[0])
            self.assertEqual(len(controls), 1, name)
            self.assertIn(button, items[0])

    def test_a_sheet_problem_names_the_tab_and_row_and_links_to_the_sheet(self):
        self.api.import_exception(
            workbook=SHEET_ID, sheet="Synthetic Show Tab", row=9, reason="bad row", detail="x"
        )
        html = self.text("/exceptions")
        self.assertEqual(count(html, "rows"), 1)
        item = group(html, "rows")
        self.assertIn("Synthetic Show Tab", item)
        self.assertIn("row 9", item)
        self.assertIn("bad row", item)
        self.assertIn(f'href="https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"', item)
        self.assertIn("Open the sheet", item)

    def test_a_sheet_that_is_not_a_google_sheet_gets_no_link(self):
        self.api.import_exception(
            workbook="synthetic.xlsx", sheet="Log", row=9, reason="bad row", detail="x"
        )
        item = group(self.text("/exceptions"), "rows")
        self.assertIn("row 9", item)
        self.assertNotIn("<a ", item)

    def test_an_item_disappears_when_it_is_done_and_the_count_drops(self):
        project = self.api.project("Synthetic todo project")
        _drive, movement = self.released_drive()
        self.assertEqual(count(self.text("/exceptions"), "projects"), 1)
        self.submit(f"/movements/{movement}/project", form="/exceptions", project=project)
        html = self.text("/exceptions")
        self.assertEqual(count(html, "projects"), 0)
        self.assertNotIn(f'data-movement="{movement}"', html)
        self.assertIn("Nothing to do.", html)

    def test_an_empty_group_says_so_and_has_no_button(self):
        html = self.text("/exceptions")
        self.assertIn("Nothing to do.", html)
        self.assertNotIn("<form", html)
        self.assertNotIn('data-group="photos"', html)


class MovementPhotoTests(CorrectionCase):
    def test_a_movement_recorded_from_transmittal_shows_its_photo_and_opens_it(self):
        drive, movement = self.released_drive()
        capture = self.movements(drive=drive)[0]["capture"]
        html = self.text(f"/movements?drive={drive}")
        found = re.search(rf'<li[^>]*id="m-{movement}".*?</li>', html, re.DOTALL)
        assert found is not None
        item = found.group(0)
        self.assertIn(f'<a class="movement-photo" href="/captures/{capture}/photo"', item)
        self.assertIn(f'<img class="thumb" src="/captures/{capture}/photo"', item)
        self.assertEqual(self.web.get(f"/captures/{capture}/photo").data, PHOTO)

    def test_a_movement_loaded_from_the_sheets_has_no_photo(self):
        load_projects(self)
        html = self.text("/movements")
        items = re.findall(r"<li[^>]*data-movement=.*?</li>", html, re.DOTALL)
        self.assertGreater(len(items), 0)
        self.assertEqual([li for li in items if 'class="thumb"' in li], [])

    def test_a_movement_in_the_needs_a_project_list_shows_its_photo_too(self):
        self.released_drive()
        item = group(self.text("/exceptions"), "projects")
        self.assertIn('<img class="thumb"', item)
