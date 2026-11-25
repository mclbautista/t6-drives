"""An optional photo on a movement: added with a correction or on the movement page, replaced, deleted (#40)."""

import io
import os
from typing import Any
from unittest.mock import patch

from support import PHOTO, DriveAppCase
from t6.verbs import MovementListInput, MovementPhotoRefInput

from outbox import Outbox

JPEG = b"\xff\xd8\xff\xe0" + b"synthetic jpeg body"
TEN_MB = 10 * 1024 * 1024


class PhotoCase(DriveAppCase):
    def setUp(self) -> None:
        super().setUp()
        env = patch.dict(os.environ, {"T6_DRIVE_PHOTO_DIR": self.directory + "/photos"})
        env.start()
        self.addCleanup(env.stop)
        self.drive = self.api.add_drive(label="Synthetic master", serial="SYN-M")
        self.api.capture("OUT\nSYN-M\nR: Invented Courier")
        self.movement = self.movements()[0]["id"]

    def movements(self) -> list[dict[str, Any]]:
        return list(
            self.api.read("movement_list", MovementListInput, drive=self.drive)["movements"]
        )

    def stored(self, movement: str) -> dict[str, Any]:
        return self.api.read("movement_photo_get", MovementPhotoRefInput, movement=movement)

    def has_photo(self, movement: str) -> bool:
        return any(m["id"] == movement and m.get("photo") == "upload" for m in self.movements())

    def upload(
        self, url: str, data: bytes, name: str = "photo.png", form: str | None = None, **values: Any
    ):
        hidden = self.fields(form or url)
        return self.web.post(
            url,
            data={**hidden, **values, "photo": (io.BytesIO(data), name)},
            content_type="multipart/form-data",
        )


class CorrectionFormPhotoTests(PhotoCase):
    def test_the_correction_form_offers_an_optional_photo(self):
        html = self.text(f"/movements/{self.movement}/correct")
        self.assertIn('enctype="multipart/form-data"', html)
        field = html[html.index('name="photo"') - 200 : html.index('name="photo"') + 200]
        self.assertIn('type="file"', field)
        self.assertIn('accept="image/jpeg,image/png,image/webp"', field)
        self.assertNotIn("required", field)
        self.assertIn("Add a photo (optional)", html)

    def test_a_photo_sent_with_a_correction_goes_on_the_new_entry(self):
        response = self.upload(f"/movements/{self.movement}/correct", PHOTO, direction="IN")
        self.assertEqual(response.status_code, 303)
        corrected = self.movements()[1]
        self.assertEqual(corrected["corrects"], self.movement)
        self.assertTrue(self.has_photo(corrected["id"]))
        self.assertFalse(self.has_photo(self.movement))
        self.assertEqual(self.stored(corrected["id"])["media_type"], "image/png")

    def test_a_photo_alone_goes_on_the_entry_itself_without_a_correction(self):
        response = self.upload(f"/movements/{self.movement}/correct", PHOTO)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], f"/movements/{self.movement}")
        self.assertEqual(len(self.movements()), 1)
        self.assertTrue(self.has_photo(self.movement))

    def test_a_bad_file_stops_the_correction_too(self):
        response = self.upload(
            f"/movements/{self.movement}/correct", b"plain text", name="notes.txt", direction="IN"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("A photo must be a JPEG, PNG or WebP image.", response.get_data(as_text=True))
        self.assertEqual(len(self.movements()), 1)


class MovementPageTests(PhotoCase):
    def test_add_replace_and_delete_on_the_movement_page(self):
        page = self.text(f"/movements/{self.movement}")
        self.assertIn("Synthetic master", page)
        self.assertIn("Add a photo", page)
        self.assertNotIn("Delete photo", page)

        added = self.upload(
            f"/movements/{self.movement}/photo", PHOTO, form=f"/movements/{self.movement}"
        )
        self.assertEqual(added.status_code, 303)
        self.assertEqual(added.headers["Location"], f"/movements/{self.movement}")
        page = self.text(f"/movements/{self.movement}")
        self.assertIn(f'src="/movements/{self.movement}/photo"', page)
        self.assertIn("Replace photo", page)
        self.assertIn("Delete photo", page)

        replaced = self.upload(
            f"/movements/{self.movement}/photo",
            JPEG,
            name="any-name.bin",
            form=f"/movements/{self.movement}",
        )
        self.assertEqual(replaced.status_code, 303)
        self.assertEqual(self.stored(self.movement)["media_type"], "image/jpeg")

        with patch.dict(os.environ, {"T6_DRIVE_CUSTODIAN_PERSON_ID": self.api.custodian}):
            deleted = self.submit(
                f"/movements/{self.movement}/photo/delete", form=f"/movements/{self.movement}"
            )
        self.assertEqual(deleted.status_code, 303)
        self.assertFalse(self.has_photo(self.movement))
        self.assertIn("Add a photo", self.text(f"/movements/{self.movement}"))

    def test_the_photo_is_served_from_the_api_and_never_cached(self):
        self.upload(f"/movements/{self.movement}/photo", PHOTO, form=f"/movements/{self.movement}")
        response = self.web.get(f"/movements/{self.movement}/photo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, PHOTO)
        self.assertEqual(response.mimetype, "image/png")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(
            self.web.get(f"/movements/{self.movements()[0]['id']}x/photo").status_code, 404
        )

    def test_a_movement_without_a_photo_has_no_photo_to_serve(self):
        self.assertEqual(self.web.get(f"/movements/{self.movement}/photo").status_code, 404)
        self.assertEqual(self.web.get("/movements/t6m_unknown").status_code, 404)

    def test_the_log_shows_a_thumbnail_and_links_to_the_movement(self):
        self.upload(f"/movements/{self.movement}/photo", PHOTO, form=f"/movements/{self.movement}")
        html = self.text(f"/movements?drive={self.drive}")
        self.assertIn(f'src="/movements/{self.movement}/photo"', html)
        self.assertIn('loading="lazy"', html)
        self.assertIn(f'href="/movements/{self.movement}"', html)

    def test_delete_refused_by_the_api_is_said_in_plain_words(self):
        self.upload(f"/movements/{self.movement}/photo", PHOTO, form=f"/movements/{self.movement}")
        with patch.dict(os.environ, {"T6_DRIVE_CUSTODIAN_PERSON_ID": "someone-else"}):
            response = self.submit(
                f"/movements/{self.movement}/photo/delete", form=f"/movements/{self.movement}"
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "Only the drive custodian or an operator may delete a photo",
            response.get_data(as_text=True),
        )
        self.assertTrue(self.has_photo(self.movement))


class RefusedFileTests(PhotoCase):
    def test_another_type_is_refused_in_plain_words(self):
        for data, name in (
            (b"plain text", "photo.png"),
            (b"GIF89a....", "photo.gif"),
            (b"", "empty.png"),
        ):
            response = self.upload(
                f"/movements/{self.movement}/photo",
                data,
                name=name,
                form=f"/movements/{self.movement}",
            )
            self.assertEqual(response.status_code, 400, name)
            self.assertIn(
                "A photo must be a JPEG, PNG or WebP image.", response.get_data(as_text=True)
            )
        self.assertFalse(self.has_photo(self.movement))

    def test_a_file_over_ten_megabytes_is_refused_in_plain_words(self):
        too_big = PHOTO[:8] + b"\0" * (TEN_MB + 1 - 8)
        response = self.upload(
            f"/movements/{self.movement}/photo", too_big, form=f"/movements/{self.movement}"
        )
        self.assertIn(response.status_code, (400, 413))
        self.assertIn("A photo may be at most 10 MB.", response.get_data(as_text=True))
        self.assertFalse(self.has_photo(self.movement))

    def test_a_photo_of_almost_ten_megabytes_reaches_the_api(self):
        large = PHOTO[:8] + b"\0" * (TEN_MB - 8)
        response = self.upload(
            f"/movements/{self.movement}/photo", large, form=f"/movements/{self.movement}"
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.stored(self.movement)["size"], TEN_MB)

    def test_no_photo_chosen_is_said_plainly(self):
        hidden = self.fields(f"/movements/{self.movement}")
        response = self.web.post(f"/movements/{self.movement}/photo", data=hidden)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a photo first.", response.get_data(as_text=True))

    def test_an_upload_while_the_api_is_down_is_not_queued(self):
        hidden = self.fields(f"/movements/{self.movement}")
        self.api.mode = "down"
        response = self.web.post(
            f"/movements/{self.movement}/photo",
            data={**hidden, "photo": (io.BytesIO(PHOTO), "photo.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 503)
        self.api.mode = "up"
        self.assertEqual(Outbox(self.outbox).entries(), [])
        self.assertFalse(self.has_photo(self.movement))

    def test_other_forms_keep_their_small_body_limit(self):
        hidden = self.fields("/new")
        response = self.web.post("/new", data={**hidden, "label": "x" * 70_000})
        self.assertEqual(response.status_code, 413)
