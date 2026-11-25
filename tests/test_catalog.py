"""The QR label and scan lookup, and the dated catalog of a Drive's contents, against the test API."""

import io
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
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
    def test_the_forms_have_no_qr_field_and_an_edit_keeps_an_existing_code(self):
        blank = self.submit("/new", form="/new", serial="X1")
        self.assertIn('name="label"', blank.get_data(as_text=True))
        self.assertNotIn('name="qr_code"', blank.get_data(as_text=True))
        response = self.submit(
            "/new", label="Shelf drive", office_location="Office A / Shelf 1", qr_code="ignored"
        )
        self.assertEqual(response.status_code, 303)
        drive = self.only_drive()
        self.assertEqual((drive["office_location"], drive["qr_code"]), ("Office A / Shelf 1", None))
        self.assertIn("Office A / Shelf 1", self.text(response.headers["Location"]))
        form = self.text(f"/drive/{drive['id']}/edit")
        self.assertIn('name="office_location" value="Office A / Shelf 1"', form)
        self.assertNotIn('name="qr_code"', form)
        coded = self.api.add_drive(label="Coded", qr_code="OLD-1")
        self.submit(f"/drive/{coded}/edit", label="Coded again")
        kept = self.api.drive(coded)["drive"]
        self.assertEqual((kept["label"], kept["qr_code"]), ("Coded again", "OLD-1"))
        self.assertIn("OLD-1", self.text(f"/drive/{coded}"))

    def test_add_qr_gives_a_drive_its_id_as_code_and_shows_the_label(self):
        drive = self.api.add_drive(label="Client drive", ownership_known=False)
        self.assertIn("Add QR", self.text(f"/drive/{drive}"))
        page = self.text(f"/drive/{drive}/qr")
        self.assertIn("Add QR", page)
        self.assertNotIn("<svg", page)
        response = self.submit(f"/drive/{drive}/qr", form=f"/drive/{drive}/qr")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], f"/drive/{drive}/qr")
        code = self.api.drive(drive)["drive"]["qr_code"]
        self.assertEqual(code.lower(), drive)
        label = self.text(f"/drive/{drive}/qr")
        self.assertIn("<svg", label)
        self.assertNotIn("Add QR", label)
        self.assertNotIn("Add QR", self.text(f"/drive/{drive}"))
        scanned = self.web.get("/qr/" + code)
        self.assertEqual(
            (scanned.status_code, scanned.headers["Location"]), (303, f"/drive/{drive}")
        )

    def test_a_t6_drive_shows_its_label_without_adding_a_code(self):
        drive = self.api.add_drive(label="Ours")
        page = self.text(f"/drive/{drive}/qr")
        self.assertIn("<svg", page)
        self.assertNotIn("Add QR", page)
        self.assertNotIn("Add QR", self.text(f"/drive/{drive}"))
        self.assertEqual(self.web.get(f"/qr/{drive}").status_code, 303)

    def test_add_qr_does_not_replace_an_existing_code(self):
        drive = self.api.add_drive(label="Coded", qr_code="OLD-2")
        self.submit(f"/drive/{drive}/qr", form=f"/drive/{drive}/edit")
        self.assertEqual(self.api.drive(drive)["drive"]["qr_code"], "OLD-2")

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
        bare = self.api.add_drive(label="No code", ownership_known=False)
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

    def test_two_scans_on_one_day_show_with_times_newest_first_and_both_links_work(self):
        self.assertEqual(self.upload(FIRST, on="2026-03-01T09:15:00+08:00").status_code, 303)
        self.assertEqual(self.upload(SECOND, on="2026-03-01T14:30").status_code, 303)
        page = self.text(self.url)
        self.assertEqual(page.count("data-snapshot-id"), 2)
        self.assertIn("2026-03-01 09:15", page)
        self.assertIn("2026-03-01 14:30", page)
        self.assertLess(page.index("2026-03-01 14:30"), page.index("2026-03-01 09:15"))

        snapshots = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=self.drive
        )["snapshots"]
        self.assertEqual(
            [s["captured_on"] for s in snapshots],
            ["2026-03-01T09:15:00+08:00", "2026-03-01T14:30:00+08:00"],
        )
        early_id, late_id = snapshots[0]["id"], snapshots[1]["id"]

        diff_links = dict(
            re.findall(r'data-snapshot-id="([^"]+)".*?href="([^"]+\?diff=[^"]+)"', page, re.DOTALL)
        )
        contents_links = dict(
            re.findall(r'data-snapshot-id="([^"]+)".*?href="([^"]+\?on=[^"]+)"', page, re.DOTALL)
        )
        self.assertEqual(set(diff_links), {early_id, late_id})
        self.assertEqual(set(contents_links), {early_id, late_id})

        early_diff = self.text(diff_links[early_id])
        self.assertIn("notes.txt", early_diff)
        self.assertNotIn("clip3.mov", early_diff)

        late_diff = self.text(diff_links[late_id])
        self.assertIn("clip3.mov", late_diff)
        self.assertIn("change--removed", late_diff)

        early_tree = self.text(contents_links[early_id])
        self.assertIn("notes.txt", early_tree)
        self.assertNotIn("clip3.mov", early_tree)
        self.assertIn("From the scan of 2026-03-01 09:15", early_tree)

        late_tree = self.text(contents_links[late_id])
        self.assertIn("clip3.mov", late_tree)
        self.assertNotIn("notes.txt", late_tree)
        self.assertIn("From the scan of 2026-03-01 14:30", late_tree)

        # Mid-day query before the second scan gets the first scan; bare day gets the end of the day.
        midday = self.text(f"{self.url}?on=2026-03-01T12%3A00%3A00%2B08%3A00")
        self.assertIn("notes.txt", midday)
        self.assertNotIn("clip3.mov", midday)
        eod = self.text(f"{self.url}?on=2026-03-01")
        self.assertIn("clip3.mov", eod)
        self.assertNotIn("notes.txt", eod)

    def test_upload_form_defaults_to_now_in_manila_and_handles_offsets_and_bad_times(self):
        manila = timezone(timedelta(hours=8))
        before = datetime.now(manila)
        html = self.text(self.url)
        after = datetime.now(manila)
        match = re.search(
            r'<input[^>]*name="captured_on"[^>]*type="datetime-local"[^>]*value="([^"]*)"', html
        )
        if match is None:
            match = re.search(
                r'<input[^>]*type="datetime-local"[^>]*name="captured_on"[^>]*value="([^"]*)"',
                html,
            )
        self.assertIsNotNone(match, html)
        assert match is not None
        default_val = match.group(1)
        self.assertRegex(default_val, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
        allowed = {
            before.strftime("%Y-%m-%dT%H:%M"),
            after.strftime("%Y-%m-%dT%H:%M"),
        }
        self.assertIn(default_val, allowed)

        # UTC offset timestamp is normalized to Manila; naive datetime-local gets +08:00.
        self.assertEqual(self.upload(FIRST, on="2026-03-01T01:30:00+00:00").status_code, 303)
        self.assertEqual(self.upload(SECOND, on="2026-03-01T14:45").status_code, 303)
        page = self.text(self.url)
        self.assertIn("2026-03-01 09:30", page)
        self.assertIn("2026-03-01 14:45", page)

        # Refuses scan at or before latest instant on the same day.
        for earlier in ("2026-03-01T14:45", "2026-03-01T14:00:00+08:00"):
            with self.subTest(earlier=earlier):
                refused = self.upload(FIRST, on=earlier)
                self.assertEqual(refused.status_code, 400)
                self.assertIn("already scanned", refused.get_data(as_text=True))

        # Invalid upload timestamp or missing offset on ?on= returns 400 cleanly.
        self.assertEqual(self.upload(FIRST, on="not-a-date").status_code, 400)
        self.assertEqual(self.web.get(f"{self.url}?on=2026-03-01T14:30").status_code, 400)


if __name__ == "__main__":
    unittest.main()
