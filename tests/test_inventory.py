"""The inventory screen and Drive pages against the test API."""

import unittest

from support import DriveAppCase


class InventoryTests(DriveAppCase):
    def seed(self):
        """A is in custody, flagged and out for the first Project; B is released; C is untouched."""
        one, two = self.api.project("Synthetic one"), self.api.project("Synthetic two")
        a = self.api.add_drive(
            label="Synthetic A",
            serial="SYNTH-A",
            suspected_serial="MAYBE-9",
            needs_review=True,
            review_note="check serial",
        )
        b = self.api.add_drive(label="Synthetic B", serial="SYNTH-B", description="blue box")
        c = self.api.add_drive(label="Synthetic C")
        self.api.receive(a, one, on="2026-11-01")
        self.api.assign(a, one, "A1", "master")
        from t6.verbs import DriveReleaseInput

        self.api.write(
            "drive_release",
            DriveReleaseInput,
            drives=[b],
            project=two,
            party="Invented Studio",
            party_kind="studio",
        )
        return one, two, a, b, c

    def shown(self, query=""):
        html = self.text("/" + query)
        return [d for d in self.ids if f'href="/drive/{d}"' in html]

    def test_every_drive_is_listed_with_its_custody_status_and_uncertain_serial_marked(self):
        _, _, a, b, c = self.seed()
        self.ids = [a, b, c]
        html = self.text("/")
        self.assertEqual(self.shown(), [a, b, c])
        self.assertIn("Custody status: in custody", html)
        self.assertIn("Custody status: released", html)
        self.assertIn("Custody status: unknown", html)
        self.assertIn("Accepted: SYNTH-A", html)
        self.assertIn("Suspected (uncertain): MAYBE-9", html)
        self.assertIn("Needs review", html)

    def test_filters_combine_and_match_the_inventory_screen_rules(self):
        one, _, a, b, c = self.seed()
        self.ids = [a, b, c]
        self.assertEqual(self.shown("?q=maybe"), [a])
        self.assertEqual(self.shown("?q=SYNTHETIC+B"), [b])
        self.assertEqual(self.shown("?q=blue+box"), [b])
        self.assertEqual(self.shown(f"?q={c}"), [c])
        self.assertEqual(self.shown("?serial=+synth-b+"), [b])
        self.assertEqual(self.shown("?serial=MAYBE-9"), [])
        self.assertEqual(self.shown("?review=yes"), [a])
        self.assertEqual(self.shown("?review=no"), [b, c])
        self.assertEqual(self.shown("?status=released"), [b])
        self.assertEqual(self.shown("?status=unknown"), [c])
        self.assertEqual(self.shown(f"?project={one}"), [a])
        self.assertEqual(self.shown(f"?project={one}&status=released"), [])
        self.assertEqual(self.shown(f"?project={one}&review=yes&status=in+custody&q=synth"), [a])

    def test_each_drive_shows_its_open_project_use_without_a_read_per_drive(self):
        _, two, a, b, c = self.seed()
        self.api.assign(a, two, "A2", "backup")
        self.api.legacy_assignment(c, "SYN")
        self.api.requests.clear()
        html = self.text("/")
        rows = {d: html[html.index(f'href="/drive/{d}"') :].split("</li>")[0] for d in (a, b, c)}
        for shown in ("Synthetic one", "A1", "master", "Synthetic two", "A2", "backup"):
            self.assertIn(shown, rows[a])
        self.assertIn("Open Project use: none", rows[b])
        self.assertIn("Unresolved: SYN", rows[c])
        self.assertNotIn("/v1/drive/get", self.api.requests)
        self.assertEqual(self.api.requests.count("/v1/drive/list"), 1)

    def test_an_ended_assignment_is_not_open_project_use(self):
        _, _, a, *_ = self.seed()
        from t6.verbs import DriveUnassignInput

        self.api.write("drive_unassign", DriveUnassignInput, drive=a, assignment=1)
        self.assertIn("Open Project use: none", self.text("/"))

    def test_project_filter_offers_the_registered_projects_from_the_api(self):
        one, two, *_ = self.seed()
        html = self.text("/")
        self.assertIn(f'value="{one}"', html)
        self.assertIn("Synthetic one", html)
        self.assertIn(f'value="{two}"', html)

    def test_inventory_links_to_every_other_screen(self):
        html = self.text("/")
        for link in ("/new", "/shipments", "/shipments/new", "/exceptions", "/outbox"):
            self.assertIn(f'href="{link}"', html)

    def test_names_from_the_api_are_escaped(self):
        self.api.add_drive(label="<script>alert(1)</script>")
        html = self.text("/")
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_inventory_pages_drives_and_resets_page_on_filter_chips(self):
        self.ids = [
            self.api.add_drive(
                label=f"Paged Drive {i:02d}",
                needs_review=(i % 2 == 0),
            )
            for i in range(105)
        ]
        page1 = self.text("/")
        self.assertIn("Showing 1&ndash;50 of 105 Drives", page1)
        self.assertNotIn("matching filters", page1)
        self.assertIn("Page 1 of 3", page1)
        self.assertIn('href="/?page=2"', page1)
        self.assertEqual(len(self.shown()), 50)
        self.assertEqual(self.shown(), self.ids[:50])

        page2 = self.text("/?page=2")
        self.assertIn("Showing 51&ndash;100 of 105 Drives", page2)
        self.assertNotIn("matching filters", page2)
        self.assertIn("Page 2 of 3", page2)
        self.assertIn('href="/"', page2)
        self.assertIn('href="/?page=3"', page2)
        self.assertEqual(self.shown("?page=2"), self.ids[50:100])
        self.assertIn('href="/?review=yes"', page2)
        self.assertNotIn("page=2&amp;review=yes", page2)

        last = self.text("/?page=999")
        self.assertIn("Showing 101&ndash;105 of 105 Drives", last)
        self.assertIn("Page 3 of 3", last)
        self.assertEqual(self.shown("?page=3"), self.ids[100:])

        invalid = self.text("/?page=notanumber")
        self.assertIn("Page 1 of 3", invalid)

        filtered = self.text("/?review=yes&page=2")
        self.assertIn("Showing 51&ndash;53 of 53 Drives matching filters", filtered)
        self.assertIn("Page 2 of 2", filtered)
        self.assertIn('href="/?review=yes"', filtered)

    def test_project_list_is_cached_across_inventory_requests(self):
        project = self.api.project("Synthetic cached")
        drive = self.api.add_drive(label="Cached Drive")
        self.api.assign(drive, project, "A1", "master")
        self.api.requests.clear()
        first = self.text("/")
        second = self.text("/")
        self.assertIn("Synthetic cached", first)
        self.assertIn("Synthetic cached", second)
        self.assertEqual(self.api.requests.count("/v1/project/list"), 1)
        self.assertEqual(self.api.requests.count("/v1/drive/list"), 2)

    def test_inventory_requests_only_the_current_page_from_the_api(self):
        import json
        from unittest.mock import patch

        for i in range(55):
            self.api.add_drive(label=f"Paged API {i:02d}")
        seen: list[dict[str, object]] = []
        original = self.api.api

        def capture(request):
            if request.path == "/v1/drive/list":
                seen.append(json.loads(request.body or b"{}"))
            return original(request)

        with patch.object(self.api, "api", side_effect=capture):
            self.text("/")
            self.text("/?page=2")
        self.assertEqual(seen, [{"limit": 50, "offset": 0}, {"limit": 50, "offset": 50}])


if __name__ == "__main__":
    unittest.main()
