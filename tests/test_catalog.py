"""The QR label and scan lookup, and the dated catalog of a Drive's contents, against the test API."""

import io
import json
import unittest
from typing import Any

from support import DriveAppCase
from t6.verbs import DriveCatalogSnapshotsInput

FIRST: list[dict[str, Any]] = [
    {"path": "Show A", "kind": "dir"},
    {"path": "Show A/clip1.mov", "kind": "file", "size_bytes": 100},
    {"path": "Show A/clip2.mov", "kind": "file", "size_bytes": 200},
    {"path": "notes.txt", "kind": "file", "size_bytes": 5},
]
SECOND: list[dict[str, Any]] = [
    {"path": "Show A", "kind": "dir"},
    {"path": "Show A/clip1.mov", "kind": "file", "size_bytes": 100},
    {"path": "Show A/clip2.mov", "kind": "file", "size_bytes": 250},
    {"path": "Show A/clip3.mov", "kind": "file", "size_bytes": 300},
]


def manifest(entries: Any, name: str = "scan.json"):
    return io.BytesIO(json.dumps(entries).encode()), name


class ShelfFactTests(DriveAppCase):
    def test_office_location_and_qr_code_are_added_edited_cleared_and_shown(self):
        response = self.submit(
            "/new", label="Shelf drive", office_location="Office A / Shelf 1", qr_code=" ab-12 "
        )
        self.assertEqual(response.status_code, 303)
        drive = self.only_drive()
        self.assertEqual(
            (drive["office_location"], drive["qr_code"]), ("Office A / Shelf 1", "AB-12")
        )
        detail = self.text(response.headers["Location"])
        self.assertIn("Office A / Shelf 1", detail)
        self.assertIn("AB-12", detail)
        form = self.text(f"/drive/{drive['id']}/edit")
        self.assertIn('name="office_location" value="Office A / Shelf 1"', form)
        self.submit(
            f"/drive/{drive['id']}/edit", label="Shelf drive", office_location="Office A / Shelf 2"
        )
        changed = self.only_drive()
        self.assertEqual(
            (changed["office_location"], changed["qr_code"]), ("Office A / Shelf 2", None)
        )

    def test_a_second_drive_cannot_take_a_qr_code_and_an_unsafe_one_is_refused(self):
        self.api.add_drive(label="First", qr_code="QR-1")
        taken = self.submit("/new", label="Second", qr_code="qr-1")
        self.assertEqual(taken.status_code, 400)
        self.assertIn("already on a Drive", taken.get_data(as_text=True))
        unsafe = self.submit("/new", label="Third", qr_code="has space")
        self.assertEqual(unsafe.status_code, 400)
        self.assertEqual(len(self.api.drives()), 1)

    def test_scanning_a_label_opens_its_drive_by_code_in_any_case_or_by_id(self):
        drive = self.api.add_drive(label="Scanned", qr_code="QR-7")
        for code in ("QR-7", "qr-7", drive):
            with self.subTest(code=code):
                response = self.web.get("/qr/" + code)
                self.assertEqual(
                    (response.status_code, response.headers["Location"]), (303, "/drive/" + drive)
                )
        self.assertEqual(self.web.get("/qr/NOPE-1").status_code, 404)
        self.assertEqual(self.web.get("/qr/t6d_" + "0" * 32).status_code, 404)
        self.assertEqual(self.web.get("/qr/bad%20code").status_code, 404)

    def test_the_qr_label_shows_a_picture_and_the_facts_to_print(self):
        drive = self.api.add_drive(
            label="Labelled", serial="LBL-1", qr_code="QR-9", office_location="Office A / Shelf 1"
        )
        page = self.text(f"/drive/{drive}/qr")
        for shown in ("<svg", "QR-9", "Labelled", "LBL-1", "Office A / Shelf 1"):
            self.assertIn(shown, page)
        bare = self.api.add_drive(label="No code")
        self.assertIn("no QR code yet", self.text(f"/drive/{bare}/qr"))
        self.assertEqual(self.web.get("/drive/t6d_" + "0" * 32 + "/qr").status_code, 404)

    def test_the_inventory_finds_a_drive_by_office_location_and_by_qr_code(self):
        a = self.api.add_drive(label="A", office_location="Office A / Shelf 1", qr_code="QR-1")
        b = self.api.add_drive(label="B", office_location="Office B / Shelf 9")
        self.assertEqual(self.shown("?office_location=office+a"), [a])
        self.assertEqual(self.shown("?q=qr-1"), [a])
        self.assertIn("Office B / Shelf 9", self.text("/"))
        self.assertEqual(sorted(self.shown("")), sorted([a, b]))

    def shown(self, query: str) -> list[str]:
        html = self.text("/" + query)
        return [d["id"] for d in self.api.drives() if f'href="/drive/{d["id"]}"' in html]


class CatalogTests(DriveAppCase):
    def setUp(self):
        super().setUp()
        self.drive = self.api.add_drive(label="Catalogued")
        self.url = f"/drive/{self.drive}/catalog"

    def upload(self, entries, on="2026-03-01", **more):
        return self.submit(
            self.url, manifest=manifest(entries), captured_on=on, root_path="Volume", **more
        )

    def test_a_scan_is_uploaded_listed_and_its_changes_are_shown(self):
        self.assertIn("No scans yet", self.text(self.url))
        first = self.upload(FIRST)
        self.assertEqual((first.status_code, first.headers["Location"]), (303, self.url))
        self.upload(SECOND, on="2026-03-10")
        page = self.text(self.url)
        self.assertEqual(page.count("data-snapshot-id"), 2)
        self.assertLess(page.index("2026-03-10"), page.index("2026-03-01"))  # newest first
        first_id, second_id = (
            s["id"]
            for s in self.api.read(
                "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=self.drive
            )["snapshots"]
        )
        changed = self.text(f"{self.url}?diff={second_id}")
        for shown in (
            "change--modified",
            "change--added",
            "change--removed",
            "clip3.mov",
            "notes.txt",
        ):
            self.assertIn(shown, changed)
        self.assertNotIn("clip1.mov", changed)  # unchanged: not stored, not shown
        self.assertEqual(self.web.get(f"{self.url}?diff=snap_nothere").status_code, 404)
        self.assertIn("clip2.mov", self.text(f"{self.url}?diff={first_id}"))

    def test_the_contents_on_a_date_are_the_latest_scan_on_or_before_it(self):
        self.upload(FIRST)
        self.upload(SECOND, on="2026-03-10")
        early = self.text(f"{self.url}?on=2026-03-05")
        self.assertIn("notes.txt", early)
        self.assertNotIn("clip3.mov", early)
        self.assertIn("From the scan of 2026-03-01", early)
        late = self.text(f"{self.url}?on=2026-04-01")
        self.assertIn("clip3.mov", late)
        self.assertNotIn("notes.txt", late)
        folder = self.text(f"{self.url}?on=2026-04-01&prefix=Show+A")
        self.assertIn("clip3.mov", folder)
        before = self.text(f"{self.url}?on=2026-02-01")
        self.assertIn("had not been scanned by 2026-02-01", before)
        bad = self.web.get(f"{self.url}?on=soon")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("YYYY-MM-DD", bad.get_data(as_text=True))

    def test_a_scan_that_is_refused_says_why_and_changes_nothing(self):
        self.upload(FIRST, on="2026-03-10")
        for entries, on, shown in (
            (FIRST, "2026-03-10", "already scanned"),
            (FIRST, "2026-03-01", "already scanned"),
            ([{"path": "../x", "kind": "file", "size_bytes": 1}], "2026-04-01", "path"),
            ([{"path": "a", "kind": "file"}], "2026-04-01", "size"),
        ):
            with self.subTest(entries=entries, on=on):
                response = self.upload(entries, on=on)
                self.assertEqual(response.status_code, 400)
                self.assertIn(shown, response.get_data(as_text=True))
        self.assertEqual(self.text(self.url).count("data-snapshot-id"), 1)

    def test_a_manifest_that_is_not_one_is_refused(self):
        for upload, shown in (
            (None, "Choose the manifest"),
            (manifest("not a list"), "list of entries"),
            ((io.BytesIO(b"{broken"), "scan.json"), "not valid JSON"),
        ):
            with self.subTest(shown=shown):
                data: dict[str, Any] = {"captured_on": "2026-03-01", "root_path": "Volume"}
                if upload is not None:
                    data["manifest"] = upload
                response = self.submit(self.url, **data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(shown, response.get_data(as_text=True))

    def test_an_object_with_an_entries_list_is_a_manifest_too(self):
        self.submit(
            self.url, manifest=manifest({"entries": FIRST}), captured_on="2026-03-01", root_path="/"
        )
        self.assertIn("4 entries", self.text(self.url))

    def test_a_scan_the_api_cannot_take_is_not_queued_and_a_second_try_lands_once(self):
        fields = self.fields(self.url)
        self.api.mode = "down"
        down = self.web.post(
            self.url, data={**fields, "manifest": manifest(FIRST), "captured_on": "2026-03-01"}
        )
        self.assertEqual(down.status_code, 503)
        self.assertEqual(self.web.get("/outbox").get_data(as_text=True).count("data-key"), 0)
        self.api.mode = "up"
        for _ in range(2):  # the same form sent twice is one write
            again = self.web.post(
                self.url,
                data={**fields, "manifest": manifest(FIRST), "captured_on": "2026-03-01"},
            )
            self.assertEqual(again.status_code, 303)
        self.assertEqual(self.text(self.url).count("data-snapshot-id"), 1)

    def test_only_the_scan_upload_takes_a_large_body_and_every_check_stays(self):
        big = [
            {
                "path": f"Show A/Day {n // 500:02d}/clip{n:05d}.mov",
                "kind": "file",
                "size_bytes": n,
                "checksum": f"{n:032x}",
            }
            for n in range(8000)
        ]
        self.assertGreater(len(json.dumps(big)), 700_000)
        self.assertEqual(self.upload(big).status_code, 303)
        self.assertIn("8000 entries", self.text(self.url))
        elsewhere = self.web.post("/new", data={**self.fields("/new"), "remarks": "x" * 70_000})
        self.assertEqual(elsewhere.status_code, 413)
        too_big = self.web.post(
            self.url,
            data={**self.fields(self.url), "manifest": (io.BytesIO(b" " * 13_000_000), "big.json")},
        )
        self.assertEqual(too_big.status_code, 413)
        forged = self.web.post(
            self.url, data={"manifest": manifest(FIRST), "captured_on": "2026-04-01"}
        )
        self.assertEqual(forged.status_code, 403)  # no CSRF token
        foreign = self.web.post(
            self.url,
            data={
                **self.fields(self.url),
                "manifest": manifest(FIRST),
                "captured_on": "2026-04-01",
            },
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(foreign.status_code, 403)
        self.assertEqual(self.web.get(self.url, headers={"Host": "evil.example"}).status_code, 400)

    def test_an_unknown_drive_has_no_catalog(self):
        self.assertEqual(self.web.get("/drive/t6d_" + "0" * 32 + "/catalog").status_code, 404)


if __name__ == "__main__":
    unittest.main()
