"""Combined Project/Assignment/custody regressions at the HTTP/SQLite seam."""

import re
import tempfile
import unittest
from pathlib import Path

from test_assignments import OLD, PROJECT, Reader, export
from test_inventory import edit_data, token

from app import create_app


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "synthetic.sqlite")
        self.reader = Reader(export())
        self.app = create_app(self.path, self.reader)
        self.client = self.app.test_client()
        self.csrf = token(self.client)
        self.assertEqual(self.app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
        self.location = self.client.post(
            "/new",
            data={"csrf": self.csrf, "description": "synthetic combined", "needs_review": "on"},
        ).headers["Location"]

    def test_imported_unknown_owner_ships_with_cached_project_and_stable_manifest(self):
        from test_assignments import NEW
        from test_imports import synthetic_workbook

        from imports import InventoryImports

        source = Path(self.temp.name) / "synthetic.xlsx"
        synthetic_workbook(source, [["Imported synthetic", "SYN-IMPORT-SHIP", "YES"]])
        imports = InventoryImports(self.path)
        imports.run("synthetic-combined", str(source))
        drive = imports.store.find_by_serial("SYN-IMPORT-SHIP")
        assert drive is not None
        location = "/drive/" + drive.id
        detail = self.client.get(location).text
        self.assertIn("Unknown ownership", detail)
        self.assertIn("Custody status: in custody", detail)
        self.assertIn("Synthetic show", detail)
        assignment = imports.store.assignment_history(drive.id)
        self.assertEqual(assignment[0].project.page_id, OLD)
        evidence = imports.evidence("synthetic-combined", exceptions_only=False)
        response = self.client.post(
            "/shipments/new",
            data={"csrf": self.csrf, "drive_id": drive.id, "project_page_id": OLD},
        )
        self.assertEqual(response.status_code, 303)
        shipment = response.headers["Location"]
        saved = self.client.get(shipment).data
        for value in (drive.id, "SYN-IMPORT-SHIP", "Synthetic show"):
            self.assertIn(value, saved.decode())
        self.assertIn("Unknown ownership", self.client.get(location).text)
        self.assertIn("Custody status: released", self.client.get(location).text)
        self.assertEqual(imports.store.assignment_history(drive.id), assignment)
        self.assertEqual(len(imports.store.list_events(drive.id)), 2)
        InventoryImports(self.path).run("synthetic-combined", str(source))
        self.assertEqual(
            InventoryImports(self.path).evidence("synthetic-combined", exceptions_only=False),
            evidence,
        )
        self.assertEqual(len(imports.store.list_events(drive.id)), 2)
        self.reader.payload = export(NEW, (OLD,), name="Synthetic renamed cache")
        self.assertEqual(self.app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0)
        self.assertIn("Synthetic renamed cache", self.client.get(location).text)
        restarted = create_app(self.path).test_client()
        self.assertEqual(restarted.get(shipment).data, saved)
        self.assertIn("Unknown ownership", restarted.get(location).text)

    def post(self, suffix, **fields):
        response = self.client.post(self.location + suffix, data={"csrf": self.csrf, **fields})
        self.assertEqual(response.status_code, 303, response.text)

    def section(self, pattern):
        match = re.search(pattern, self.client.get(self.location).text, re.DOTALL)
        assert match is not None
        return match.group()

    def assignment_history(self):
        return self.section(r"<section><h2>Project use / Assignment history</h2>.*?</section>")

    def custody_history(self):
        return self.section(r'<section id="custody-history">.*?</section>')

    def test_outage_and_mutations_keep_assignment_and_custody_independent(self):
        self.post("/assignments", project_page=OLD, label="retained synthetic label")
        self.reader.payload = RuntimeError("private upstream failure")
        self.assertNotEqual(
            self.app.test_cli_runner().invoke(args=["refresh-projects"]).exit_code, 0
        )
        page = self.client.get("/").text
        self.assertIn("Refresh failed", page)
        self.assertNotIn("private upstream failure", page)
        assignments = self.assignment_history()
        for kind, status in (
            ("received", "in custody"),
            ("released", "released"),
            ("returned", "in custody"),
        ):
            self.post("/custody", kind=kind, project_text="SYN")
            self.assertIn("Custody status: " + status, self.client.get(self.location).text)
            self.assertEqual(assignments, self.assignment_history())
        custody = self.custody_history()
        self.post("/assignments", project_text="SYN", label="unresolved label")
        self.assertEqual(custody, self.custody_history())
        key = re.search(r'data-assignment-id="(\d+)"', self.assignment_history())
        assert key is not None
        self.post("/assignments/" + key[1] + "/end", end="2024-03-11")
        self.assertEqual(custody, self.custody_history())
        self.assertIn("Custody status: in custody", self.client.get(self.location).text)
        self.assertIn("Unknown actor (unknown)", custody)
        restarted = create_app(self.path).test_client().get(self.location).text
        self.assertIn(custody, restarted)
        self.assertIn("End: 2024-03-11", restarted)
        self.assertIn("End: Open", restarted)
        self.assertIn("Unknown ownership", restarted)

    def test_combined_filters_intersect_open_use_status_review_and_search(self):
        self.post("/assignments", project_page=OLD)
        self.post("/assignments", project_text="SYN")
        self.post("/custody", kind="released")
        filters = {
            "project": PROJECT,
            "project_text": "SYN",
            "status": "released",
            "review": "yes",
            "q": "combined",
        }
        self.assertIn(self.location, self.client.get("/", query_string=filters).text)
        for field, value in (
            ("project", "t6p_" + "2" * 32),
            ("project_text", "missing"),
            ("status", "in custody"),
            ("review", "no"),
            ("q", "missing"),
            ("serial", "missing"),
        ):
            with self.subTest(field=field):
                self.assertNotIn(
                    self.location, self.client.get("/", query_string={**filters, field: value}).text
                )

    def test_navigation_and_fact_edits_preserve_both_histories_and_revisions(self):
        self.post("/assignments", project_page=OLD, label="retained synthetic label")
        self.post("/custody", kind="received")
        page = self.client.get(self.location).text
        for href in ("/", self.location + "/edit", self.location + "/custody"):
            self.assertIn('href="' + href + '"', page)
            self.assertEqual(self.client.get(href).status_code, 200)
        self.assertIn(
            'href="' + self.location + '"', self.client.get(self.location + "/custody").text
        )
        self.assertIn("Add Assignment", page)
        assignment, custody = self.assignment_history(), self.custody_history()
        stale = edit_data(self.client, self.location, description="stale")
        fresh = edit_data(
            self.client, self.location, description="synthetic edited facts", needs_review="on"
        )
        self.assertEqual(self.client.post(self.location + "/edit", data=fresh).status_code, 303)
        before = self.client.get(self.location).text
        self.assertEqual(self.client.post(self.location + "/edit", data=stale).status_code, 409)
        self.assertEqual(
            self.client.post(
                self.location + "/edit", data={"csrf": self.csrf, "description": "missing revision"}
            ).status_code,
            409,
        )
        self.assertEqual(before, self.client.get(self.location).text)
        self.assertEqual(assignment, self.assignment_history())
        self.assertEqual(custody, self.custody_history())
        restarted = create_app(self.path).test_client().get(self.location).text

        # A fresh app/session has a new CSRF token; compare history without form secrets.
        def normalize(text):
            return re.sub(r'name="csrf" value="[^"]+"', 'name="csrf"', text)

        for retained in (assignment, custody, "Unknown ownership", "synthetic edited facts"):
            self.assertIn(normalize(retained), normalize(restarted))
