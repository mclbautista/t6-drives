"""Helper endpoint (`POST /helper/catalog`) against the real t6 VerbAPI at Seam 2."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from support import SECRET, DriveAppCase
from t6.verbs import DriveCatalogSnapshotsInput

from app import create_app
from runtime import application

TOKEN = "synthetic-helper-token-0123456789abcdef"
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


class HelperEndpointCase(DriveAppCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(
            self.api.client(),
            self.outbox,
            retry_interval=0,
            helper_token=TOKEN,
        )
        self.web = self.app.test_client()

    def post_helper(
        self,
        payload: Any = None,
        *,
        token: str | None = TOKEN,
        auth_scheme: str = "x-helper-token",
        headers: dict[str, str] | None = None,
        raw: bytes | None = None,
        base_url: str = "http://localhost",
    ):
        req_headers = dict(headers or {})
        if token is not None:
            if auth_scheme == "bearer":
                req_headers["Authorization"] = f"Bearer {token}"
            else:
                req_headers["X-Helper-Token"] = token
        if raw is not None:
            return self.web.post(
                "/helper/catalog",
                data=raw,
                content_type="application/json",
                headers=req_headers,
                base_url=base_url,
            )
        return self.web.post(
            "/helper/catalog",
            json=payload,
            headers=req_headers,
            base_url=base_url,
        )

    def scan_payload(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "serial": "",
            "volume_name": "",
            "label": "",
            "captured_on": "2026-03-01T14:30:00+08:00",
            "entries": FIRST,
            "mode": "preview",
        }
        payload.update(overrides)
        return payload


class HelperAuthAndGuardTests(HelperEndpointCase):
    def test_empty_helper_token_answers_404_like_an_unknown_route(self):
        disabled = create_app(self.api.client(), self.outbox, retry_interval=0).test_client()
        for method in (disabled.post, disabled.get):
            res = method("/helper/catalog", headers={"X-Helper-Token": TOKEN})
            self.assertEqual(res.status_code, 404)
            self.assertNotIn("unauthorized", res.get_data(as_text=True).lower())
        self.assertEqual(
            disabled.post(
                "/helper/catalog",
                json=self.scan_payload(),
                headers={"Host": "evil.example"},
            ).status_code,
            400,
        )

    def test_missing_or_wrong_token_is_refused_with_401_and_never_leaked(self):
        for headers in (
            {},
            {"X-Helper-Token": "wrong-token"},
            {"X-Helper-Token": ""},
            {"Authorization": "Bearer wrong-token"},
            {"Authorization": f"Basic {TOKEN}"},
        ):
            with self.subTest(headers=headers):
                res = self.post_helper(self.scan_payload(), token=None, headers=headers)
                self.assertEqual(res.status_code, 401)
                body = res.get_data(as_text=True)
                self.assertEqual(res.get_json()["status"], "unauthorized")
                self.assertNotIn(TOKEN, body)

    def test_both_x_helper_token_and_bearer_header_authenticate(self):
        drive = self.api.add_drive(label="Shuttle 1", serial="SN-AUTH-1")
        for scheme in ("x-helper-token", "bearer"):
            with self.subTest(scheme=scheme):
                res = self.post_helper(
                    self.scan_payload(serial="SN-AUTH-1", mode="preview"),
                    auth_scheme=scheme,
                )
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.get_json()["drive"]["id"], drive)

    def test_host_check_applies_and_no_csrf_origin_or_session_is_required(self):
        drive = self.api.add_drive(label="Office Drive", serial="SN-HOST-1")
        self.assertEqual(
            self.post_helper(
                self.scan_payload(serial="SN-HOST-1"),
                headers={"Host": "evil.example"},
            ).status_code,
            400,
        )
        office_app = create_app(
            self.api.client(),
            self.outbox,
            retry_interval=0,
            allowed_host="192.0.2.10",
            helper_token=TOKEN,
        ).test_client()
        ok = office_app.post(
            "/helper/catalog",
            json=self.scan_payload(serial="SN-HOST-1"),
            headers={"X-Helper-Token": TOKEN},
            base_url="http://192.0.2.10:5000",
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(
            ok.get_json()["catalog_url"],
            f"http://192.0.2.10:5000/drive/{drive}/catalog",
        )

    def test_body_limit_is_the_manifest_limit_while_forms_stay_at_64kb(self):
        self.api.add_drive(label="Big Drive", serial="SN-BIG-1")
        big_entries = [
            {
                "path": f"Show A/Day {n // 500:02d}/clip{n:05d}.mov",
                "kind": "file",
                "size_bytes": n,
            }
            for n in range(8000)
        ]
        big_raw = json.dumps(self.scan_payload(serial="SN-BIG-1", entries=big_entries)).encode()
        self.assertGreater(len(big_raw), 400_000)
        ok = self.post_helper(raw=big_raw)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["entry_count"], 8000)

        too_big = b"{" + b" " * 12_500_000 + b"}"
        self.assertEqual(self.post_helper(raw=too_big).status_code, 413)

        elsewhere = self.web.post("/new", data={**self.fields("/new"), "remarks": "x" * 70_000})
        self.assertEqual(elsewhere.status_code, 413)


class HelperValidationTests(HelperEndpointCase):
    def test_invalid_payloads_return_400_json_invalid(self):
        bad_raws = [b"not-json", b"[]", b'"string"']
        for raw in bad_raws:
            with self.subTest(raw=raw):
                res = self.post_helper(raw=raw)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.get_json()["status"], "invalid")

        bad_payloads: list[dict[str, Any]] = [
            self.scan_payload(mode="upload"),
            {k: v for k, v in self.scan_payload().items() if k != "mode"},
            {k: v for k, v in self.scan_payload().items() if k != "captured_on"},
            self.scan_payload(captured_on="2026-03-01"),
            self.scan_payload(captured_on="2026-03-01T14:30:00"),
            self.scan_payload(captured_on="not-a-timestamp"),
            {k: v for k, v in self.scan_payload().items() if k != "entries"},
            self.scan_payload(entries="not-a-list"),
            self.scan_payload(entries=[{"path": "../secret", "kind": "file", "size_bytes": 1}]),
            self.scan_payload(entries=[{"path": "a.mov", "kind": "file"}]),
            self.scan_payload(entries=[{"path": "a.mov", "kind": "link"}]),
            self.scan_payload(
                entries=[
                    {"path": "dup.mov", "kind": "file", "size_bytes": 1},
                    {"path": "dup.mov", "kind": "file", "size_bytes": 2},
                ]
            ),
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                res = self.post_helper(payload)
                self.assertEqual(res.status_code, 400)
                body = res.get_json()
                self.assertEqual(body["status"], "invalid")
                self.assertTrue(body["reason"])

    def test_size_alias_and_default_root_path(self):
        drive = self.api.add_drive(label="Alias Drive", serial="SN-ALIAS")
        entries = [
            {"path": "Reel", "kind": "dir"},
            {"path": "Reel/a.mov", "kind": "file", "size": 42, "mtime": "2026-03-01T12:00:00Z"},
        ]
        res = self.post_helper(
            self.scan_payload(
                serial="SN-ALIAS",
                volume_name="REEL_VOL",
                entries=entries,
                mode="save",
            )
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["status"], "saved")
        self.assertEqual(body["total_bytes"], 42)
        snaps = self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive)[
            "snapshots"
        ]
        self.assertEqual(snaps[0]["root_path"], "REEL_VOL")

        # Without volume_name or root_path, root_path defaults to "/".
        res2 = self.post_helper(
            self.scan_payload(
                serial="SN-ALIAS",
                captured_on="2026-03-01T15:00:00+08:00",
                entries=entries,
                mode="save",
            )
        )
        self.assertEqual(res2.status_code, 200)
        snaps2 = self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive)[
            "snapshots"
        ]
        self.assertEqual(snaps2[1]["root_path"], "/")


class HelperMatchingTests(HelperEndpointCase):
    def test_matches_by_accepted_or_unique_suspected_serial(self):
        accepted = self.api.add_drive(label="Accepted One", serial="SN-ACC-1")
        suspected = self.api.add_drive(label="Suspected One", suspected_serial="SN-SUSP-1")

        res1 = self.post_helper(self.scan_payload(serial="sn-acc-1"))
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.get_json()["drive"], {"id": accepted, "label": "Accepted One"})

        res2 = self.post_helper(self.scan_payload(serial="SN-SUSP-1"))
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.get_json()["drive"], {"id": suspected, "label": "Suspected One"})

        # Two drives sharing a suspected serial are ambiguous for serial matching.
        self.api.add_drive(label="Dup Susp 1", suspected_serial="SN-DUP-SUSP")
        self.api.add_drive(label="Dup Susp 2", suspected_serial="SN-DUP-SUSP")
        res3 = self.post_helper(self.scan_payload(serial="SN-DUP-SUSP"))
        self.assertEqual(res3.status_code, 422)
        self.assertEqual(res3.get_json()["status"], "no_drive")

    def test_matches_by_volume_name_and_typed_label_on_open_project_use_only(self):
        project = self.api.project("Helper Show")
        d1 = self.api.add_drive(label="Drive Record Label Only")
        # Drive label alone (no open Project use) must not match.
        self.assertEqual(
            self.post_helper(self.scan_payload(volume_name="Drive Record Label Only")).status_code,
            422,
        )

        # Open Project use matches case-insensitively and trimmed.
        self.api.assign(d1, project, "Show_Vol_A", "master")
        res = self.post_helper(self.scan_payload(volume_name="  show_vol_a "))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["drive"]["id"], d1)

        # Closed Project use does not match.
        d_closed = self.api.add_drive(label="Closed Use Drive")
        self.api.assign(
            d_closed, project, "Old_Vol", "backup", start="2026-01-01", end="2026-02-01"
        )
        self.assertEqual(
            self.post_helper(self.scan_payload(volume_name="Old_Vol")).status_code,
            422,
        )

        # Ambiguous volume_name (2 drives with open Project use sharing label) -> no_drive unless typed label resolves it.
        d2 = self.api.add_drive(label="Shared A")
        d3 = self.api.add_drive(label="Shared B")
        self.api.assign(d2, project, "REUSED_NAME", "master")
        self.api.assign(d3, project, "REUSED_NAME", "backup")
        self.api.assign(d3, project, "UNIQUE_TYPED_B", "swing")

        ambiguous = self.post_helper(self.scan_payload(volume_name="REUSED_NAME"))
        self.assertEqual(ambiguous.status_code, 422)
        self.assertEqual(ambiguous.get_json()["status"], "no_drive")

        # Typed label breaks the ambiguity or matches when volume_name doesn't match.
        by_label = self.post_helper(
            self.scan_payload(volume_name="REUSED_NAME", label="unique_typed_b")
        )
        self.assertEqual(by_label.status_code, 200)
        self.assertEqual(by_label.get_json()["drive"]["id"], d3)

        # Serial takes priority over volume_name and label.
        d_ser = self.api.add_drive(label="Priority Serial", serial="SN-PRIO")
        by_serial = self.post_helper(
            self.scan_payload(serial="SN-PRIO", volume_name="Show_Vol_A", label="unique_typed_b")
        )
        self.assertEqual(by_serial.status_code, 200)
        self.assertEqual(by_serial.get_json()["drive"]["id"], d_ser)


class HelperPreviewSaveAndErrorsTests(HelperEndpointCase):
    def test_preview_writes_nothing_and_save_records_scan_with_idempotency_and_order(self):
        drive = self.api.add_drive(label="Timed Helper Drive", serial="SN-SAVE-1")

        # Preview writes nothing.
        prev = self.post_helper(
            self.scan_payload(
                serial="SN-SAVE-1",
                volume_name="Shuttle01",
                captured_on="2026-03-01T09:15:00+08:00",
                entries=FIRST,
                mode="preview",
            )
        )
        self.assertEqual(prev.status_code, 200)
        self.assertEqual(
            prev.get_json(),
            {
                "status": "preview",
                "drive": {"id": drive, "label": "Timed Helper Drive"},
                "added": 4,
                "modified": 0,
                "removed": 0,
                "entry_count": 4,
                "total_bytes": 305,
                "catalog_url": f"http://localhost/drive/{drive}/catalog",
            },
        )
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive)[
                "snapshots"
            ],
            [],
        )

        # Save records the scan with sent time; retrying the same idempotency_key is idempotent.
        for _ in range(2):
            saved = self.post_helper(
                self.scan_payload(
                    serial="SN-SAVE-1",
                    volume_name="Shuttle01",
                    captured_on="2026-03-01T09:15:00+08:00",
                    entries=FIRST,
                    mode="save",
                    idempotency_key="helper-key-1",
                )
            )
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(
                saved.get_json(),
                {
                    "status": "saved",
                    "drive": {"id": drive, "label": "Timed Helper Drive"},
                    "added": 4,
                    "modified": 0,
                    "removed": 0,
                    "entry_count": 4,
                    "total_bytes": 305,
                    "catalog_url": f"http://localhost/drive/{drive}/catalog",
                },
            )
        snaps = self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive)[
            "snapshots"
        ]
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["captured_on"], "2026-03-01T09:15:00+08:00")

        # Second scan later on the same day shows diff counts and is kept alongside the first.
        saved2 = self.post_helper(
            self.scan_payload(
                serial="SN-SAVE-1",
                volume_name="Shuttle01",
                captured_on="2026-03-01T14:30:00+08:00",
                entries=SECOND,
                mode="save",
                idempotency_key="helper-key-2",
            )
        )
        self.assertEqual(saved2.status_code, 200)
        body2 = saved2.get_json()
        self.assertEqual(body2["status"], "saved")
        self.assertEqual(body2["added"], 1)
        self.assertEqual(body2["modified"], 1)
        self.assertEqual(body2["removed"], 1)
        self.assertEqual(body2["entry_count"], 4)
        self.assertEqual(body2["total_bytes"], 650)

        # Older or equal scan gives 409 "never" with rule "catalog_order".
        for earlier in ("2026-03-01T14:30:00+08:00", "2026-03-01T12:00:00+08:00"):
            for mode in ("preview", "save"):
                with self.subTest(earlier=earlier, mode=mode):
                    refused = self.post_helper(
                        self.scan_payload(
                            serial="SN-SAVE-1",
                            captured_on=earlier,
                            entries=FIRST,
                            mode=mode,
                        )
                    )
                    self.assertEqual(refused.status_code, 409)
                    data = refused.get_json()
                    self.assertEqual(data["status"], "never")
                    self.assertEqual(data["rule"], "catalog_order")
                    self.assertTrue(data["reason"])

    def test_api_down_returns_503_try_later_and_never_queues_in_outbox(self):
        project = self.api.project("Outage Show")
        drive = self.api.add_drive(label="Outage Drive", serial="SN-DOWN-1")
        self.api.assign(drive, project, "VOL_DOWN", "master")

        self.api.mode = "down"
        for payload in (
            self.scan_payload(serial="SN-DOWN-1", mode="preview"),
            self.scan_payload(serial="SN-DOWN-1", mode="save", idempotency_key="down-1"),
            self.scan_payload(volume_name="VOL_DOWN", mode="save", idempotency_key="down-2"),
        ):
            with self.subTest(payload=payload):
                res = self.post_helper(payload)
                self.assertEqual(res.status_code, 503)
                self.assertEqual(res.get_json()["status"], "try_later")

        # Outbox stays completely empty.
        self.assertFalse(Path(self.outbox).exists() and json.loads(Path(self.outbox).read_text()))
        self.assertEqual(self.web.get("/outbox").get_data(as_text=True).count("data-key"), 0)

        # Lost confirm response also gives 503 try_later, nothing in Outbox, and retry with same key is idempotent.
        self.api.mode = "lost"
        lost = self.post_helper(
            self.scan_payload(serial="SN-DOWN-1", mode="save", idempotency_key="lost-key-1")
        )
        self.assertEqual(lost.status_code, 503)
        self.assertEqual(lost.get_json()["status"], "try_later")
        self.assertEqual(self.web.get("/outbox").get_data(as_text=True).count("data-key"), 0)

        self.api.mode = "up"
        retry = self.post_helper(
            self.scan_payload(serial="SN-DOWN-1", mode="save", idempotency_key="lost-key-1")
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.get_json()["status"], "saved")
        snaps = self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive)[
            "snapshots"
        ]
        self.assertEqual(len(snaps), 1)


class HelperRuntimeConfigTests(unittest.TestCase):
    def test_runtime_reads_drives_helper_token_and_compose_passes_it(self):
        compose = (Path(__file__).parents[1] / "compose.yaml").read_text()
        self.assertIn("DRIVES_HELPER_TOKEN: ${DRIVES_HELPER_TOKEN:-}", compose)

        base = {
            "DRIVES_SESSION_SECRET": "s" * 32,
            "T6_API_URL": "http://127.0.0.1:9",
            "T6_API_SECRET": SECRET,
            "DRIVES_OUTBOX": "/data/outbox.json",
        }
        with tempfile.TemporaryDirectory():
            with patch.dict(os.environ, base):
                client = application().test_client()
                self.assertEqual(client.post("/helper/catalog", json={}).status_code, 404)
            with patch.dict(os.environ, {**base, "DRIVES_HELPER_TOKEN": TOKEN}):
                client = application().test_client()
                self.assertEqual(client.post("/helper/catalog", json={}).status_code, 401)
                self.assertEqual(
                    client.post(
                        "/helper/catalog",
                        json={},
                        headers={"X-Helper-Token": TOKEN},
                    ).status_code,
                    400,
                )


if __name__ == "__main__":
    unittest.main()
