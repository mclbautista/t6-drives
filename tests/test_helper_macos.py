"""Static checks and macOS Seam 1 E2E tests for the custodian Mac helper."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from support import DriveAppCase
from t6.verbs import DriveCatalogAtInput, DriveCatalogSnapshotsInput
from werkzeug.serving import WSGIRequestHandler, make_server

from app import create_app

HELPER_DIR = REPO_ROOT / "helper"
TOKEN = "synthetic-macos-helper-token-0123456789"
FORBIDDEN_PYTHON_RE = re.compile(r"\bpython(?:3(?:\.\d+)?)?\b", re.IGNORECASE)


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        pass


class HelperStaticTests(unittest.TestCase):
    def test_no_python_anywhere_in_helper_and_shell_syntax_is_valid(self) -> None:
        files = sorted(p for p in HELPER_DIR.iterdir() if p.is_file())
        self.assertGreaterEqual(len(files), 3)
        zsh_bin = shutil.which("zsh")

        for path in files:
            with self.subTest(file=path.name):
                content = path.read_text(encoding="utf-8")
                match = FORBIDDEN_PYTHON_RE.search(content)
                self.assertIsNone(
                    match,
                    f"{path.name} must not mention or invoke python/python3 (found {match!r})",
                )
                if content.startswith("#!/bin/zsh"):
                    self.assertTrue(
                        os.access(path, os.X_OK),
                        f"{path.name} must be executable",
                    )
                    if zsh_bin is not None:
                        res = subprocess.run(
                            [zsh_bin, "-n", str(path)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(
                            res.returncode,
                            0,
                            f"zsh -n failed on {path.name}: {res.stderr}",
                        )


@unittest.skipUnless(sys.platform == "darwin", "requires macOS")
class HelperMacOSE2ETests(DriveAppCase):
    def setUp(self) -> None:
        super().setUp()
        self.app = create_app(
            self.api.client(),
            self.outbox,
            retry_interval=0,
            helper_token=TOKEN,
        )
        self.server = make_server("127.0.0.1", 0, self.app, request_handler=QuietHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        self.work = Path(self.directory)
        self.trap_bin = self.work / "trap_bin"
        self.trap_bin.mkdir()
        for name in ("python", "python3"):
            trap = self.trap_bin / name
            trap.write_text("#!/bin/sh\necho 'FORBIDDEN python invocation' >&2\nexit 99\n")
            trap.chmod(0o755)

        self.stubs = self.work / "stubs"
        self.stubs.mkdir()
        self.serial_log = self.work / "serial_calls.log"
        self.dialog_log = self.work / "dialog_calls.log"
        self.open_log = self.work / "open_calls.log"

        self.serial_stub = self.stubs / "serial.sh"
        self.serial_stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.serial_log}"\n'
            'printf "%s" "${STUB_SERIAL:-}"\n'
        )
        self.serial_stub.chmod(0o755)

        self.dialog_stub = self.stubs / "dialog.sh"
        self.dialog_stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> "{self.dialog_log}"\n'
            'if [ "$1" = "label" ]; then\n'
            '  if [ "${STUB_LABEL_EXIT:-0}" != "0" ]; then\n'
            '    exit "${STUB_LABEL_EXIT}"\n'
            "  fi\n"
            '  printf "%s\\n" "${STUB_LABEL_VALUE:-}"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "confirm" ]; then\n'
            '  if [ "${STUB_CONFIRM_EXIT:-0}" != "0" ]; then\n'
            '    exit "${STUB_CONFIRM_EXIT}"\n'
            "  fi\n"
            '  printf "Save\\n"\n'
            "  exit 0\n"
            "fi\n"
            "exit 2\n"
        )
        self.dialog_stub.chmod(0o755)

        self.open_stub = self.stubs / "open.sh"
        self.open_stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{self.open_log}"\n')
        self.open_stub.chmod(0o755)

    def make_volume(self, name: str = "Shuttle_Vol_01") -> Path:
        vol = self.work / "volumes" / name
        vol.mkdir(parents=True, exist_ok=True)
        (vol / "notes.txt").write_bytes(b"hello")
        show_a = vol / "Show A"
        show_a.mkdir()
        (show_a / "clip 1.mov").write_bytes(b"0123456789a")
        sub = show_a / 'Sub "Quotes"'
        sub.mkdir()
        (sub / "take '1' \u2014 final.mov").write_bytes(b"0123456789abcdefg")
        (show_a / "EmptyDir").mkdir()

        # Skipped directories and root invisible files.
        (vol / ".Spotlight-V100" / "Store-V2").mkdir(parents=True)
        (vol / ".Spotlight-V100" / "Store-V2" / "index.db").write_bytes(b"idx")
        (vol / ".Trashes" / "501").mkdir(parents=True)
        (vol / ".Trashes" / "501" / "deleted.mov").write_bytes(b"del")
        (vol / ".fseventsd").mkdir()
        (vol / ".fseventsd" / "0000000000000001").write_bytes(b"fse")
        (vol / ".DocumentRevisions-V100" / "PerUID").mkdir(parents=True)
        (vol / ".DocumentRevisions-V100" / "PerUID" / "db").write_bytes(b"rev")
        (show_a / ".Trashes").mkdir()
        (show_a / ".Trashes" / "nested.mov").write_bytes(b"nest")
        (vol / ".DS_Store").write_bytes(b"ds")
        (vol / "._notes.txt").write_bytes(b"ad")
        return vol

    def write_config(self, confirm: str) -> Path:
        cfg = self.work / f"config-{confirm}"
        cfg.write_text(
            "# Custodian Mac helper config\n"
            f"url = {self.base_url}\n"
            f"token = {TOKEN}\n"
            f"confirm = {confirm}\n"
        )
        return cfg

    def helper_env(
        self,
        cfg: Path,
        *,
        serial: str = "",
        label_value: str = "",
        label_exit: int = 0,
        confirm_exit: int = 0,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.trap_bin}:{env.get('PATH', '')}"
        env["T6_HELPER_CONFIG"] = str(cfg)
        env["T6_HELPER_SERIAL_CMD"] = str(self.serial_stub)
        env["T6_HELPER_DIALOG_CMD"] = str(self.dialog_stub)
        env["T6_HELPER_OPEN_CMD"] = str(self.open_stub)
        env["STUB_SERIAL"] = serial
        env["STUB_LABEL_VALUE"] = label_value
        env["STUB_LABEL_EXIT"] = str(label_exit)
        env["STUB_CONFIRM_EXIT"] = str(confirm_exit)
        return env

    def run_scan_and_send(
        self,
        vol: Path,
        env: dict[str, str],
    ) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
        # captured_on has one-second precision and t6 refuses a scan that is not later than
        # the Drive's last one, so two scans in the same second would flake.
        last = getattr(self, "_last_scan_second", None)
        while last is not None and int(time.time()) <= last:
            time.sleep(0.05)
        scan_proc = subprocess.run(
            [str(HELPER_DIR / "t6-drive-scan"), str(vol)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self._last_scan_second = int(time.time())
        self.assertEqual(scan_proc.returncode, 0, f"scan failed: {scan_proc.stderr}")
        scan_doc = json.loads(scan_proc.stdout)
        send_proc = subprocess.run(
            [str(HELPER_DIR / "t6-drive-send")],
            input=scan_proc.stdout,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return scan_doc, send_proc

    def clear_logs(self) -> None:
        for log in (self.serial_log, self.dialog_log, self.open_log):
            if log.exists():
                log.unlink()

    def read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_ask_mode_scan_preview_confirm_save_and_open_by_serial(self) -> None:
        drive_id = self.api.add_drive(label="Shuttle Ask Drive", serial="SN-MAC-ASK-1")
        vol = self.make_volume("ASK_VOL")
        cfg = self.write_config("ask")
        env = self.helper_env(cfg, serial="SN-MAC-ASK-1")

        scan_doc, send_proc = self.run_scan_and_send(vol, env)
        self.assertEqual(send_proc.returncode, 0, send_proc.stderr)

        # Verify scan JSON structure and offset timestamp.
        self.assertEqual(scan_doc["serial"], "SN-MAC-ASK-1")
        self.assertEqual(scan_doc["volume_name"], "ASK_VOL")
        self.assertEqual(scan_doc["root_path"], "ASK_VOL")
        captured = datetime.fromisoformat(scan_doc["captured_on"])
        self.assertIsNotNone(captured.utcoffset())

        # Dialog was called once in confirm mode with Drive label and exact counts.
        dialogs = self.read_lines(self.dialog_log)
        self.assertEqual(dialogs, ["confirm Shuttle Ask Drive 6 0 0"])

        # Open command was called with the Drive's catalog URL.
        opened = self.read_lines(self.open_log)
        self.assertEqual(opened, [f"{self.base_url}/drive/{drive_id}/catalog"])

        # Catalog contains exact files/dirs and zero skipped folders/invisible root files.
        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["root_path"], "ASK_VOL")

        at = self.api.read(
            "drive_catalog_at", DriveCatalogAtInput, drive=drive_id, on=snaps[0]["captured_on"]
        )
        entries = at["entries"]
        by_path = {e["path"]: e for e in entries}
        expected_paths = {
            "Show A",
            "Show A/EmptyDir",
            'Show A/Sub "Quotes"',
            "Show A/Sub \"Quotes\"/take '1' \u2014 final.mov",
            "Show A/clip 1.mov",
            "notes.txt",
        }
        self.assertEqual(set(by_path), expected_paths)
        self.assertEqual(by_path["Show A"]["kind"], "dir")
        self.assertIsNone(by_path["Show A"].get("size_bytes"))
        self.assertEqual(by_path["Show A/EmptyDir"]["kind"], "dir")
        self.assertIsNone(by_path["Show A/EmptyDir"].get("size_bytes"))
        self.assertEqual(by_path["notes.txt"]["kind"], "file")
        self.assertEqual(by_path["notes.txt"]["size_bytes"], 5)
        self.assertEqual(by_path["Show A/clip 1.mov"]["size_bytes"], 11)
        self.assertEqual(
            by_path["Show A/Sub \"Quotes\"/take '1' \u2014 final.mov"]["size_bytes"], 17
        )
        for e in entries:
            self.assertIsNotNone(datetime.fromisoformat(e["mtime"]).utcoffset())

        # Canceling in confirm dialog saves nothing and opens nothing.
        self.clear_logs()
        (vol / "extra.txt").write_bytes(b"abc")
        cancel_env = self.helper_env(cfg, serial="SN-MAC-ASK-1", confirm_exit=1)
        _, cancel_send = self.run_scan_and_send(vol, cancel_env)
        self.assertEqual(cancel_send.returncode, 0, cancel_send.stderr)
        self.assertEqual(self.read_lines(self.dialog_log), ["confirm Shuttle Ask Drive 1 0 0"])
        self.assertEqual(self.read_lines(self.open_log), [])
        snaps_after = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps_after), 1)

    def test_silent_mode_and_volume_name_match_with_no_dialog_or_open(self) -> None:
        project = self.api.project("Mac Helper Show")
        drive_id = self.api.add_drive(label="Silent Drive")
        self.api.assign(drive_id, project, "VOL_BY_NAME", "master")

        vol = self.make_volume("VOL_BY_NAME")
        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="")

        _, send_proc = self.run_scan_and_send(vol, env)
        self.assertEqual(send_proc.returncode, 0, send_proc.stderr)
        self.assertEqual(self.read_lines(self.dialog_log), [])
        self.assertEqual(self.read_lines(self.open_log), [])

        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)

    def test_no_match_prompts_once_in_ask_mode_and_never_prompts_in_silent_mode(self) -> None:
        project = self.api.project("Unmatched Show")
        drive_id = self.api.add_drive(label="Typed Label Drive")
        self.api.assign(drive_id, project, "TYPED_ASSIGN_LABEL", "master")

        vol = self.make_volume("UNKNOWN_VOL")

        # Silent mode: no dialog, no open, catalog stays empty.
        silent_env = self.helper_env(self.write_config("silent"), serial="")
        _, silent_send = self.run_scan_and_send(vol, silent_env)
        self.assertNotEqual(silent_send.returncode, 0)
        self.assertEqual(self.read_lines(self.dialog_log), [])
        self.assertEqual(self.read_lines(self.open_log), [])
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id)[
                "snapshots"
            ],
            [],
        )

        # Ask mode: canceling the label prompt aborts cleanly after one prompt.
        self.clear_logs()
        cancel_label_env = self.helper_env(
            self.write_config("ask"), serial="", label_exit=1, label_value="TYPED_ASSIGN_LABEL"
        )
        _, cancel_send = self.run_scan_and_send(vol, cancel_label_env)
        self.assertEqual(cancel_send.returncode, 0, cancel_send.stderr)
        self.assertEqual(self.read_lines(self.dialog_log), ["label UNKNOWN_VOL"])
        self.assertEqual(self.read_lines(self.open_log), [])

        # Ask mode: typing a matching label prompts once for label, then confirm, then saves & opens.
        self.clear_logs()
        ask_env = self.helper_env(
            self.write_config("ask"), serial="", label_value="TYPED_ASSIGN_LABEL"
        )
        _, ok_send = self.run_scan_and_send(vol, ask_env)
        self.assertEqual(ok_send.returncode, 0, ok_send.stderr)
        self.assertEqual(
            self.read_lines(self.dialog_log),
            ["label UNKNOWN_VOL", "confirm Typed Label Drive 6 0 0"],
        )
        self.assertEqual(
            self.read_lines(self.open_log),
            [f"{self.base_url}/drive/{drive_id}/catalog"],
        )
        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)

    def test_applescript_compiles_cleanly_with_osacompile_and_build_helper(self) -> None:
        direct_app = self.work / "Direct.app"
        res = subprocess.run(
            ["osacompile", "-o", str(direct_app), str(HELPER_DIR / "Scan Drive.applescript")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, f"osacompile failed: {res.stderr}")
        self.assertTrue((direct_app / "Contents" / "MacOS" / "applet").exists())

        built_app = self.work / "Scan Drive.app"
        build_res = subprocess.run(
            [str(HELPER_DIR / "build-scan-drive-app"), str(built_app)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            build_res.returncode, 0, f"build-scan-drive-app failed: {build_res.stderr}"
        )
        self.assertTrue((built_app / "Contents" / "Resources" / "t6-drive-scan").is_file())
        self.assertTrue((built_app / "Contents" / "Resources" / "t6-drive-send").is_file())


if __name__ == "__main__":
    unittest.main()
