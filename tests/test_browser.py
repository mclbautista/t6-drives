"""Real Chromium against a loopback HTTP server and disposable synthetic SQLite."""

import json
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
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
            server = make_server(
                "127.0.0.1",
                5000,
                create_app(str(Path(directory) / "synthetic.sqlite")),
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
