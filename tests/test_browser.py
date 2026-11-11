"""Real Chromium against a loopback HTTP server and disposable synthetic SQLite."""

import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from test_assignments import OLD, PROJECT, Reader, export
from werkzeug.serving import WSGIRequestHandler, make_server

from app import create_app


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


class BrowserTest(unittest.TestCase):
    def test_navigation_creation_edit_search_and_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(os.environ.get("BROWSER_ARTIFACTS", directory))
            artifacts.mkdir(parents=True, exist_ok=True)
            app = create_app(str(Path(directory) / "synthetic.sqlite"), Reader(export()))
            self.assertEqual(app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
            server = make_server(
                "127.0.0.1",
                5000,
                app,
                request_handler=QuietHandler,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    context = browser.new_context(viewport={"width": 1280, "height": 900})
                    context.tracing.start(screenshots=True, snapshots=True, sources=True)
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on(
                        "console",
                        lambda message: (
                            errors.append(message.text) if message.type == "error" else None
                        ),
                    )
                    page.goto("http://127.0.0.1:5000/")
                    page.get_by_role("link", name="New Drive").click()
                    expect(page.get_by_label("Ownership", exact=True)).to_have_value("unknown")
                    page.get_by_label("Description", exact=True).fill("synthetic browser drive")
                    page.get_by_label("Suspected serial (uncertain)", exact=True).fill(
                        "browser candidate"
                    )
                    page.get_by_label("Needs review", exact=True).check()
                    page.get_by_role("button", name="Save", exact=True).click()
                    expect(page.get_by_text("Unknown ownership", exact=True)).to_be_visible()
                    location = page.url
                    drive_id = location.rsplit("/", 1)[-1]
                    self.assertRegex(drive_id, r"^t6d_[0-9a-f]{32}$")
                    page.get_by_role("link", name="Inventory", exact=True).click()
                    page.get_by_label("Search", exact=True).fill("browser candidate")
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(
                        page.get_by_text("Suspected (uncertain): BROWSER CANDIDATE", exact=False)
                    ).to_be_visible()
                    page.screenshot(path=str(artifacts / "uncertain-inventory.png"), full_page=True)
                    page.get_by_label("Search", exact=True).fill("")
                    page.get_by_label("Exact accepted serial", exact=True).fill("browser candidate")
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(page.get_by_role("link", name=drive_id, exact=True)).to_have_count(0)
                    page.get_by_label("Exact accepted serial", exact=True).fill("")
                    page.get_by_label("Search", exact=True).fill(drive_id)
                    page.get_by_role("button", name="Find", exact=True).click()
                    page.get_by_role("link", name=drive_id, exact=True).click()
                    page.get_by_role("link", name="Edit", exact=True).click()
                    stale = context.new_page()
                    stale.goto(page.url)
                    page.get_by_label("Brand", exact=True).fill("synthetic corrected brand")
                    page.get_by_role("button", name="Save", exact=True).click()
                    expect(
                        page.get_by_text("synthetic corrected brand", exact=True)
                    ).to_be_visible()
                    stale.get_by_label("Description", exact=True).fill("must not overwrite")
                    with stale.expect_response(
                        lambda response: response.request.method == "POST"
                    ) as rejected:
                        stale.get_by_role("button", name="Save", exact=True).click()
                    self.assertEqual(rejected.value.status, 409)
                    expect(stale.get_by_role("link", name="Reload the form")).to_be_visible()
                    page.reload()
                    expect(
                        page.get_by_text("synthetic corrected brand", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text("synthetic browser drive", exact=True)).to_be_visible()
                    page.get_by_role("button", name="Promote suspected serial to accepted").click()
                    expect(page.get_by_text("BROWSER CANDIDATE", exact=True)).to_be_visible()
                    expect(
                        page.get_by_role("button", name="Promote suspected serial to accepted")
                    ).to_have_count(0)
                    page.get_by_label("Cached Project", exact=False).select_option(OLD)
                    page.get_by_label("Assignment label", exact=True).fill("synthetic old label")
                    page.get_by_label("Start date", exact=True).fill("2024-03-10")
                    page.get_by_role("button", name="Add Assignment", exact=True).click()
                    expect(
                        page.get_by_text("Label: synthetic old label", exact=False)
                    ).to_be_visible()
                    page.get_by_label("Unresolved Project text", exact=True).fill("SYN")
                    page.get_by_role("button", name="Add Assignment", exact=True).click()
                    expect(
                        page.locator("li[data-assignment-id]").filter(has_text="Unresolved: SYN")
                    ).to_be_visible()
                    expect(
                        page.get_by_role("button", name="End Assignment", exact=True)
                    ).to_have_count(2)
                    first = page.locator("li[data-assignment-id]").filter(
                        has_text="synthetic old label"
                    )
                    first.get_by_label("End date", exact=True).fill("2024-03-11")
                    first.get_by_role("button", name="End Assignment", exact=True).click()
                    expect(page.get_by_text("End: 2024-03-11", exact=False)).to_be_visible()
                    expect(
                        page.get_by_role("button", name="End Assignment", exact=True)
                    ).to_have_count(1)
                    page.get_by_role("link", name="Inventory", exact=True).click()
                    page.get_by_label("Project (open use)", exact=False).select_option(PROJECT)
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(page.get_by_role("link", name=drive_id, exact=True)).to_have_count(0)
                    page.get_by_label("Project (open use)", exact=False).select_option("")
                    page.get_by_label("Unresolved Project text (exact, open use)", exact=True).fill(
                        "SYN"
                    )
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(page.get_by_role("link", name=drive_id, exact=True)).to_be_visible()
                    expect(
                        page.get_by_text(
                            "Open Project use (not custody): Unresolved: SYN", exact=True
                        )
                    ).to_be_visible()
                    page.get_by_role("link", name=drive_id, exact=True).click()
                    expect(page.get_by_role("link", name="Edit", exact=True)).to_be_visible()
                    page.get_by_role("link", name="Record custody", exact=True).click()
                    for kind, status in (
                        ("received", "in custody"),
                        ("released", "released"),
                        ("returned", "in custody"),
                    ):
                        page.get_by_label("Kind", exact=True).select_option(kind)
                        page.get_by_role("button", name="Append custody event", exact=True).click()
                        expect(
                            page.get_by_text("Custody status: " + status, exact=True)
                        ).to_be_visible()
                        expect(page.get_by_text("End: 2024-03-11", exact=False)).to_be_visible()
                        expect(page.locator("li[data-assignment-id]")).to_have_count(2)
                        expect(
                            page.get_by_role("button", name="End Assignment", exact=True)
                        ).to_have_count(1)
                        if kind != "returned":
                            page.get_by_role("link", name="Record custody", exact=True).click()
                    expect(page.locator("li[data-event-id]")).to_have_count(3)
                    page.screenshot(path=str(artifacts / "edited-detail.png"), full_page=True)
                    self.assertEqual(errors, [])
                    context.tracing.stop(path=str(artifacts / "browser-trace.zip"))
                    (artifacts / "browser-result.json").write_text(
                        json.dumps(
                            {
                                "browser": browser.version,
                                "result": "passed",
                                "console_errors": errors,
                                "scenarios": [
                                    "create",
                                    "navigate",
                                    "uncertain search",
                                    "accepted-only lookup",
                                    "edit",
                                    "stale form rejection",
                                    "explicit promotion",
                                    "unknown ownership",
                                    "cached and unresolved Assignments",
                                    "explicit Assignment end and history",
                                    "open Project filters independent of custody",
                                ],
                            },
                            indent=2,
                        )
                    )
                    browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    unittest.main()
