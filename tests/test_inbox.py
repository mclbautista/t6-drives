"""Inbox pages against the synthetic HTTP API, using core's real capture and approval verbs."""

import re

from support import PHOTO, DriveAppCase
from t6.verbs import Done, DriveApproveInput, DriveInboxInput, MovementListInput
from test_plain_pages import ID_PATTERN, visible


class InboxTests(DriveAppCase):
    def test_a_text_only_item_links_to_its_topic_and_has_no_broken_photo(self):
        self.api.waiting_capture("IN\nSynthetic text case")
        answer = self.api.read("drive_inbox", DriveInboxInput)
        answer["items"][0].update(photo_reference=None, topic_id=23, message_id=42)
        self.api.answers["/v1/drive/inbox"] = Done(result=answer).model_dump(mode="json")
        html = self.text("/inbox")
        self.assertIn("No photo is kept.", html)
        self.assertNotIn('<img class="capture-photo"', html)
        self.assertIn('href="https://t.me/c/1/23/42"', html)
        self.assertNotIn("23", visible(html))
        self.assertNotIn("42", visible(html))

    def test_invalid_choices_and_conflicting_settlements_show_a_refusal_without_another_drive(self):
        capture = self.api.waiting_capture("IN\nSynthetic guarded case")
        url = f"/inbox/{capture}/0/approve"
        hidden = self.fields("/inbox")
        self.assertEqual(self.web.post(url, data={"choice": "new"}).status_code, 403)
        invalid = self.submit(url, hidden=hidden, choice="unknown")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Check the values", visible(invalid.get_data(as_text=True)))
        self.assertEqual(self.api.drives(), [])
        self.assertEqual(self.submit(url, hidden=hidden, choice="new").status_code, 303)
        refused = self.submit(url, hidden=hidden, choice="not_a_drive")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("already settled", visible(refused.get_data(as_text=True)))
        self.assertNotIn(capture, visible(refused.get_data(as_text=True)))
        self.assertEqual(len(self.api.drives()), 1)

    def test_approval_while_api_is_down_waits_to_send_in_plain_words_and_lands_once(self):
        capture = self.api.waiting_capture("IN\nSynthetic delayed case")
        hidden = self.fields("/inbox")
        self.api.mode = "down"
        self.assertEqual(self.web.get("/inbox").status_code, 503)
        response = self.submit(f"/inbox/{capture}/0/approve", hidden=hidden, choice="new")
        self.assertEqual(response.status_code, 202)
        text = visible(self.text("/outbox"))
        self.assertIn("Inbox choice", text)
        self.assertNotIn(capture, text)
        self.assertNotIn("drive_approve", text)
        self.api.mode = "up"
        self.assertIn("Nothing to approve.", self.text("/inbox"))
        self.assertEqual(self.only_drive()["label"], "Synthetic delayed case")
        self.assertIn("Nothing is waiting", self.text("/outbox"))
        self.assertEqual(len(self.api.drives()), 1)

    def test_api_words_and_ids_are_plain_in_item_caption_recipient_and_guesses(self):
        drive = self.api.add_drive(label="Synthetic t6d_" + "a" * 32)
        self.api.waiting_capture("OUT\nSynthetic case\nR: Invented Courier")
        answer = self.api.read("drive_inbox", DriveInboxInput)
        item = answer["items"][0]
        item.update(
            text="Assignment t6d_" + "b" * 32,
            caption="OUT\nAssignment t6d_" + "b" * 32,
            recipient="[person t6p_" + "c" * 32 + "]",
            guesses=[
                {
                    "drive": drive,
                    "confidence": 0.5,
                    "reason": "Accepted serial t6d_" + "a" * 32,
                    "exact": False,
                }
            ],
        )
        self.api.answers["/v1/drive/inbox"] = Done(result=answer).model_dump(mode="json")
        text = visible(self.text("/inbox"))
        self.assertNotRegex(text, ID_PATTERN)
        self.assertNotIn("Assignment", text)
        self.assertNotIn("Accepted serial", text)
        self.assertNotIn("[person", text)

    def test_the_photo_of_an_unknown_or_malformed_capture_is_not_found(self):
        self.assertEqual(self.web.get("/captures/capture_" + "0" * 32 + "/photo").status_code, 404)
        self.assertEqual(self.web.get("/captures/..%2Fx/photo").status_code, 404)

    def test_an_empty_inbox_has_no_approval_forms(self):
        html = self.text("/inbox")
        self.assertIn("Nothing to approve.", html)
        self.assertNotIn("<form", html)

    def test_guesses_show_drive_names_confidence_reasons_and_exact_marks_and_can_be_picked(self):
        first = self.api.add_drive(label="Synthetic blue master", serial="SYN-BLUE")
        second = self.api.add_drive(label="Synthetic blue backup", serial="SYN-BACKUP")
        capture = self.api.waiting_capture("IN\nSynthetic blue case")
        answer = self.api.read("drive_inbox", DriveInboxInput)
        answer["items"][0]["guesses"] = [
            {"drive": first, "confidence": 0.95, "reason": "Same label", "exact": True},
            {"drive": second, "confidence": 0.6, "reason": "Similar case", "exact": False},
        ]
        self.api.answers["/v1/drive/inbox"] = Done(result=answer).model_dump(mode="json")
        html = self.text("/inbox")
        text = visible(html)
        for words in ("✓", "Exact", "95%", "60%", "Same label", "Similar case"):
            self.assertIn(words, text)
        self.assertIn("Synthetic blue master", text)
        self.assertIn("Synthetic blue backup", text)
        self.assertNotRegex(text, ID_PATTERN)
        self.assertIn(f'name="drive" value="{second}"', html)
        response = self.submit(
            f"/inbox/{capture}/0/approve", form="/inbox", choice="drive", drive=second
        )
        self.assertEqual(response.status_code, 303)
        del self.api.answers["/v1/drive/inbox"]
        self.assertIn("Approved", self.text("/inbox"))
        self.assertNotIn(f'data-capture="{capture}"', self.text("/inbox"))
        log = self.api.read("movement_list", MovementListInput)["movements"]
        self.assertEqual([(m["drive"], m["direction"]) for m in log], [(second, "IN")])

    def test_none_keeps_the_item_waiting_quietly_and_can_be_repeated(self):
        capture = self.api.waiting_capture("IN\nSynthetic uncertain case")
        self.assertIn('value="none">None</button>', self.text("/inbox"))
        url = f"/inbox/{capture}/0/approve"
        hidden = self.fields("/inbox")
        for _ in range(2):
            self.assertEqual(self.submit(url, hidden=hidden, choice="none").status_code, 303)
            html = self.text("/inbox")
            self.assertIn("Left waiting. You can choose later.", html)
            self.assertIn(f'data-capture="{capture}"', html)
            self.assertNotRegex(html, r'<[^>]+role="alert"')
        self.assertEqual(self.api.drives(), [])

    def test_not_a_drive_closes_only_the_chosen_item_and_shows_the_result(self):
        capture = self.api.waiting_capture("IN\nSynthetic case\nSynthetic cable")
        html = self.text("/inbox")
        self.assertIn('value="not_a_drive">Not a drive</button>', html)
        response = self.submit(f"/inbox/{capture}/1/approve", form="/inbox", choice="not_a_drive")
        self.assertEqual(response.status_code, 303)
        html = self.text("/inbox")
        self.assertIn("Marked as not a drive.", html)
        self.assertIn("Synthetic case", html)
        self.assertNotIn("<h2>Synthetic cable</h2>", html)
        self.assertEqual(self.api.drives(), [])

    def test_type_it_accepts_a_label_or_serial_and_shows_a_missing_drive_refusal(self):
        drive = self.api.add_drive(label="Synthetic typed case", serial="SYN-TYPED")
        for number, typed in enumerate(("Synthetic typed case", "SYN-TYPED"), 1):
            capture = self.api.waiting_capture("IN\nUncertain case", message=number)
            url = f"/inbox/{capture}/0/approve"
            refused = self.submit(url, form="/inbox", choice="drive", typed="No such drive")
            self.assertEqual(refused.status_code, 400)
            self.assertIn("Drive not found", refused.get_data(as_text=True))
            self.assertIn(f'data-capture="{capture}"', self.text("/inbox"))
            response = self.submit(url, form="/inbox", choice="drive", typed=typed)
            self.assertEqual(response.status_code, 303)
            self.assertIn("Approved", self.text("/inbox"))
            self.assertNotIn(f'data-capture="{capture}"', self.text("/inbox"))
        log = self.api.read("movement_list", MovementListInput, drive=drive)["movements"]
        self.assertEqual(len(log), 2)
        self.assertEqual([m["direction"] for m in log], ["IN", "IN"])

    def test_new_drive_approves_the_item_shows_the_result_and_does_not_duplicate_on_repeat(self):
        capture = self.api.waiting_capture("IN\nSynthetic new case")
        url = f"/inbox/{capture}/0/approve"
        hidden = self.fields("/inbox")
        response = self.submit(url, hidden=hidden, choice="new")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/inbox")
        self.assertIn("Approved", self.text("/inbox"))
        self.assertIn("Nothing to approve.", self.text("/inbox"))
        self.assertEqual(self.only_drive()["label"], "Synthetic new case")
        self.assertEqual(self.submit(url, hidden=hidden, choice="new").status_code, 303)
        self.assertEqual(len(self.api.drives()), 1)
        self.assertIn("/v1/drive/approve", self.api.requests)

    def test_item_shows_photo_caption_post_link_and_escaped_text(self):
        caption = "OUT\nSynthetic <b>case</b>\nR: Invented Courier"
        capture = self.api.waiting_capture(caption)
        html = self.text("/inbox")
        self.assertIn(f'<img class="capture-photo" src="/captures/{capture}/photo"', html)
        self.assertIn("2026-11-02", html)
        self.assertIn("Synthetic &lt;b&gt;case&lt;/b&gt;", html)
        self.assertIn("OUT\nSynthetic &lt;b&gt;case&lt;/b&gt;\nR: Invented Courier", html)
        self.assertNotIn("<b>case</b>", html)
        self.assertIn('href="https://t.me/c/1/1"', html)
        photo = self.web.get(f"/captures/{capture}/photo")
        self.assertEqual(photo.data, PHOTO)
        self.assertEqual(photo.headers["Content-Type"], "image/png")
        self.assertEqual(photo.headers["Cache-Control"], "no-store")
        self.assertEqual(photo.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", photo.headers["Content-Security-Policy"])

    def test_waiting_items_are_newest_first_in_caption_order_and_settled_items_are_hidden(self):
        older = self.api.waiting_capture("IN\nSynthetic older", day="2026-11-01")
        newer = self.api.waiting_capture(
            "OUT\nSynthetic first\nSynthetic second\nR: Invented Courier",
            message=2,
            day="2026-11-03",
        )
        settled = self.api.waiting_capture("IN\nSynthetic settled", message=3, day="2026-11-04")
        done = self.api.client().drive_approve(
            DriveApproveInput(capture=settled, item=0, choice="not_a_drive")
        )
        self.assertIsInstance(done, Done)
        response = self.web.get("/inbox")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertEqual(re.findall(r'data-capture="([^"]+)"', html), [newer, newer, older])
        self.assertLess(html.index("Synthetic first"), html.index("Synthetic second"))
        self.assertNotIn("Synthetic settled", html)
        self.assertIn("OUT", html)
        self.assertIn("Invented Courier", html)
