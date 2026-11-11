"""Chromium navigation and actual browser print rendering of saved evidence."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from test_assignments import NEW, OLD, Reader, export
from test_browser import QuietHandler
from werkzeug.serving import make_server

from app import create_app


class ShipmentBrowserTest(unittest.TestCase):
    def test_printed_snapshot_survives_edits_outage_and_server_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            artifacts = Path(os.environ.get("BROWSER_ARTIFACTS", directory))
            artifacts.mkdir(parents=True, exist_ok=True)
            path = str(Path(directory) / "synthetic.sqlite")
            reader = Reader(export())
            app = create_app(path, reader, custody_actor_policy="self-declared-synthetic")
            self.assertEqual(app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)

            def start(application):
                server = make_server("127.0.0.1", 5000, application, request_handler=QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                return server, thread

            server, thread = start(app)
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    context = browser.new_context(viewport={"width": 1400, "height": 1000})
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
                    locations = []
                    ids = []
                    for n in range(2):
                        page.goto("http://127.0.0.1:5000/")
                        page.get_by_role("link", name="New Drive", exact=True).click()
                        page.get_by_label("Description", exact=True).fill(
                            f"synthetic printed drive {n}"
                        )
                        if n == 0:
                            page.get_by_label("Accepted serial", exact=True).fill(
                                "synthetic accepted"
                            )
                            page.get_by_label("Suspected serial (uncertain)", exact=True).fill(
                                "synthetic candidate"
                            )
                            page.get_by_label("Label", exact=True).fill("synthetic original label")
                            page.get_by_label("Capacity (TB)", exact=True).fill("4")
                        page.get_by_label("Needs review", exact=True).check()
                        page.get_by_role("button", name="Save", exact=True).click()
                        locations.append(page.url)
                        ids.append(page.url.rsplit("/", 1)[-1])
                        page.get_by_label("Cached Project", exact=False).select_option(OLD)
                        page.get_by_label("Assignment label", exact=True).fill(
                            "synthetic original assignment"
                        )
                        page.get_by_role("button", name="Add Assignment", exact=True).click()
                    # External reader outage retains complete cache and does not enter release path.
                    reader.payload = RuntimeError("synthetic private upstream failure")
                    self.assertNotEqual(
                        app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0
                    )
                    page.get_by_role("link", name="Inventory", exact=True).click()
                    page.get_by_role("link", name="Release shipment", exact=True).click()
                    expect(
                        page.get_by_text("Refresh failed; using last complete cache.")
                    ).to_be_visible()
                    for drive_id in ids:
                        page.locator(f'input[name="drive_id"][value="{drive_id}"]').check()
                    page.get_by_label("Recipient free text", exact=True).fill(
                        "<synthetic original recipient>"
                    )
                    page.get_by_label("Business date", exact=False).fill("2024-05-06")
                    page.get_by_label("Cached Project", exact=False).select_option(OLD)
                    page.get_by_label("Self-declared actor (not authenticated)", exact=True).fill(
                        "synthetic printer"
                    )
                    page.get_by_role("button", name="Release selected Drives", exact=True).click()
                    shipment = page.url
                    expect(page.locator("tbody tr")).to_have_count(2)
                    expect(
                        page.get_by_role("columnheader", name="Accepted serial", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role(
                            "columnheader", name="Suspected serial (uncertain)", exact=True
                        )
                    ).to_be_visible()
                    expect(
                        page.get_by_role("cell", name="SYNTHETIC ACCEPTED", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("cell", name="SYNTHETIC CANDIDATE", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("cell", name="Unknown accepted serial", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("cell", name="Unknown capacity", exact=True)
                    ).to_be_visible()
                    saved = page.locator("#manifest").inner_html()
                    # Exercise the button's native window.print, then Chromium's print renderer.
                    page.evaluate(
                        "window.printCalls=0; const nativePrint=window.print; window.print=()=>{window.printCalls++; nativePrint.call(window)}; void 0"
                    )
                    page.get_by_role("button", name="Print manifest", exact=True).click()
                    self.assertEqual(page.evaluate("window.printCalls"), 1)
                    page.emulate_media(media="print")
                    expect(page.locator(".navigation")).to_be_hidden()
                    pdf = page.pdf(path=str(artifacts / "shipment-original.pdf"), format="A4")
                    self.assertTrue(pdf.startswith(b"%PDF-"))
                    self.assertGreater(len(pdf), 1000)
                    page.screenshot(path=str(artifacts / "shipment-print.png"), full_page=True)
                    page.emulate_media(media="screen")
                    for location in locations:
                        page.goto(location)
                        expect(
                            page.get_by_text("Custody status: released", exact=True)
                        ).to_be_visible()
                        expect(
                            page.get_by_role("button", name="End Assignment", exact=True)
                        ).to_have_count(1)
                    page.goto(locations[0])
                    page.get_by_role("link", name="Edit", exact=True).click()
                    page.get_by_label("Accepted serial", exact=True).fill(
                        "synthetic changed accepted"
                    )
                    page.get_by_label("Suspected serial (uncertain)", exact=True).fill(
                        "synthetic changed candidate"
                    )
                    page.get_by_label("Label", exact=True).fill("synthetic changed label")
                    page.get_by_label("Capacity (TB)", exact=True).fill("8")
                    page.get_by_label("Description", exact=True).fill(
                        "synthetic changed description"
                    )
                    page.get_by_role("button", name="Save", exact=True).click()
                    page.get_by_role("link", name="Record custody", exact=True).click()
                    page.get_by_label("Kind", exact=True).select_option("returned")
                    page.get_by_label("Recipient free text", exact=True).fill(
                        "synthetic changed recipient"
                    )
                    page.get_by_role("button", name="Append custody event", exact=True).click()
                    expect(
                        page.get_by_text("Custody status: in custody", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("button", name="End Assignment", exact=True)
                    ).to_have_count(1)
                    reader.payload = export(NEW, (OLD,), name="Synthetic changed Project")
                    self.assertEqual(
                        app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0
                    )
                    server.shutdown()
                    thread.join(timeout=10)
                    server.server_close()
                    server, thread = start(create_app(path))
                    page.goto(shipment)
                    self.assertEqual(saved, page.locator("#manifest").inner_html())
                    expect(page.get_by_text("Synthetic show", exact=False)).to_be_visible()
                    expect(page.get_by_text("synthetic changed", exact=False)).to_have_count(0)
                    printed_after = page.pdf(
                        path=str(artifacts / "shipment-after-restart.pdf"), format="A4"
                    )
                    self.assertTrue(printed_after.startswith(b"%PDF-"))
                    page.get_by_role("link", name="Shipments", exact=True).click()
                    page.get_by_role("link", name=shipment.rsplit("/", 1)[-1], exact=True).click()
                    self.assertEqual(saved, page.locator("#manifest").inner_html())
                    self.assertEqual(errors, [])
                    context.tracing.stop(path=str(artifacts / "shipment-trace.zip"))
                    (artifacts / "shipment-result.json").write_text(
                        json.dumps(
                            {
                                "browser": browser.version,
                                "result": "passed",
                                "console_errors": errors,
                                "pdf_bytes": [len(pdf), len(printed_after)],
                                "stable_saved_manifest_after_restart": True,
                            },
                            indent=2,
                        )
                    )
                    browser.close()
            finally:
                server.shutdown()
                thread.join(timeout=10)
                server.server_close()
