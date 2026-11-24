"""The "Reload now" button on Exceptions, against the real API on a temporary database.

The API reads its sheets through `configured_sources`; here that gives two small workbooks made for the test.
"""

import re
from datetime import datetime
from unittest.mock import patch

from openpyxl import Workbook  # type: ignore[import-untyped]
from support import DriveAppCase
from t6.client import Client
from t6.drives.sheets import WorkbookSheetSource
from t6.verbs import Done, DriveCutoverInput, DriveListInput, Preview

TAB = "Synthetic reload show"


class ReloadCase(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.sheet_rows: list[tuple[str, str]] = [("Synthetic sheet drive", "SYN-SHEET-1")]
        sources = patch(
            "t6.drives.sheets.configured_sources", side_effect=lambda environ: self.sources()
        )
        sources.start()
        self.addCleanup(sources.stop)

    def sources(self) -> tuple[WorkbookSheetSource, WorkbookSheetSource]:
        """The two sheets as they are now: the rows in `self.sheet_rows`, and an empty tracker."""
        workbook = Workbook()
        workbook.remove(workbook.active)
        sheet = workbook.create_sheet(TAB)
        sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER"])
        for row in self.sheet_rows:
            sheet.append(row)
        path = self.directory + "/projects.xlsx"
        workbook.save(path)
        tracker = Workbook()
        log = tracker.active
        assert log is not None
        log.title = "NOVEMBER 2026"
        log.append(["DATE", "PROJECT NAME", "LABEL", "IN/OUT", "RECEIVED BY", "REMARKS"])
        log.append(
            [datetime(2026, 11, 1), TAB, "Synthetic sheet drive", "OUT", "Invented Courier", "x"]
        )
        tracker_path = self.directory + "/tracker.xlsx"
        tracker.save(tracker_path)
        return WorkbookSheetSource(path), WorkbookSheetSource(tracker_path)

    def labels(self) -> list[str]:
        return sorted(d["label"] for d in self.api.read("drive_list", DriveListInput)["drives"])

    def go_live(self) -> None:
        client = Client(self.api.url, "synthetic-operator-secret")
        path = self.directory + "/live.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.title = TAB
        sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER"])
        workbook.save(path)
        preview = client.drive_cutover(DriveCutoverInput(workbook=path, idempotency_key="live"))
        assert isinstance(preview, Preview)
        done = client.drive_cutover(
            DriveCutoverInput(workbook=path, idempotency_key="live", confirm=preview.token)
        )
        assert isinstance(done, Done), done

    def confirm_form(self, html: str) -> dict[str, str]:
        found = re.search(r'<form[^>]*action="/exceptions/reload".*?</form>', html, re.DOTALL)
        assert found is not None
        return dict(
            re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', found.group(0))
        )


class ReloadNowTests(ReloadCase):
    def test_exceptions_offers_a_reload_now_button(self):
        html = self.text("/exceptions")
        self.assertIn('<a class="btn" href="/exceptions/reload">Reload now</a>', html)

    def test_the_summary_comes_first_and_nothing_changes_until_confirm(self):
        self.api.add_drive(label="Synthetic before reload", serial="SYN-BEFORE")
        before = self.labels()
        html = self.text("/exceptions/reload")
        self.assertIn("Reload now", html)
        self.assertIn("replaces", html)
        form = self.confirm_form(html)
        self.assertEqual(set(form), {"csrf", "key", "token"})
        self.assertEqual(self.labels(), before)

    def test_the_summary_says_what_the_last_reload_loaded(self):
        self.assertIn("No reload has run yet", self.text("/exceptions/reload"))
        form = self.confirm_form(self.text("/exceptions/reload"))
        self.web.post("/exceptions/reload", data=form)
        html = self.text("/exceptions/reload")
        self.assertIn("1 Drive", html)
        self.assertNotIn("No reload has run yet", html)

    def test_confirming_reloads_and_the_exceptions_list_refreshes(self):
        self.api.add_drive(label="Synthetic before reload", serial="SYN-BEFORE")
        self.api.import_exception(
            workbook="synthetic.xlsx", sheet="Log", row=3, reason="old problem", detail="x"
        )
        form = self.confirm_form(self.text("/exceptions/reload"))
        response = self.web.post("/exceptions/reload", data=form)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/exceptions")
        self.assertEqual(self.labels(), ["Synthetic sheet drive"])
        html = self.text("/exceptions")
        self.assertIn("Reloaded: 2 rows read, 1 Drive", html)
        self.assertNotIn("old problem", html)
        self.assertNotIn("Reloaded:", self.text("/exceptions"))

    def test_confirming_twice_is_one_reload(self):
        form = self.confirm_form(self.text("/exceptions/reload"))
        first = self.web.post("/exceptions/reload", data=form)
        second = self.web.post("/exceptions/reload", data=form)
        self.assertEqual((first.status_code, second.status_code), (303, 409))
        self.assertIn("already done", second.get_data(as_text=True))
        self.assertEqual(self.labels(), ["Synthetic sheet drive"])

    def test_a_sheet_that_changed_since_the_summary_is_not_loaded(self):
        form = self.confirm_form(self.text("/exceptions/reload"))
        self.sheet_rows.append(("Synthetic added later", "SYN-LATER"))
        response = self.web.post("/exceptions/reload", data=form)
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed since you looked", response.get_data(as_text=True))
        self.assertEqual(self.labels(), [])

    def test_a_confirm_without_the_summary_does_nothing(self):
        form = self.confirm_form(self.text("/exceptions/reload"))
        response = self.web.post("/exceptions/reload", data={**form, "token": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.labels(), [])

    def test_the_pages_are_plain(self):
        from test_plain_pages import BANNED, ID_PATTERN, visible

        form = self.confirm_form(self.text("/exceptions/reload"))
        pages = [
            self.web.get("/exceptions/reload"),
            self.web.post("/exceptions/reload", data=form, follow_redirects=True),
        ]
        for response in pages:
            text = visible(response.get_data(as_text=True))
            self.assertEqual(ID_PATTERN.findall(text), [])
            self.assertEqual([w for w in BANNED if w in text.lower()], [])


class ReloadAfterCutoverTests(ReloadCase):
    def test_in_live_mode_the_refusal_is_in_plain_words_and_nothing_runs(self):
        self.go_live()
        drive = self.api.add_drive(label="Synthetic real record", serial="SYN-REAL")
        kept = self.labels()
        self.assertIn("Synthetic real record", kept)
        response = self.web.get("/exceptions/reload")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 409)
        self.assertIn("system is live", html)
        self.assertIn("the sheets can no longer replace", html)
        self.assertNotIn('action="/exceptions/reload"', html)
        posted = self.web.post(
            "/exceptions/reload",
            data={"csrf": self.fields("/new")["csrf"], "key": "0" * 32, "token": "x"},
        )
        self.assertEqual(posted.status_code, 409)
        self.assertEqual(self.labels(), kept)
        self.assertEqual(self.api.drive(drive)["drive"]["label"], "Synthetic real record")

    def test_the_button_is_still_there_so_the_refusal_explains_itself(self):
        self.go_live()
        self.assertIn("Reload now", self.text("/exceptions"))
