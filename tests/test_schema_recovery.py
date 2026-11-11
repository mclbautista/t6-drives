"""Synthetic schema recovery via actual startup/HTTP and consistent SQLite backup."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from t6.drives import Drive

from app import create_app, encode


class SchemaRecoveryTests(unittest.TestCase):
    def test_existing_synthetic_schema_migrates_and_backup_restores_future_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "synthetic.sqlite")
            recovery = str(Path(directory) / "recovery.sqlite")
            drive = Drive(label="Synthetic migration fixture")
            with sqlite3.connect(database) as db:
                db.execute(
                    "CREATE TABLE drives(id TEXT PRIMARY KEY, serial TEXT UNIQUE, facts TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT INTO drives VALUES(?,?,?)", (drive.id, drive.serial, encode(drive))
                )
            app = create_app(database)
            before = app.test_client().get("/").data
            self.assertIn(b"Synthetic migration fixture", before)
            with sqlite3.connect(database) as db, sqlite3.connect(recovery) as backup:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))
                baseline = list(db.iterdump())
                db.backup(backup)
            with sqlite3.connect(database) as db:
                db.execute("PRAGMA user_version=999")
                db.execute("DROP TABLE drives")
            with self.assertRaisesRegex(RuntimeError, "Unsupported database schema"):
                create_app(database)
            with sqlite3.connect(recovery) as backup, sqlite3.connect(database) as db:
                backup.backup(db)
            self.assertEqual(create_app(database).test_client().get("/").data, before)
            with sqlite3.connect(database) as db:
                self.assertEqual(list(db.iterdump()), baseline)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
