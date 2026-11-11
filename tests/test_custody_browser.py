"""Chromium custody navigation against synthetic loopback HTTP and SQLite."""

import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from test_browser import loopback_server

from app import create_app


class CustodyBrowserTest(unittest.TestCase):
    def test_custody_kinds_and_backdated_visible_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            server = loopback_server(
                create_app(
                    str(Path(directory) / "db"), custody_actor_policy="self-declared-synthetic"
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    page.get_by_role("link", name="New Drive", exact=True).click()
                    page.get_by_label("Description", exact=True).fill("synthetic custody browser")
                    page.get_by_label("Needs review", exact=True).check()
                    page.get_by_role("button", name="Save", exact=True).click()
                    location = page.url
                    expect(page.get_by_text("Custody status: unknown", exact=True)).to_be_visible()
                    for kind, status in (
                        ("received", "in custody"),
                        ("released", "released"),
                        ("returned", "in custody"),
                        ("retired", "retired"),
                    ):
                        page.get_by_role("link", name="Record custody", exact=True).click()
                        page.get_by_label("Kind", exact=True).select_option(kind)
                        page.get_by_label("Business date", exact=False).fill("2025-05-06")
                        page.get_by_label("Recipient free text", exact=True).fill(
                            "<synthetic recipient>"
                        )
                        page.get_by_label("Self-declared actor", exact=False).fill(
                            "synthetic operator"
                        )
                        page.get_by_label("Project unresolved text", exact=True).fill(
                            "synthetic project"
                        )
                        if kind == "retired":
                            with page.expect_response(
                                lambda response: response.request.method == "POST"
                            ) as rejected:
                                page.get_by_role("button", name="Append custody event").click()
                            self.assertEqual(rejected.value.status, 400)
                            page.goto(location)
                            expect(page.locator("#custody-history li")).to_have_count(3)
                            page.get_by_role("link", name="Record custody", exact=True).click()
                            page.get_by_label("Kind", exact=True).select_option(kind)
                            page.get_by_label("Reason", exact=False).fill("synthetic lost")
                        page.get_by_role("button", name="Append custody event").click()
                        expect(
                            page.get_by_text("Custody status: " + status, exact=True)
                        ).to_be_visible()
                    rows = page.locator("#custody-history li")
                    original = rows.all_text_contents()
                    rows.last.get_by_role("link").click()
                    page.get_by_label("Kind", exact=True).select_option("received")
                    page.get_by_label("Business date", exact=False).fill("2000-01-01")
                    page.get_by_label("Reason", exact=False).fill("synthetic correction")
                    page.get_by_role("button", name="Append custody event").click()
                    expect(
                        page.get_by_text("Custody status: in custody", exact=True)
                    ).to_be_visible()
                    expect(rows).to_have_count(5)
                    self.assertEqual(rows.all_text_contents()[:4], original)
                    expect(rows.last).to_contain_text("Correction of event")
                    expect(rows.last).to_contain_text("Unknown actor")
                    page.get_by_role("link", name="Inventory", exact=True).click()
                    page.get_by_label("Status", exact=True).select_option("in custody")
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(
                        page.get_by_text("synthetic custody browser", exact=False)
                    ).to_be_visible()
                    browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()
