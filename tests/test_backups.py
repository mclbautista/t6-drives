"""Synthetic recovery at host-admin commands and observable HTTP seams."""

import json
import os
import re
import sqlite3
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet, InvalidToken
from openpyxl import Workbook

from app import create_app
from assignments import Assignments
from backups import BackupConfig, backup, restore
from imports import InventoryImports


class Reader:
    def read(self):
        return {
            "complete": True,
            "projects": [
                {
                    "t6_id": "t6p_" + "1" * 32,
                    "name": "Synthetic cached project",
                    "active_page_id": "2" * 32,
                    "legacy_page_ids": [],
                }
            ],
        }


def seed(root: Path):
    database = root / "source.sqlite"
    app = create_app(str(database))
    Assignments(str(database)).refresh(Reader())
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Synthetic batch"
    sheet.append(["DATE: Jan 2, 2025"])
    sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "SPACE", "T6 IN"])
    sheet.append(["Synthetic imported", "SYN-RESTORE", "4TB", "YES"])
    month = workbook.create_sheet("Synthetic month")
    month.append(["HARD DRIVE LABEL", "DATE", "IN/OUT", "RECEIVED BY", "REMARKS"])
    month.append(
        ["Synthetic imported", "Jan 3, 2025", "OUT", "Synthetic recipient", "S/N: SYN-RESTORE"]
    )
    month.append(["Synthetic exception", None, "OUT", None, "unattached synthetic evidence"])
    source = root / "synthetic.xlsx"
    workbook.save(source)
    workbook.close()
    InventoryImports(str(database)).run("synthetic", str(source), kind="log-sheet")
    key = root / "key"
    key.write_bytes(Fernet.generate_key())
    key.chmod(0o600)
    destination = root / "destination"
    destination.mkdir(mode=0o700)
    return app, source, BackupConfig(database, destination, key, "sha256:" + "1" * 64)


def logical(database):
    with sqlite3.connect(database) as db:
        return list(db.iterdump())


class BackupTests(unittest.TestCase):
    def test_complete_encrypted_restore_http_receipts_and_schema_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, source, config = seed(root)
            client = app.test_client()
            listing = client.get("/shipments").data.decode()
            pages = ["/", "/exceptions", "/shipments"] + sorted(
                set(re.findall(r'href="(/shipments/[^"?]+)"', listing)) - {"/shipments/new"}
            )
            baseline = {p: client.get(p).data for p in pages}
            self.assertGreater(len(pages), 3)
            self.assertIn(b"Synthetic exception", baseline["/exceptions"])
            dump = logical(config.database)
            archive = backup(config, "pre-deploy")
            self.assertNotIn(b"SQLite format", archive.read_bytes())
            with sqlite3.connect(config.database) as db:
                db.execute("PRAGMA user_version=999")
                db.execute("DROP TABLE drives")
            with self.assertRaisesRegex(RuntimeError, "Unsupported database schema"):
                create_app(str(config.database))
            started = time.monotonic()
            restored = restore(config, archive, root / "fresh")
            after = create_app(str(restored)).test_client()
            self.assertEqual({p: after.get(p).data for p in pages}, baseline)
            self.assertEqual(logical(restored), dump)
            InventoryImports(str(restored)).run("synthetic", str(source), kind="log-sheet")
            self.assertEqual(logical(restored), dump)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 4 * 3600)
            print(
                f"SYNTHETIC complete decrypt/restore/HTTP/rerun elapsed={elapsed:.3f}s; off-host NOT verified"
            )
            with self.assertRaises(FileExistsError):
                restore(config, archive, root / "fresh")
            self.assertEqual(os.stat(restored).st_mode & 0o777, 0o600)

    def test_wrong_key_corruption_and_version_do_not_publish_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            archive = backup(config, "nightly")
            key2 = root / "other-key"
            key2.write_bytes(Fernet.generate_key())
            key2.chmod(0o600)
            for altered in (
                replace(config, key_file=key2),
                replace(config, release="sha256:" + "2" * 64),
            ):
                with self.assertRaises((InvalidToken, ValueError)):
                    restore(altered, archive, root / "rejected")
                self.assertFalse((root / "rejected").exists())
            archive.write_bytes(archive.read_bytes()[:-5] + b"BAD!!")
            with self.assertRaises(InvalidToken):
                restore(config, archive, root / "rejected")
            self.assertFalse((root / "rejected").exists())

    def test_retention_authentication_scope_and_failure_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            old = datetime.now(UTC) - timedelta(days=15)
            # Time control only at the host boundary, not domain or SQLite rules.
            with patch("backups.datetime") as clock:
                clock.now.return_value = old
                clock.strptime = datetime.strptime
                clock.fromisoformat = datetime.fromisoformat
                expired = backup(config, "nightly")
            unrelated = config.destination / "unrelated"
            unrelated.write_bytes(b"not ours")
            backup(config, "nightly")
            self.assertFalse(expired.exists())
            foreign = config.destination / expired.name
            foreign.write_bytes(b"foreign or corrupt artifact with matching filename")
            # Retention must not delete unauthenticated names or future good points.
            with patch("backups.datetime") as clock:
                clock.now.return_value = datetime.now(UTC) + timedelta(days=2)
                clock.strptime = datetime.strptime
                clock.fromisoformat = datetime.fromisoformat
                future = backup(config, "nightly")
            good = backup(config, "nightly")
            self.assertTrue(foreign.exists())
            self.assertTrue(future.exists())
            self.assertTrue(unrelated.exists())
            config.key_file.write_bytes(b"invalid key")
            with self.assertRaises(ValueError):
                backup(config, "nightly")
            self.assertTrue(good.exists())
            status = json.loads((config.destination / "status.json").read_text())
            self.assertEqual(status["result"], "failed")
            self.assertNotIn("key", json.dumps(status))

    def test_configured_preimport_fail_closed_and_successful_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app, source, config = seed(root)
            path = root / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "database": str(config.database),
                        "destination": str(config.destination),
                        "key_file": str(config.key_file),
                        "release": config.release,
                    }
                )
            )
            with patch.dict(os.environ, {"DRIVES_BACKUP_CONFIG": str(path)}):
                result = app.test_cli_runner().invoke(
                    args=["import-log-sheet", "--lineage", "synthetic", "--workbook", str(source)]
                )
                self.assertEqual(result.exit_code, 0)
                self.assertEqual(len(list(config.destination.glob("*pre-import*.fernet"))), 1)
                before = logical(config.database)
                config.key_file.write_bytes(b"invalid key")
                result = app.test_cli_runner().invoke(
                    args=["import-log-sheet", "--lineage", "synthetic", "--workbook", str(source)]
                )
                self.assertNotEqual(result.exit_code, 0)
                self.assertEqual(logical(config.database), before)
                self.assertNotIn("invalid key", result.output)

    def test_online_wal_backup_excludes_uncommitted_row_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            baseline = logical(config.database)
            with sqlite3.connect(config.database) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("DELETE FROM import_receipts")
                # Backup must see the last committed complete row/receipt set.
                archive = backup(config, "nightly")
                writer.rollback()
            restored = restore(config, archive, root / "fresh")
            self.assertEqual(logical(restored), baseline)

    def test_status_requires_fresh_authenticated_nightly_not_only_hooks(self):
        from backups import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            path = root / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "database": str(config.database),
                        "destination": str(config.destination),
                        "key_file": str(config.key_file),
                        "release": config.release,
                    }
                )
            )

            def status():
                with patch("sys.argv", ["backups", "--config", str(path), "status"]):
                    return main()

            self.assertEqual(status(), 1)
            archive = backup(config, "nightly")
            self.assertEqual(status(), 0)
            backup(config, "pre-deploy")
            self.assertEqual(status(), 0)
            nightly = config.destination / "nightly.json"
            value = json.loads(nightly.read_text())
            value["started"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
            nightly.write_text(json.dumps(value))
            self.assertEqual(status(), 1)
            value["started"] = datetime.now(UTC).isoformat()
            value["artifact"] = "../key"
            nightly.write_text(json.dumps(value))
            self.assertEqual(status(), 1)
            backup(config, "nightly")
            archive.write_bytes(b"corrupt")
            # Only the artifact referenced by nightly.json may prove freshness.
            current = json.loads(nightly.read_text())["artifact"]
            (config.destination / current).write_bytes(b"corrupt")
            self.assertEqual(status(), 1)

    def test_restore_rejects_link_and_oversize_before_creating_target(self):
        from backups import LIMIT

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            archive = backup(config, "nightly")
            link = root / "linked"
            link.symlink_to(archive)
            with self.assertRaises(OSError):
                restore(config, link, root / "rejected")
            with archive.open("wb") as stream:
                stream.truncate(LIMIT * 2 + 1)
            with self.assertRaises(ValueError):
                restore(config, archive, root / "rejected")
            self.assertFalse((root / "rejected").exists())

    def test_private_permissions_and_symlink_target_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, config = seed(root)
            archive = backup(config, "nightly")
            target = root / "target"
            target.symlink_to(config.destination, target_is_directory=True)
            with self.assertRaises(FileExistsError):
                restore(config, archive, target)
            config.key_file.chmod(0o644)
            with self.assertRaises(ValueError):
                backup(config, "nightly")
