"""Plain pages: no ID and no system word in what a person reads, on every page of the app.

Every route is rendered with fixture data through the real VerbAPI. The visible text is the HTML with
tags, scripts, styles and attribute values left out. IDs are allowed in links (attributes) and inside the
one collapsed "Details" line of a Drive page, which is checked on its own.
"""

import io
import json
import re
import unittest
from datetime import date, timedelta
from html.parser import HTMLParser

from support import DriveAppCase
from t6.verbs import (
    DriveCatalogSnapshotsInput,
    DriveReleaseInput,
    DriveShipmentsInput,
    MovementListInput,
)
from test_projects import load_projects

import glossary

ID_PATTERN = re.compile(r"t6[a-z]_[0-9a-f]+")
BANNED = (
    "assignment",
    "custody event",
    "accepted serial",
    "suspected serial",
    "promote",
    "outbox",
    "[person",
)


class VisibleText(HTMLParser):
    """Text a person reads: no tags, scripts, styles, attribute values or the collapsed Details line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0  # depth inside script, style or the Details line
        self.details = 0  # Details lines seen
        self.details_open = False
        self.stack: list[bool] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "img", "input", "meta", "link", "hr"):
            return
        names = dict(attrs)
        skipped = tag in ("script", "style") or (
            tag == "details" and "ids" in (names.get("class") or "").split()
        )
        if tag == "details" and skipped:
            self.details += 1
            self.details_open = self.details_open or "open" in names
        self.stack.append(skipped)
        self.skip += skipped

    def handle_endtag(self, tag):
        if tag in ("br", "img", "input", "meta", "link", "hr"):
            return
        if self.stack:
            self.skip -= self.stack.pop()

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def visible(html: str) -> str:
    parser = VisibleText()
    parser.feed(html)
    return " ".join(" ".join(parser.parts).split())


def details_of(html: str) -> VisibleText:
    parser = VisibleText()
    parser.feed(html)
    return parser


class PlainPageCase(DriveAppCase):
    """One fixture with a Drive in every state the pages show."""

    def setUp(self):
        super().setUp()
        load_projects(self)  # registered, empty and name-only Projects, movements, a reload report
        self.project = self.api.project("Synthetic Plain Show")
        self.drive = self.api.add_drive(
            label="Synthetic plain master",
            serial="SYN-PLAIN-1",
            suspected_serial="SYN-CHECK-1",
            description="blue case",
            office_location="SHELF",
            qr_code="QR-PLAIN",
            needs_review=True,
            review_note="check the label",
        )
        self.other = self.api.add_drive(label="Synthetic plain backup", serial="SYN-PLAIN-2")
        self.api.assign(self.drive, self.project, "master", "master", start="2026-10-01")
        self.api.legacy_assignment(self.other, "Synthetic text only")
        self.api.receive(self.drive, self.project)
        self.late = self.api.add_drive(label="Synthetic late", serial="SYN-LATE")
        self.api.write(
            "drive_release",
            DriveReleaseInput,
            drives=[self.late],
            project=self.project,
            party="Invented Recipient",
            party_kind="contact",
            on=(date.today() - timedelta(days=40)).isoformat(),
        )
        self.shipment = self.api.read("drive_shipments", DriveShipmentsInput)["shipments"][0][
            "shipment_id"
        ]
        # A recording post that names a Drive: a movement without a Project.
        self.moved = self.api.add_drive(label="Synthetic no project", serial="SYN-NOPROJ")
        self.api.capture("OUT Invented Courier|SYN-NOPROJ", message=1)
        # A recording post nobody could match: an unresolved capture.
        self.capture = self.api.capture("nothing readable here", message=2, day="2026-11-03")
        self.api.import_exception(
            workbook="synthetic.xlsx", sheet="Log", row=7, reason="bad row", detail="x"
        )
        scan = [{"path": "Show A/clip1.mov", "kind": "file", "size_bytes": 100}]
        self.api.scan(self.drive, "2026-11-01", scan)
        self.api.scan(
            self.drive, "2026-11-08", [*scan, {"path": "b.mov", "kind": "file", "size_bytes": 7}]
        )
        self.snapshot = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=self.drive
        )["snapshots"][-1]["id"]
        self.movement = self.api.read("movement_list", MovementListInput, drive=self.moved)[
            "movements"
        ][0]["id"]

    def routes(self) -> list[str]:
        drive = f"/drive/{self.drive}"
        return [
            "/",
            "/?q=plain",
            "/?status=released",
            "/new",
            "/projects",
            "/projects?q=syn&out=1&kind=name_only&sort=total",
            f"/projects/view?project={self.project}",
            "/projects/view?project=Synthetic%20text%20project",
            "/movements",
            f"/movements?drive={self.moved}",
            f"/movements/{self.movement}/correct",
            f"/movements/{self.movement}",
            "/exceptions",
            f"/captures/{self.capture}/drive",
            drive,
            f"/drive/{self.other}",
            f"/drive/{self.late}",
            drive + "/edit",
            drive + "/retire",
            drive + "/custody",
            drive + "/qr",
            f"/drive/{self.other}/qr",
            drive + "/catalog",
            drive + "/catalog?on=2026-11-09",
            drive + f"/catalog?diff={self.snapshot}",
            "/shipments",
            "/shipments/new",
            f"/shipments/{self.shipment}",
            "/outbox",
        ]


class EveryPageIsPlainTests(PlainPageCase):
    def test_no_page_shows_an_id_or_a_system_word(self):
        found = []
        for url in self.routes():
            response = self.web.get(url)
            self.assertEqual(response.status_code, 200, url)
            text = visible(response.get_data(as_text=True))
            self.assertGreater(len(text), 20, url)
            for match in ID_PATTERN.findall(text):
                found.append((url, match))
            lowered = text.lower()
            for word in BANNED:
                if word in lowered:
                    found.append((url, word))
        self.assertEqual(found, [])

    def test_the_pages_that_report_a_problem_are_plain_too(self):
        pages = [
            self.submit(f"/drive/{self.drive}/retire", kind="retired", project="t6p_" + "0" * 24),
            self.submit(f"/drive/{self.late}/promote", form="/new"),  # no serial to confirm
        ]
        hidden = self.fields("/new")
        self.api.mode = "down"
        pages.append(self.web.get("/"))
        pages.append(self.web.post("/new", data={**hidden, "label": "Queued synthetic"}))
        pages.append(self.web.get("/outbox"))  # an entry waits, with its values
        self.api.mode = "up"
        pages.append(self.web.get("/drive/t6d_" + "0" * 32))
        self.assertGreaterEqual(len(pages), 6)
        found = []
        for response in pages:
            text = visible(response.get_data(as_text=True))
            found += ID_PATTERN.findall(text)
            found += [w for w in BANNED if w in text.lower()]
        self.assertEqual(found, [])

    def test_ids_stay_in_links_and_the_page_still_works(self):
        html = self.text("/")
        self.assertIn(f'href="/drive/{self.drive}"', html)
        self.assertIn(f'value="{self.project}"', self.text(f"/drive/{self.drive}"))
        self.assertIn(f'href="/movements/{self.movement}/correct"', self.text("/movements"))


class DriveLabelTests(PlainPageCase):
    def test_a_drive_is_its_label_with_serial_and_project_underneath(self):
        page = self.text(f"/drive/{self.drive}")
        title = re.search(r"<h1>(.*?)</h1>", page, re.DOTALL)
        assert title is not None
        self.assertIn("Synthetic plain master", title.group(1))
        self.assertNotIn(self.drive, title.group(1))
        sub = visible(page)
        self.assertIn("SYN-PLAIN-1", sub)
        self.assertIn("Synthetic Plain Show", sub)
        self.assertIn("<title>Synthetic plain master", page)

    def test_search_lists_the_label_with_serial_and_project_and_no_id_column(self):
        text = visible(self.text("/"))
        self.assertNotIn("ID Label", text)
        self.assertIn("Synthetic plain master", text)
        self.assertIn("Synthetic Plain Show", text)

    def test_project_drop_downs_show_names_only(self):
        for url in (
            "/",
            f"/drive/{self.drive}",
            f"/movements/{self.movement}/correct",
            "/exceptions",
        ):
            html = self.text(url)
            options = re.findall(r"<option[^>]*>([^<]*)</option>", html)
            self.assertIn("Synthetic Plain Show", options, url)
            self.assertFalse([o for o in options if " — " in o or ID_PATTERN.search(o)], url)


class DetailsLineTests(PlainPageCase):
    def test_the_drive_page_has_one_collapsed_details_line_with_the_id(self):
        html = self.text(f"/drive/{self.drive}")
        parsed = details_of(html)
        self.assertEqual(parsed.details, 1)
        self.assertFalse(parsed.details_open)
        match = re.search(r'<details class="ids">(.*?)</details>', html, re.DOTALL)
        assert match is not None
        self.assertIn("<summary>Details</summary>", match.group(1))
        self.assertIn(self.drive, match.group(1))

    def test_no_other_page_has_a_details_line_of_ids(self):
        for url in self.routes():
            if (
                url == f"/drive/{self.drive}"
                or url.startswith(f"/drive/{self.other}")
                or url == (f"/drive/{self.late}")
            ):
                continue
            self.assertEqual(details_of(self.text(url)).details, 0, url)


class GlossaryTests(PlainPageCase):
    def test_the_plain_words_are_used(self):
        drive = visible(self.text(f"/drive/{self.drive}"))
        for word in (glossary.PROJECT_USE, glossary.SERIAL_TO_CHECK, glossary.CONFIRM_SERIAL):
            self.assertIn(word, drive)
        self.assertIn("Retire", drive)
        self.assertIn(glossary.WAITING_TO_SEND, visible(self.text("/outbox")))
        nav = re.search(r'<nav aria-label="Main">(.*?)</nav>', self.text("/"), re.DOTALL)
        assert nav is not None
        self.assertIn(f">{glossary.WAITING_TO_SEND}</a>", nav.group(1))
        self.assertNotIn(">Outbox</a>", nav.group(1))

    def test_the_glossary_holds_the_plain_words(self):
        self.assertEqual(
            (
                glossary.PROJECT_USE,
                glossary.MOVEMENT,
                glossary.SERIAL,
                glossary.SERIAL_TO_CHECK,
                glossary.CONFIRM_SERIAL,
                glossary.WAITING_TO_SEND,
                glossary.RETIRE,
            ),
            (
                "Project use",
                "movement",
                "serial",
                "serial to check",
                "Confirm serial",
                "Waiting to send",
                "Retire",
            ),
        )
        for word in BANNED[:-1]:
            self.assertNotIn(word, " ".join(glossary.PLAIN.values()).lower())

    def test_a_queued_write_waits_on_a_page_with_plain_words(self):
        hidden = self.fields("/new")
        self.api.mode = "down"
        self.web.post("/new", data={**hidden, "label": "Queued synthetic"})
        text = visible(self.web.get("/outbox").get_data(as_text=True))
        self.assertIn("Queued synthetic", text)
        self.assertNotIn("drive add", text.lower())
        self.assertNotIn("drive_add", text)


class WorkflowStillWorksTests(PlainPageCase):
    def test_the_serial_to_check_is_confirmed_from_the_drive_page(self):
        page = f"/drive/{self.drive}"
        self.assertIn(glossary.CONFIRM_SERIAL, self.text(page))
        # The route and the write are unchanged.
        response = self.submit(page + "/promote", form=page)
        self.assertEqual(response.status_code, 303)
        self.assertNotIn(glossary.CONFIRM_SERIAL, self.text(page))

    def test_a_manifest_upload_page_is_still_plain(self):
        data: dict[str, object] = {**self.fields(f"/drive/{self.drive}/catalog")}
        data["captured_on"] = "2026-11-10"
        data["root_path"] = "/"
        data["manifest"] = (
            io.BytesIO(json.dumps([{"path": "c.mov", "kind": "file", "size_bytes": 1}]).encode()),
            "scan.json",
        )
        response = self.web.post(
            f"/drive/{self.drive}/catalog", data=data, content_type="multipart/form-data"
        )
        self.assertLess(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
