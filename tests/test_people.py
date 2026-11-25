"""The person picker: Contacts found by name or handle, and a new person added after a confirm."""

import re
import unittest

from support import DriveAppCase
from t6.notion.testing import rich_text, title
from t6.verbs import ContactFindInput, MovementListInput


class PersonPickerTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.api.notion.add_page(
            "contacts",
            {"Name": title("Synthetic Ana Cruz"), "TelegramHandle": rich_text("@synana")},
        )
        self.api.notion.add_page("contacts", {"Name": title("Synthetic Ben Reyes")})
        self.drive = self.api.add_drive(label="Synthetic master", serial="SYN-M")
        self.api.capture("OUT\nSYN-M\nR: Typed Courier")

    def found(self, q):
        response = self.web.get("/people/find", query_string={"q": q})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["people"]

    def contacts(self):
        return [c["name"] for c in self.api.read("contact_find", ContactFindInput)["contacts"]]

    def movement(self):
        return self.api.read("movement_list", MovementListInput)["movements"][0]["id"]

    def test_it_finds_contacts_by_name_or_by_handle(self):
        self.assertEqual([p["name"] for p in self.found("cruz")], ["Synthetic Ana Cruz"])
        self.assertEqual([p["name"] for p in self.found("@synana")], ["Synthetic Ana Cruz"])
        self.assertEqual(self.found("nobody at all"), [])
        self.assertEqual(
            sorted(p["name"] for p in self.found("")), ["Synthetic Ana Cruz", "Synthetic Ben Reyes"]
        )

    def test_the_picker_is_on_the_correction_form_and_the_movements_person_filter(self):
        for url in (f"/movements/{self.movement()}/correct", "/movements"):
            html = self.text(url)
            self.assertRegex(html, r'<input[^>]*name="person"[^>]*data-person-picker', url)
            self.assertIn('href="/people/new?back=', html)

    def test_without_a_script_the_picker_is_still_a_text_box_with_a_list_to_fill(
        self,
    ):
        html = self.text("/movements")
        self.assertIn('list="people-list"', html)
        self.assertIn('<datalist id="people-list">', html)

    def test_a_movement_loaded_with_a_typed_name_keeps_it_on_the_correction_form(self):
        html = self.text(f"/movements/{self.movement()}/correct")
        self.assertIn('value="Typed Courier"', html)
        self.assertNotIn('Synthetic Ana Cruz" selected', html)

    def test_adding_a_person_previews_then_creates_after_the_confirm(self):
        before = self.contacts()
        shown = self.submit(
            "/people/new/preview",
            form="/people/new",
            name="Synthetic Newcomer",
            telegram_handle="@newcomer",
            phone="0000",
            back="/movements",
        )
        html = shown.get_data(as_text=True)
        self.assertIn("Synthetic Newcomer", html)
        self.assertIn("Added from Drives", html)
        self.assertEqual(self.contacts(), before)
        hidden = dict(re.findall(r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"', html))
        done = self.web.post("/people/new", data=hidden)
        self.assertEqual(done.status_code, 303)
        self.assertEqual(done.headers["Location"], "/movements?person=Synthetic+Newcomer")
        self.assertIn("Synthetic Newcomer", self.contacts())
        self.assertEqual([p["name"] for p in self.found("newcomer")], ["Synthetic Newcomer"])

    def test_a_blank_name_and_a_duplicate_are_refused_in_plain_words_and_nothing_is_added(self):
        before = self.contacts()
        for name, words in (("   ", "Give a name"), ("synthetic ana cruz", "already exists")):
            response = self.submit("/people/new/preview", form="/people/new", name=name, back="/")
            self.assertEqual(response.status_code, 400)
            html = response.get_data(as_text=True)
            self.assertIn(words, html)
            self.assertIn('role="alert"', html)
        self.assertEqual(self.contacts(), before)

    def test_the_way_back_is_only_ever_a_page_of_this_app(self):
        shown = self.submit(
            "/people/new/preview",
            form="/people/new",
            name="Synthetic Elsewhere",
            back="//evil.test/x",
        )
        hidden = dict(
            re.findall(
                r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"',
                shown.get_data(as_text=True),
            )
        )
        done = self.web.post("/people/new", data=hidden)
        self.assertTrue(done.headers["Location"].startswith("/"))
        self.assertFalse(done.headers["Location"].startswith("//"))

    def test_a_person_added_while_the_api_is_down_waits_and_is_added_once(self):
        shown = self.submit(
            "/people/new/preview", form="/people/new", name="Synthetic Later", back="/"
        )
        hidden = dict(
            re.findall(
                r'<input type="hidden" name="([a-z_]+)" value="([^"]*)"',
                shown.get_data(as_text=True),
            )
        )
        self.api.mode = "down"
        self.assertEqual(self.web.post("/people/new", data=hidden).status_code, 202)
        self.api.mode = "up"
        self.web.get("/")
        self.web.get("/")
        self.assertEqual(self.contacts().count("Synthetic Later"), 1)


if __name__ == "__main__":
    unittest.main()
