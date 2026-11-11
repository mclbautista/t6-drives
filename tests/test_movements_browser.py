"""Combined host imports, private exception navigation and reconstructed print."""

import tempfile
import threading
import unittest
from pathlib import Path

import test_movements as movements
from playwright.sync_api import expect, sync_playwright
from test_assignments import Reader, export
from test_browser import loopback_server
from test_imports import synthetic_workbook

from app import create_app
from import_store import SQLiteDriveStore
from shipments import Shipments


class CombinedImportBrowserTests(unittest.TestCase):
    def test_combined_navigation_print_exceptions_safety_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "synthetic-inventory.xlsx"
            log = Path(directory) / "synthetic-log.xlsx"
            database = str(Path(directory) / "synthetic.sqlite")
            synthetic_workbook(inventory, [["Synthetic original A", "SYN-A", "YES"]])
            movements.log_workbook(log)
            reader = Reader(export(name="Synthetic month"))
            app = create_app(database, reader)
            self.assertEqual(app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
            for command, lineage, path in [
                ("import-inventory", "inventory", inventory),
                ("import-log-sheet", "log", log),
            ]:
                result = app.test_cli_runner().invoke(
                    args=[command, "--lineage", lineage, "--workbook", str(path)]
                )
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertNotIn(str(path), result.output)
            store = SQLiteDriveStore(database)
            a = store.find_by_serial("SYN-A")
            assert a is not None
            manifests = Shipments(store.connect, store.assignments).all()
            repeated = next(s for s in manifests if len(s["lines"]) == 3)
            server = loopback_server(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on(
                        "console",
                        lambda message: (
                            errors.append(message.text) if message.type == "error" else None
                        ),
                    )
                    base = f"http://127.0.0.1:{server.server_port}"
                    page.goto(base)
                    page.locator(f'a[href="/drive/{a.id}"]').click()
                    expect(page.get_by_text("Unknown date", exact=False).first).to_be_visible()
                    self.assertIn("Source repeated label", page.locator("body").inner_text())
                    page.goto(base + "/shipments/" + repeated["id"])
                    expect(
                        page.get_by_text("Import-time reconstruction", exact=False)
                    ).to_be_visible()
                    expect(page.locator("tbody tr")).to_have_count(3)
                    self.assertEqual(page.get_by_role("cell", name=a.id, exact=True).count(), 2)
                    expect(
                        page.get_by_role("columnheader", name="Accepted serial", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role(
                            "columnheader", name="Suspected serial (uncertain)", exact=True
                        )
                    ).to_be_visible()
                    saved = page.locator("#manifest").inner_html()
                    page.evaluate(
                        "window.printCalls=0; const nativePrint=window.print; window.print=()=>{window.printCalls++; nativePrint.call(window)}; void 0"
                    )
                    page.get_by_role("button", name="Print manifest", exact=True).click()
                    self.assertEqual(page.evaluate("window.printCalls"), 1)
                    page.emulate_media(media="print")
                    expect(page.locator(".navigation")).to_be_hidden()
                    self.assertTrue(page.pdf().startswith(b"%PDF-"))
                    page.emulate_media(media="screen")
                    page.goto(base)
                    page.get_by_role("link", name="Import exceptions", exact=True).click()
                    page.get_by_label("Search source, reason or evidence").fill("synthetic-secret")
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(page.locator("article")).to_have_count(1)
                    expect(page.get_by_text("movement with no serial", exact=False)).to_be_visible()
                    self.assertIn("Row 6", page.locator("article").inner_text())
                    self.assertIn(str(log), page.locator("article").inner_text())
                    self.assertEqual(page.locator("script").count(), 0)
                    self.assertEqual(page.locator('form[method="post"]').count(), 0)
                    self.assertIsNone(page.evaluate("window.syntheticSecret"))
                    response = page.reload()
                    assert response is not None
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                    page.get_by_label("Search source, reason or evidence").fill("absent-synthetic")
                    page.get_by_role("button", name="Find", exact=True).click()
                    expect(page.locator("article")).to_have_count(0)
                    before = movements.MovementTests.state(store)
                    # Fetch errors deliberately are checked as HTTP, not browser console errors.
                    client = app.test_client()
                    for route in ["/import-log-sheet", "/import-inventory", "/exceptions/resolve"]:
                        self.assertEqual(client.get(route).status_code, 404)
                    for route in ["/exceptions", "/import-log-sheet"]:
                        self.assertEqual(
                            client.post(
                                route, headers={"Origin": "https://synthetic.invalid"}
                            ).status_code,
                            403,
                        )
                    for route in ["/health", "/missing-synthetic", "/import-log-sheet"]:
                        http_response = client.get(route)
                        for private in [str(log), "synthetic-secret", "SYN-A"]:
                            self.assertNotIn(private, http_response.text)
                    self.assertEqual(movements.MovementTests.state(store), before)
                    self.assertEqual(errors, [])
                    page.goto(base + "/drive/" + a.id)
                    page.get_by_role("link", name="Edit", exact=True).click()
                    page.get_by_label("Accepted serial", exact=True).fill("SYN-CORRECTED")
                    page.get_by_label("Label", exact=True).fill("Synthetic corrected label")
                    page.get_by_label("Capacity (TB)", exact=True).fill("16")
                    page.get_by_role("button", name="Save", exact=True).click()
                    reader.payload = export(name="Synthetic renamed Project")
                    self.assertEqual(
                        app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0
                    )
                    server.shutdown()
                    thread.join(5)
                    server.server_close()
                    restarted = create_app(database)
                    for command, lineage, path in [
                        ("import-inventory", "inventory", inventory),
                        ("import-log-sheet", "log", log),
                    ]:
                        self.assertEqual(
                            restarted.test_cli_runner()
                            .invoke(args=[command, "--lineage", lineage, "--workbook", str(path)])
                            .exit_code,
                            0,
                        )
                    server = loopback_server(restarted)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    page.goto(f"http://127.0.0.1:{server.server_port}/shipments/" + repeated["id"])
                    self.assertEqual(page.locator("#manifest").inner_html(), saved)
                    self.assertEqual(errors, [])
                    browser.close()
            finally:
                server.shutdown()
                thread.join(5)
                server.server_close()
