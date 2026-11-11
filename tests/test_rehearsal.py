"""Host-command preparation seam; all inputs generated, never real data."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rehearsal import Hold, preflight_real, synthetic


class RehearsalTest(unittest.TestCase):
    def test_g4_hold_precedes_every_input_database_or_key_access(self):
        plan = {
            "mode": "real",
            "boundary": "this-mac-loopback",
            "workbooks": ["/synthetic-spy/never-open.xlsx"],
            "project_export": "/synthetic-spy/never-open.json",
            "database": "/synthetic-spy/never-open.sqlite",
            "offhost_destination": None,
            "key_custodian": None,
            "restore_operator": None,
        }
        # Tripwires: not just import calls; even stat/read/connect must not occur.
        with (
            patch.object(Path, "read_bytes", side_effect=AssertionError("input opened")),
            patch.object(Path, "stat", side_effect=AssertionError("input inspected")),
            patch.object(sqlite3, "connect", side_effect=AssertionError("database opened")),
            self.assertRaisesRegex(Hold, "G4_NOT_CONFIGURED"),
        ):
            preflight_real(plan)
        for field in ("offhost_destination", "key_custodian", "restore_operator"):
            partial = {
                **plan,
                "offhost_destination": "synthetic approved placeholder",
                "key_custodian": "synthetic custodian",
                "restore_operator": "synthetic operator",
                field: None,
            }
            with self.assertRaisesRegex(Hold, "G4_NOT_CONFIGURED"):
                preflight_real(partial)
        with self.assertRaisesRegex(Hold, "REAL_EXECUTION_DISABLED"):
            preflight_real(
                {
                    **plan,
                    "offhost_destination": "synthetic",
                    "key_custodian": "synthetic",
                    "restore_operator": "synthetic",
                }
            )

    def test_generated_rehearsal_retains_reconciliation_retry_restart_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "private-run"
            result = synthetic(root)
            report = json.loads((root / "report.json").read_text())
            self.assertEqual(result["result"], "synthetic-pass")
            self.assertFalse(result["real_rehearsal_passed"])
            self.assertEqual(result["g4"], "not-configured")
            self.assertEqual(
                report["comparisons"], ["retry", "app-restart", "decrypt-restore", "restored-rerun"]
            )
            self.assertTrue(report["fault_recovered"])
            self.assertEqual(report["quarantine"][0]["status"], "source-retained-unreadable")
            self.assertGreater(report["coverage"]["exception"], 0)
            self.assertGreater(report["coverage"]["usable"], 0)
            self.assertEqual(report["coverage"]["pending"], 0)
            self.assertEqual(report["restore"]["state_sha256"], report["state_sha256"])
            self.assertLess(report["restore"]["seconds"], 4 * 3600)
            self.assertEqual((root / "report.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                synthetic(root)
