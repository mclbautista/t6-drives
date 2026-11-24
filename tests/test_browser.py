"""Real Chromium against a loopback HTTP server and the test API."""

import json
import logging
import os
import threading
import unittest

from playwright.sync_api import expect, sync_playwright
from support import DriveAppCase
from t6.verbs import MovementListInput
from werkzeug.serving import WSGIRequestHandler, make_server

FIRST_SCAN = [
    {"path": "Show A", "kind": "dir"},
    {"path": "Show A/clip1.mov", "kind": "file", "size_bytes": 100},
    {"path": "Show A/clip2.mov", "kind": "file", "size_bytes": 200},
    {"path": "notes.txt", "kind": "file", "size_bytes": 5},
]
SECOND_SCAN = [
    {"path": "Show A", "kind": "dir"},
    {"path": "Show A/clip1.mov", "kind": "file", "size_bytes": 100},
    {"path": "Show A/clip2.mov", "kind": "file", "size_bytes": 250},
    {"path": "Show A/clip3.mov", "kind": "file", "size_bytes": 300},
]


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


class BrowserTest(DriveAppCase):
    def setUp(self):
        super().setUp()
        # Bind an OS-selected port without changing the production authority guard: translate only this
        # test server's exact authority and same-origin Origin. HTTP tests cover the real Host guard.
        self.server = make_server("127.0.0.1", 0, self.app, request_handler=QuietHandler)
        authority = f"127.0.0.1:{self.server.server_port}"
        app = self.app

        def test_authority(environ, start_response):
            if environ.get("HTTP_HOST") == authority:
                environ["HTTP_HOST"] = "127.0.0.1:5000"
                if environ.get("HTTP_ORIGIN") == f"http://{authority}":
                    environ["HTTP_ORIGIN"] = "http://127.0.0.1:5000"
            return app(environ, start_response)

        self.server.app = test_authority
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        # CI installs Chromium with Playwright; a machine that has its own sets DRIVES_CHROMIUM.
        browser = self.playwright.chromium.launch(executable_path=os.environ.get("DRIVES_CHROMIUM"))
        self.addCleanup(browser.close)
        self.page = browser.new_page(viewport={"width": 1280, "height": 900})
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def test_projects_and_movements_at_phone_and_desktop_width(self):
        from test_projects import load_projects

        load_projects(self)
        for width in (375, 1280):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.goto(self.base + "/")
            self.page.get_by_role("link", name="Projects", exact=True).click()
            expect(self.page.get_by_role("heading", name="Projects", exact=True)).to_be_visible()
            for project in ("Unresolved: Synthetic violet", "Unresolved: Synthetic text project"):
                self.page.get_by_role("link", name=project, exact=True).click()
                expect(
                    self.page.get_by_text(
                        "SYN-TEXT" if project.endswith("project") else "SYN-MASTER", exact=True
                    )
                ).to_be_visible()
                self.assertLessEqual(
                    self.page.evaluate("document.documentElement.scrollWidth"), width
                )
                self.page.get_by_role("link", name="All Projects", exact=True).click()
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
            self.page.get_by_role("link", name="Movements", exact=True).click()
            self.page.get_by_label("Person", exact=True).fill("Receiver")
            self.page.get_by_role("button", name="Filter", exact=True).click()
            expect(self.page.get_by_text("Synthetic missing", exact=True)).to_be_visible()
            expect(self.page.get_by_text("Unmatched", exact=True)).to_be_visible()
            expect(self.page.get_by_text("Synthetic master", exact=True)).to_have_count(0)
            self.assertLessEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)

    def test_search_and_new_drive_at_phone_and_desktop_width(self):
        drive = self.api.add_drive(
            label="Synthetic phone master " + "x" * 100,
            serial="SYN-PHONE-" + "9" * 80,
            office_location="Synthetic shelf " + "z" * 100,
        )
        for width in (375, 1280):
            self.page.set_viewport_size({"width": width, "height": 900})
            self.page.goto(self.base + "/")
            expect(self.page.locator("main input:visible")).to_have_count(1)
            self.page.get_by_label("Search", exact=True).fill("phone master")
            self.page.get_by_role("button", name="Find", exact=True).click()
            expect(
                self.page.get_by_role(
                    "link", name="Synthetic phone master " + "x" * 100, exact=True
                )
            ).to_be_visible()
            for path in ("/", "/new", f"/drive/{drive}", f"/drive/{drive}/retire"):
                self.page.goto(self.base + path)
                self.assertLessEqual(
                    self.page.evaluate("document.documentElement.scrollWidth"), width, path
                )
            self.page.goto(self.base + "/new")
            self.page.get_by_label("Serial", exact=True).fill("SYN-PHONE-" + "9" * 80)
            self.page.get_by_role("button", name="Continue", exact=True).click()
            expect(self.page).to_have_url(self.base + "/drive/" + drive)

    def test_create_search_edit_promote_assign_and_record_custody(self):
        project = self.api.project("Synthetic show")
        page = self.page
        page.goto(self.base + "/")
        page.get_by_role("link", name="New Drive").click()
        page.get_by_role("button", name="Continue", exact=True).click()
        expect(page.get_by_label("Ownership", exact=True)).to_have_value("unknown")
        page.get_by_label("Label", exact=True).fill("synthetic browser drive")
        page.get_by_label("Serial to check", exact=True).fill("browser candidate")
        page.get_by_label("Needs review", exact=True).check()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Unknown ownership", exact=True)).to_be_visible()
        drive_id = page.url.rsplit("/", 1)[-1]
        self.assertRegex(drive_id, r"^t6d_[0-9a-f]{32}$")
        page.get_by_role("link", name="Search", exact=True).click()
        page.get_by_label("Search", exact=True).fill("browser candidate")
        page.get_by_role("button", name="Find", exact=True).click()
        expect(page.get_by_text("Serial to check: BROWSER CANDIDATE")).to_be_visible()
        page.get_by_label("Search", exact=True).fill("")
        page.get_by_text("More filters", exact=True).click()
        page.get_by_label("Exact serial", exact=True).fill("browser candidate")
        page.get_by_role("button", name="Find", exact=True).click()
        expect(page.get_by_role("link", name="synthetic browser drive", exact=True)).to_have_count(
            0
        )
        page.goto(f"{self.base}/drive/{drive_id}")
        page.get_by_role("link", name="Edit", exact=True).click()
        page.get_by_label("Label", exact=True).fill("synthetic corrected label")
        page.get_by_role("button", name="Save", exact=True).click()
        expect(
            page.get_by_role("heading", name="synthetic corrected label", exact=True)
        ).to_be_visible()
        page.get_by_role("button", name="Confirm serial").click()
        expect(page.get_by_text("BROWSER CANDIDATE", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Confirm serial")).to_have_count(0)
        page.get_by_label("Project", exact=False).first.select_option(project)
        page.get_by_label("Project use label", exact=True).fill("synthetic master")
        page.get_by_label("Start date", exact=True).fill("2026-11-01")
        page.get_by_role("button", name="Add Project use", exact=True).click()
        expect(page.get_by_text("Label: synthetic master", exact=False)).to_be_visible()
        page.get_by_label("End date", exact=True).first.fill("2026-11-05")
        page.get_by_role("button", name="End Project use", exact=True).click()
        expect(page.get_by_text("End: 2026-11-05", exact=False)).to_be_visible()
        page.goto(f"{self.base}/drive/{drive_id}/custody")
        for kind, status in (("received", "in custody"), ("retired", "retired")):
            page.get_by_label("Kind", exact=True).select_option(kind)
            page.get_by_label("Reason", exact=False).fill("synthetic lost")
            page.get_by_role("button", name="Record movement").click()
            expect(page.get_by_text("Custody status: " + status, exact=True)).to_be_visible()
            if kind == "received":
                page.get_by_role("link", name="Retire", exact=True).click()
        expect(page.locator("li[data-event-number]")).to_have_count(2)
        page.get_by_role("link", name="Search", exact=True).click()
        page.get_by_text("More filters", exact=True).click()
        page.get_by_label("Status", exact=True).select_option("retired")
        page.get_by_role("button", name="Find", exact=True).click()
        expect(page.get_by_text("synthetic corrected label", exact=False)).to_be_visible()

    def test_qr_label_scan_lookup_and_dated_catalog(self):
        page = self.page
        page.goto(self.base + "/new")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_label("Label", exact=True).fill("Synthetic shelf drive")
        page.get_by_label("Office location", exact=True).fill("Office A / Shelf 1")
        expect(page.get_by_label("QR code", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_text("Office A / Shelf 1", exact=True)).to_be_visible()
        drive_id = page.url.rsplit("/", 1)[-1]
        page.get_by_role("button", name="Add QR", exact=True).click()
        expect(page.locator(".qr-picture svg")).to_be_visible()
        page.goto(self.base + "/qr/" + drive_id)  # what scanning the printed label opens
        self.assertTrue(page.url.endswith("/drive/" + drive_id))
        page.goto(self.base + "/")
        page.get_by_text("More filters", exact=True).click()
        page.get_by_label("Office location", exact=True).fill("shelf 1")
        page.get_by_role("button", name="Find", exact=True).click()
        expect(page.get_by_role("link", name="Synthetic shelf drive", exact=True)).to_be_visible()

        page.goto(f"{self.base}/drive/{drive_id}")
        page.get_by_role("link", name="Contents", exact=True).click()
        expect(page.get_by_text("No scans yet")).to_be_visible()
        for day, entries in (
            ("2026-03-01", FIRST_SCAN),
            ("2026-03-10", SECOND_SCAN),
        ):
            page.get_by_label("Scan date", exact=True).fill(day)
            page.get_by_label("Manifest file", exact=True).set_input_files(
                {
                    "name": "scan.json",
                    "mimeType": "application/json",
                    "buffer": json.dumps(entries).encode(),
                }
            )
            page.get_by_role("button", name="Save scan", exact=True).click()
        expect(page.locator("li[data-snapshot-id]")).to_have_count(2)
        page.locator("li[data-snapshot-id]").first.get_by_role("link", name="What changed").click()
        expect(
            page.get_by_role("heading", name="Changed by the scan of 2026-03-10")
        ).to_be_visible()
        expect(page.locator("tr.change--added")).to_have_count(1)
        expect(page.locator("tr.change--modified")).to_have_count(1)
        expect(page.locator("tr.change--removed")).to_have_count(1)
        page.get_by_label("Date", exact=True).fill("2026-03-05")
        page.get_by_role("button", name="Show contents", exact=True).click()
        expect(page.get_by_text("From the scan of 2026-03-01", exact=False)).to_be_visible()
        expect(page.get_by_role("cell", name="notes.txt", exact=True)).to_be_visible()
        expect(page.get_by_role("cell", name="Show A/clip3.mov", exact=True)).to_have_count(0)
        page.get_by_label("Date", exact=True).fill("2026-04-01")
        page.get_by_role("button", name="Show contents", exact=True).click()
        expect(page.get_by_role("cell", name="Show A/clip3.mov", exact=True)).to_be_visible()
        expect(page.get_by_role("cell", name="notes.txt", exact=True)).to_have_count(0)

    def test_release_shipment_manifest_and_print_button(self):
        project = self.api.project("Synthetic show")
        first = self.api.add_drive(label="synthetic first", serial="SYN-FIRST")
        self.api.add_drive(label="synthetic second")
        page = self.page
        page.goto(self.base + "/")
        page.goto(self.base + "/shipments/new")
        page.locator(f'input[name="drive_id"][value="{first}"]').check()
        page.get_by_label("Existing party kind").select_option("studio")
        page.get_by_label("Party (page ID or name)").fill("Invented Studio")
        page.get_by_label("Project", exact=False).first.select_option(project)
        page.get_by_role("button", name="Release selected Drives", exact=True).click()
        expect(page.get_by_role("columnheader", name="Serial", exact=True)).to_be_visible()
        expect(page.get_by_role("cell", name="SYN-FIRST", exact=True)).to_be_visible()
        expect(page.locator("tr[data-drive-id]")).to_have_count(1)
        page.evaluate("window.print = () => { window.printed = true }")
        page.get_by_role("button", name="Print manifest", exact=True).click()
        self.assertTrue(page.evaluate("window.printed"))
        page.goto(self.base + "/shipments")
        expect(page.get_by_text("Invented Studio", exact=False)).to_be_visible()
        page.get_by_role("link", name="Search", exact=True).click()
        page.get_by_text("More filters", exact=True).click()
        page.get_by_label("Status", exact=True).select_option("released")
        page.get_by_role("button", name="Find", exact=True).click()
        expect(page.get_by_role("link", name="synthetic first", exact=True)).to_be_visible()

    def test_exceptions_corrections_and_place_at_phone_and_desktop_width(self):
        project = self.api.project("Synthetic exceptions project")
        drive = self.api.add_drive(
            label="Synthetic exception master " + "x" * 100, serial="SYN-EXC-" + "9" * 80
        )
        self.api.add_drive(label="Synthetic spare", serial="SYN-SPARE")
        self.api.capture("OUT Invented Courier|SYN-EXC-" + "9" * 80, message=1)
        capture = self.api.capture("OUT Invented Courier|" + "NOWHERE-" * 20, message=2)
        movement = self.api.read("movement_list", MovementListInput)["movements"][0]["id"]
        for width in (375, 1280):
            page = self.page
            page.set_viewport_size({"width": width, "height": 900})
            for path in (
                "/exceptions",
                f"/captures/{capture}/drive",
                f"/movements/{movement}/correct",
                f"/movements?drive={drive}",
                f"/drive/{drive}",
            ):
                page.goto(self.base + path)
                self.assertLessEqual(
                    page.evaluate("document.documentElement.scrollWidth"), width, path
                )
            page.goto(self.base + "/exceptions")
            photo = page.locator("img.capture-photo")
            expect(photo).to_be_visible()
            page.wait_for_function(
                "document.querySelector('img.capture-photo').naturalWidth > 0", timeout=5000
            )
        page = self.page
        page.set_viewport_size({"width": 375, "height": 900})
        page.goto(self.base + "/exceptions")
        page.get_by_label("Project", exact=True).select_option(project)
        page.get_by_role("button", name="Set Project", exact=True).click()
        expect(page.get_by_text("No movements need a Project.")).to_be_visible()
        page.get_by_role("link", name="Give it a Drive").click()
        page.get_by_label("Find a Drive").fill("SYN-SPARE")
        page.get_by_role("button", name="Find", exact=True).click()
        page.get_by_role("button", name="Give this Drive").click()
        expect(page.get_by_role("status")).to_contain_text("was given a Drive")
        expect(page.get_by_text("No unresolved captures.")).to_be_visible()
        page.goto(self.base + f"/drive/{drive}")
        page.get_by_label("Place", exact=True).select_option("SHELF")
        page.get_by_role("button", name="Save place and ownership").click()
        page.goto(self.base + "/?q=SYN-EXC")
        expect(page.get_by_text("SHELF", exact=True)).to_be_visible()
        expect(page.get_by_text("T6 owned", exact=True)).to_be_visible()

    def test_a_write_made_while_the_api_is_down_is_queued_and_lands_once_after_recovery(self):
        page = self.page
        page.goto(self.base + "/new")
        page.get_by_role("button", name="Continue", exact=True).click()
        page.get_by_label("Label", exact=True).fill("synthetic queued drive")
        self.api.stop()
        page.get_by_role("button", name="Save", exact=True).click()
        expect(page.get_by_role("status")).to_contain_text("queued")
        page.get_by_role("link", name="Waiting to send").click()
        expect(page.get_by_text("Waiting: New Drive", exact=False)).to_be_visible()
        page.goto(self.base + "/")
        expect(page.get_by_role("alert")).to_contain_text("unavailable")
        self.api.start()
        page.goto(self.base + "/")
        expect(page.get_by_text("synthetic queued drive", exact=False)).to_be_visible()
        page.get_by_role("link", name="Waiting to send").click()
        expect(page.get_by_text("Nothing is waiting")).to_be_visible()
        self.assertEqual(len(self.api.drives()), 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    unittest.main()
