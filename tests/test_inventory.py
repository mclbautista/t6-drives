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


if __name__ == "__main__":
    unittest.main()
