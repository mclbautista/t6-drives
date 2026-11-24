"""Presentation only: every page shares one shell, its stylesheet and its navigation."""

import unittest

from support import DriveAppCase

NAV = ("Search", "Projects", "Movements", "Exceptions", "Outbox")


class DesignTests(DriveAppCase):
    def test_every_page_has_the_shell_the_viewport_the_stylesheet_and_the_nav(self):
        drive = self.api.add_drive(label="Synthetic design drive", serial="SYN-D")
        urls = ["/", "/new", f"/drive/{drive}", f"/drive/{drive}/edit", f"/drive/{drive}/custody"]
        for url in [*urls, "/shipments", "/shipments/new", "/exceptions", "/outbox"]:
            response = self.web.get(url)
            html = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn('<html lang="en">', html, url)
            self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', html)
            self.assertIn("<style>", html, url)
            self.assertIn("prefers-color-scheme: dark", html, url)
            for name in NAV:
                self.assertIn(f">{name}</a>", html, url)

    def test_the_current_page_is_marked_in_the_nav(self):
        self.assertIn('<a href="/outbox" aria-current="page">', self.text("/outbox"))

    def test_notice_queued_and_unavailable_pages_keep_their_roles_and_the_shell(self):
        drive = self.api.add_drive(label="Synthetic")
        form = f"/drive/{drive}/custody"
        pages = [
            (self.submit(form, kind="retired", project="t6p_" + "0" * 24), 'role="alert"'),
        ]
        hidden = self.fields("/new")
        self.api.mode = "down"
        pages.append((self.web.get("/"), 'role="alert"'))
        pages.append((self.web.post("/new", data={**hidden, "label": "Queued"}), 'role="status"'))
        for response, role in pages:
            html = response.get_data(as_text=True)
            self.assertIn(role, html)
            self.assertIn("<style>", html)
            self.assertIn(">Outbox</a>", html)
        self.assertEqual([r.status_code for r, _ in pages], [400, 503, 202])


if __name__ == "__main__":
    unittest.main()


class SearchNavigationTests(DriveAppCase):
    def test_recording_leaves_navigation_and_retire_stays_on_the_drive(self):
        drive = self.api.add_drive(label="Synthetic navigation drive")
        html = self.text(f"/drive/{drive}")
        self.assertNotIn(">Record custody</a>", html)
        self.assertNotIn(">Release shipment</a>", html)
        self.assertIn(f'href="/drive/{drive}/retire"', html)
        self.assertIn(">Retire</a>", html)
