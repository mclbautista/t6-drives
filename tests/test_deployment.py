"""Public HTTP and startup seam: the app owns no database and holds the API secret only at runtime."""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SECRET, DriveAppCase
from t6.client import Client

from app import create_app
from runtime import application

SESSION = "s" * 32
OFFICE = "192.0.2.10"


class DeploymentTests(unittest.TestCase):
    def test_health_is_empty_without_calling_the_api_and_loopback_authority_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Client("http://127.0.0.1:9", SECRET, timeout=1), directory + "/o.json")
            client = app.test_client()
            response = client.get("/health", base_url="http://127.0.0.1:43127")
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.data, b"")
            for host in ("evil.example", "127.0.0.1.evil.example", "192.0.2.1"):
                self.assertEqual(client.get("/health", base_url="http://" + host).status_code, 400)
            self.assertEqual(
                client.post(
                    "/new",
                    base_url="http://localhost:43127",
                    headers={"Origin": "http://localhost:43128"},
                ).status_code,
                403,
            )

    def test_one_configured_office_host_is_accepted_beside_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                Client("http://127.0.0.1:9", SECRET, timeout=1),
                directory + "/o.json",
                allowed_host=OFFICE,
            )
            client = app.test_client()
            self.assertEqual(
                client.get("/health", base_url=f"http://{OFFICE}:5000").status_code, 204
            )
            self.assertEqual(
                client.get("/health", base_url="http://127.0.0.1:5000").status_code, 204
            )
            for host in ("evil.example", OFFICE + ".evil.example", "192.0.2.11"):
                self.assertEqual(client.get("/health", base_url="http://" + host).status_code, 400)
            self.assertEqual(
                client.get(
                    "/health",
                    base_url="http://127.0.0.1:5000",
                    headers={"X-Forwarded-Host": OFFICE, "Forwarded": f"host={OFFICE}"},
                ).status_code,
                204,
            )
            self.assertEqual(
                client.get(
                    "/health",
                    base_url="http://evil.example",
                    headers={"X-Forwarded-Host": OFFICE, "Forwarded": f"host={OFFICE}"},
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/new",
                    base_url=f"http://{OFFICE}:5000",
                    headers={"Origin": f"http://{OFFICE}:5001"},
                ).status_code,
                403,
            )

    def test_without_the_setting_an_office_authority_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Client("http://127.0.0.1:9", SECRET, timeout=1), directory + "/o.json")
            response = app.test_client().get("/health", base_url=f"http://{OFFICE}:5000")
            self.assertEqual(response.status_code, 400)

    def test_a_malformed_allowed_host_stops_start_up_and_names_the_setting(self):
        for value in ("0.0.0.0", "a,b", "*.example", "http://x", "x:5000", "a b", "a/b", "[::1]"):
            with (
                tempfile.TemporaryDirectory() as directory,
                self.assertRaisesRegex(RuntimeError, "DRIVES_ALLOWED_HOST"),
            ):
                create_app(
                    Client("http://127.0.0.1:9", SECRET, timeout=1),
                    directory + "/o.json",
                    allowed_host=value,
                )
        base = {
            "DRIVES_SESSION_SECRET": SESSION,
            "T6_API_URL": "http://127.0.0.1:9",
            "T6_API_SECRET": SECRET,
        }
        with (
            patch.dict(os.environ, {**base, "DRIVES_ALLOWED_HOST": "x:5000"}),
            self.assertRaisesRegex(RuntimeError, "DRIVES_ALLOWED_HOST"),
        ):
            application()
        with patch.dict(os.environ, {**base, "DRIVES_ALLOWED_HOST": OFFICE}):
            office = application().test_client()
            self.assertEqual(office.get("/health", base_url=f"http://{OFFICE}").status_code, 204)

    def test_compose_publishes_on_a_configurable_address_defaulting_to_loopback(self):
        text = (Path(__file__).parents[1] / "compose.yaml").read_text()
        self.assertIn('"${DRIVES_BIND:-127.0.0.1}:${DRIVES_PORT:-5000}:8080"', text)
        self.assertIn("DRIVES_ALLOWED_HOST: ${DRIVES_ALLOWED_HOST:-}", text)
        self.assertNotIn("0.0.0.0", text.replace("never 0.0.0.0", ""))

    def test_runtime_needs_the_api_address_and_secret_and_a_session_secret(self):
        base = {
            "DRIVES_SESSION_SECRET": SESSION,
            "T6_API_URL": "http://127.0.0.1:9",
            "T6_API_SECRET": SECRET,
            "DRIVES_OUTBOX": "/data/outbox.json",
        }
        with patch.dict(os.environ, base):
            app = application()
            self.assertEqual(app.secret_key, SESSION)
            self.assertEqual(app.test_client().get("/health").status_code, 204)
        for name in ("DRIVES_SESSION_SECRET", "T6_API_URL", "T6_API_SECRET"):
            with patch.dict(os.environ, base):
                del os.environ[name]
                with self.assertRaises(RuntimeError) as raised:
                    application()
            self.assertNotIn(SECRET, str(raised.exception))
        with (
            patch.dict(os.environ, {**base, "DRIVES_OUTBOX": "/tmp/outbox.json"}),
            self.assertRaisesRegex(RuntimeError, "/data"),
        ):
            application()
        with (
            patch.dict(os.environ, {**base, "DRIVES_SESSION_SECRET": "short"}),
            self.assertRaisesRegex(RuntimeError, "session secret"),
        ):
            application()


class OfficeHostWriteTests(DriveAppCase):
    def test_a_write_from_the_office_authority_with_a_matching_origin_is_accepted(self):
        self.app = create_app(self.api.client(), self.outbox, retry_interval=0, allowed_host=OFFICE)
        self.web = self.app.test_client()
        base = f"http://{OFFICE}:5000"
        page = self.web.get("/new", base_url=base)
        self.assertEqual(page.status_code, 200)
        form = dict(
            re.findall(
                r'<input type="hidden" name="(csrf|key)" value="([^"]*)"',
                page.get_data(as_text=True),
            )
        )
        response = self.web.post(
            "/new", base_url=base, headers={"Origin": base}, data={**form, "label": "Synthetic"}
        )
        self.assertEqual(response.status_code, 303)
        response = self.web.post(
            "/new", base_url=base, headers={"Origin": "http://evil.example"}, data=form
        )
        self.assertEqual(response.status_code, 403)


class NoLocalDatabaseTests(DriveAppCase):
    def test_a_session_of_reads_and_writes_leaves_only_the_outbox_file(self):
        project = self.api.project("Synthetic show")
        drive = self.api.add_drive(label="Synthetic one")
        self.assertEqual(self.text("/new").count("<form"), 1)
        self.assertEqual(self.submit("/new", label="Synthetic two").status_code, 303)
        self.assertEqual(
            self.submit(
                f"/drive/{drive}/custody", kind="received", project=project, on="2026-11-01"
            ).status_code,
            303,
        )
        self.api.mode = "down"
        self.assertEqual(self.web.get("/").status_code, 503)
        self.assertEqual(self.submit("/new", label="Queued").status_code, 202)
        names = sorted(path.name for path in Path(self.directory).iterdir() if path.is_file())
        self.assertEqual(
            [n for n in names if n.endswith((".sqlite", ".db", "-wal", "-journal"))], []
        )
        self.assertEqual(Path(self.outbox).parent, Path(self.directory))
        self.assertTrue(Path(self.outbox).exists())

    def test_the_api_secret_and_address_never_appear_in_an_error_page(self):
        self.api.mode = "down"
        for url in ("/", "/shipments", "/exceptions", "/shipments/new"):
            body = self.web.get(url)
            self.assertEqual(body.status_code, 503)
            self.assertIn("unavailable", body.get_data(as_text=True).lower())
            self.assertNotIn(SECRET, body.get_data(as_text=True))
            self.assertNotIn(str(self.api.port), body.get_data(as_text=True))
            self.assertNotIn("Traceback", body.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
