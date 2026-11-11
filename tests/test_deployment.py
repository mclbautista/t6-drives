"""Public HTTP/startup seam; only disposable synthetic SQLite."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app


class DeploymentTests(unittest.TestCase):
    def test_health_is_empty_and_dynamic_loopback_authority_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(str(Path(directory) / "synthetic.sqlite"))
            client = app.test_client()
            response = client.get("/health", base_url="http://127.0.0.1:43127")
            self.assertEqual(response.status_code, 204)
            self.assertEqual(response.data, b"")
            self.assertEqual(client.get("/", base_url="http://localhost:43127").status_code, 200)
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

    def test_future_schema_fails_before_any_application_write(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "synthetic.sqlite")
            with sqlite3.connect(database) as db:
                db.execute("PRAGMA user_version=999")
            before = Path(database).read_bytes()
            with self.assertRaisesRegex(RuntimeError, "Unsupported database schema"):
                create_app(database)
            self.assertEqual(Path(database).read_bytes(), before)

    def test_initialization_is_repeatable_and_stamps_supported_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "synthetic.sqlite")
            for _ in range(2):
                self.assertEqual(create_app(database).test_client().get("/health").status_code, 204)
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))
