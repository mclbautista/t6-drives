"""Search-first pages against the real t6 verb API."""

import re
from urllib.parse import urlencode

from support import DriveAppCase
from t6.verbs import Done, DriveListInput, DriveReleaseInput, Preview, ProjectListInput


class SearchTests(DriveAppCase):
    def test_search_finds_partial_label_and_serial(self):
        drive = self.api.add_drive(label="Synthetic violet master", serial="SYN-VIOLET-01")
        other = self.api.add_drive(label="Synthetic unrelated")
        for query in ("violet", "SYN-VIOLET-01"):
            with self.subTest(query=query):
                html = self.text("/?" + urlencode({"q": query}))
                self.assertIn(f'href="/drive/{drive}"', html)
                self.assertNotIn(f'href="/drive/{other}"', html)

    def test_search_finds_project_code(self):
        project = self.api.project("Synthetic search project")
        drive = self.api.add_drive(label="Synthetic violet master", serial="SYN-VIOLET-01")
        other = self.api.add_drive(label="Synthetic unrelated")
        self.api.assign(drive, project, "Synthetic master", "master")
        self.api.registry.set_code(
            project,
            "SYN-SEARCH",
            actor="setup",
            idempotency_key="synthetic-code",
            expected_revision=1,
        )
        projects = self.api.read("project_list", ProjectListInput)
        code = next(p["project_code"] for p in projects["projects"] if p["t6_id"] == project)
        html = self.text("/?" + urlencode({"q": code}))
        self.assertIn(f'href="/drive/{drive}"', html)
        self.assertNotIn(f'href="/drive/{other}"', html)

    def test_result_shows_the_party_of_the_last_release(self):
        project = self.api.project("Synthetic release project")
        drive = self.api.add_drive(label="Synthetic released master", office_location="SHELF")
        self.api.write(
            "drive_release",
            DriveReleaseInput,
            drives=[drive],
            project=project,
            party="Invented Recipient",
            party_kind="contact",
        )
        html = self.text("/?q=released")
        row = re.search(r"<li>.*?</li>", html, re.DOTALL)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertIn("Invented Recipient", row.group())
        self.assertIn("released", row.group())

    def test_new_drive_checks_the_serial_before_asking_for_facts(self):
        drive = self.api.add_drive(label="Synthetic known drive", serial="SYN-KNOWN")
        html = self.text("/new")
        self.assertIn('name="serial"', html)
        self.assertNotIn('name="label"', html)
        response = self.submit("/new", serial=" syn-known ")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/drive/" + drive)
        self.assertEqual(len(self.api.drives()), 1)

    def test_unknown_serial_continues_to_facts_before_creating_the_drive(self):
        response = self.submit("/new", serial="SYN-NEW-SERIAL")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="label"', html)
        self.assertIn('value="SYN-NEW-SERIAL"', html)
        self.assertEqual(self.api.drives(), [])
        hidden = dict(re.findall(r'<input type="hidden" name="(csrf|key)" value="([^"]*)"', html))
        saved = self.submit(
            "/new", hidden=hidden, label="Synthetic new master", serial="SYN-NEW-SERIAL"
        )
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(self.only_drive()["serial"], "SYN-NEW-SERIAL")

    def test_practice_banner_follows_the_api_on_every_page(self):
        from t6.drives.practice import apply_live_setting

        banner = "Practice: records are wiped at the next reload"
        drive = self.api.add_drive(label="Synthetic practice drive")
        pages = [
            "/",
            "/new",
            f"/drive/{drive}",
            f"/drive/{drive}/edit",
            f"/drive/{drive}/custody",
            f"/drive/{drive}/retire",
            "/shipments",
            "/shipments/new",
            "/exceptions",
            "/outbox",
        ]
        for url in pages:
            self.assertIn(banner, self.text(url), url)
        # ADR 0010: the API goes live when it starts with T6_DRIVE_LIVE=true; nothing is reloaded.
        apply_live_setting(self.api.registry, {"T6_DRIVE_LIVE": "true"})
        self.assertFalse(self.api.read("drive_list", DriveListInput)["practice"])
        final_drive = self.only_drive()["id"]
        for url in (
            "/",
            "/new",
            "/exceptions",
            "/outbox",
            "/shipments",
            "/shipments/new",
            f"/drive/{final_drive}",
            f"/drive/{final_drive}/edit",
            f"/drive/{final_drive}/retire",
        ):
            self.assertNotIn(banner, self.text(url), url)
