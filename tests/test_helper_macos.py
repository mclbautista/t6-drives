"""Static checks and macOS Seam 1 E2E tests for the custodian Mac helper."""

import json
import os
import plistlib
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
        names = {p.name for p in files}
        for expected in (
            "t6-drive-scan",
            "t6-drive-send",
            "t6-drive-agent",
            "t6-drive-hubs",
            "build-scan-drive-app",
            "Scan Drive.applescript",
            "dev.t6.drives-helper.plist",
        ):
            self.assertIn(expected, names)
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

    def test_launchd_plist_is_valid_and_matches_contract(self) -> None:
        plist_path = HELPER_DIR / "dev.t6.drives-helper.plist"
        data = plistlib.loads(plist_path.read_bytes())
        self.assertEqual(data["Label"], "dev.t6.drives-helper")
        self.assertEqual(
            data["ProgramArguments"],
            ["/Library/Application Support/t6-drives-helper/t6-drive-agent"],
        )
        self.assertEqual(data["WatchPaths"], ["/Volumes"])
        self.assertEqual(data["StartInterval"], 900)
        self.assertEqual(data["Nice"], 10)
        self.assertIs(data["LowPriorityIO"], True)
        self.assertEqual(data["ProcessType"], "Background")

        plutil_bin = shutil.which("plutil")
        if sys.platform == "darwin":
            self.assertIsNotNone(plutil_bin, "plutil must exist on macOS")
        if plutil_bin is not None:
            res = subprocess.run(
                [plutil_bin, "-lint", str(plist_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"plutil -lint failed: {res.stdout} {res.stderr}")


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
        self.app_requests: list[tuple[str, str]] = []

        @self.app.before_request
        def _record_app_request() -> None:
            from flask import request

            self.app_requests.append((request.method, request.path))

        self.server = make_server("127.0.0.1", 0, self.app, request_handler=QuietHandler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

        self.work = Path(self.directory)
        self.volumes_dir = self.work / "volumes"
        self.volumes_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = self.work / "state"
        self.log_dir = self.work / "logs"

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
        vol = self.volumes_dir / name
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

    def write_config(
        self, confirm: str, *, url: str | None = None, filename: str | None = None
    ) -> Path:
        cfg = self.work / (filename or f"config-{confirm}")
        cfg.write_text(
            "# Custodian Mac helper config\n"
            f"url = {url or self.base_url}\n"
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
        rescan_seconds: int | None = None,
        max_bytes: int | None = None,
        usb_cmd: str | Path | None = None,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{self.trap_bin}:{env.get('PATH', '')}"
        env["T6_HELPER_CONFIG"] = str(cfg)
        env["T6_HELPER_VOLUMES_DIR"] = str(self.volumes_dir)
        env["T6_HELPER_STATE_DIR"] = str(self.state_dir)
        env["T6_HELPER_LOG_DIR"] = str(self.log_dir)
        env["T6_HELPER_BIN_DIR"] = str(HELPER_DIR)
        env["T6_HELPER_SERIAL_CMD"] = str(self.serial_stub)
        env["T6_HELPER_DIALOG_CMD"] = str(self.dialog_stub)
        env["T6_HELPER_OPEN_CMD"] = str(self.open_stub)
        env["STUB_SERIAL"] = serial
        env["STUB_LABEL_VALUE"] = label_value
        env["STUB_LABEL_EXIT"] = str(label_exit)
        env["STUB_CONFIRM_EXIT"] = str(confirm_exit)
        if rescan_seconds is not None:
            env["T6_HELPER_RESCAN_SECONDS"] = str(rescan_seconds)
        if max_bytes is not None:
            env["T6_HELPER_MAX_BYTES"] = str(max_bytes)
        if usb_cmd is not None:
            env["T6_HELPER_USB_CMD"] = str(usb_cmd)
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

    def run_agent(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [str(HELPER_DIR / "t6-drive-agent")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"agent failed: {proc.stderr}")
        return proc

    def clear_logs(self) -> None:
        for log in (self.serial_log, self.dialog_log, self.open_log):
            if log.exists():
                log.unlink()

    def read_lines(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line]

    def helper_log_lines(self) -> list[str]:
        return self.read_lines(self.log_dir / "helper.log")

    def work_files(self) -> list[Path]:
        work_dir = self.state_dir / "work"
        return sorted(work_dir.iterdir()) if work_dir.exists() else []

    def queue_files(self) -> list[Path]:
        queue_dir = self.state_dir / "queue"
        return sorted(queue_dir.iterdir()) if queue_dir.exists() else []

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

        # Silent mode: no dialog, no open, exits 10 (no_drive), catalog stays empty.
        silent_env = self.helper_env(self.write_config("silent"), serial="")
        _, silent_send = self.run_scan_and_send(vol, silent_env)
        self.assertEqual(silent_send.returncode, 10, silent_send.stderr)
        self.assertEqual(self.read_lines(self.dialog_log), [])
        self.assertEqual(self.read_lines(self.open_log), [])
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id)[
                "snapshots"
            ],
            [],
        )

        # Ask mode: canceling or leaving the label prompt empty exits 10 (waiting) so queued scans stay queued.
        self.clear_logs()
        cancel_label_env = self.helper_env(
            self.write_config("ask"), serial="", label_exit=1, label_value="TYPED_ASSIGN_LABEL"
        )
        _, cancel_send = self.run_scan_and_send(vol, cancel_label_env)
        self.assertEqual(cancel_send.returncode, 10, cancel_send.stderr)
        self.assertEqual(self.read_lines(self.dialog_log), ["label UNKNOWN_VOL"])
        self.assertEqual(self.read_lines(self.open_log), [])

        self.clear_logs()
        empty_label_env = self.helper_env(
            self.write_config("ask"), serial="", label_exit=0, label_value="   "
        )
        _, empty_send = self.run_scan_and_send(vol, empty_label_env)
        self.assertEqual(empty_send.returncode, 10, empty_send.stderr)
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

    def test_agent_mount_rescan_unmount_sends_updated_scan_and_ignores_symlinks(self) -> None:
        drive_id = self.api.add_drive(label="Agent Drive", serial="SN-AGENT-1")
        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="SN-AGENT-1", rescan_seconds=0)

        # Symlink and dotfile directory in VOLUMES_DIR must be ignored.
        (self.volumes_dir / "Macintosh HD").symlink_to("/")
        (self.volumes_dir / ".hidden_vol").mkdir()

        vol = self.make_volume("AGENT_VOL")
        self.run_agent(env)

        self.assertTrue((self.state_dir / "work" / "AGENT_VOL.json").is_file())
        self.assertTrue((self.state_dir / "work" / "AGENT_VOL.ts").is_file())
        self.assertFalse((self.state_dir / "work" / "Macintosh HD.json").exists())
        self.assertFalse((self.state_dir / "work" / ".hidden_vol.json").exists())
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id)[
                "snapshots"
            ],
            [],
        )

        # Add file between rescans; rescan picks it up.
        (vol / "late_addition.mov").write_bytes(b"late-clip")
        self.run_agent(env)
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id)[
                "snapshots"
            ],
            [],
        )

        # Unmount sends one scan containing the newly added file and empties work/ and queue/.
        shutil.rmtree(vol)
        self.run_agent(env)

        self.assertEqual(self.work_files(), [])
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(self.read_lines(self.dialog_log), [])
        self.assertEqual(self.read_lines(self.open_log), [])

        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)
        at = self.api.read(
            "drive_catalog_at", DriveCatalogAtInput, drive=drive_id, on=snaps[0]["captured_on"]
        )
        by_path = {e["path"]: e for e in at["entries"]}
        self.assertIn("late_addition.mov", by_path)
        self.assertEqual(by_path["late_addition.mov"]["size_bytes"], 9)

        logs = self.helper_log_lines()
        self.assertTrue(any(" taken " in line and "AGENT_VOL" in line for line in logs), logs)
        self.assertTrue(any(" sent " in line and "AGENT_VOL" in line for line in logs), logs)

    def test_agent_two_eject_sessions_on_one_day_produce_two_catalog_scans(self) -> None:
        drive_id = self.api.add_drive(label="Two Sessions Drive", serial="SN-TWO-SESS")
        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="SN-TWO-SESS")

        # Session 1
        vol1 = self.make_volume("SESS_VOL")
        self.run_agent(env)
        # With default 900s interval, an immediate second wake does not rescan.
        (vol1 / "between_rescans.txt").write_bytes(b"skip")
        self.run_agent(env)
        shutil.rmtree(vol1)
        self.run_agent(env)

        snaps1 = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps1), 1)
        at1 = self.api.read(
            "drive_catalog_at", DriveCatalogAtInput, drive=drive_id, on=snaps1[0]["captured_on"]
        )
        self.assertNotIn("between_rescans.txt", {e["path"] for e in at1["entries"]})

        # Ensure second session captured_on timestamp is strictly later than session 1.
        time.sleep(1.1)

        # Session 2 on the same day
        vol2 = self.make_volume("SESS_VOL")
        (vol2 / "session2.mov").write_bytes(b"second-eject")
        self.run_agent(env)
        shutil.rmtree(vol2)
        self.run_agent(env)

        snaps2 = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps2), 2)
        at2 = self.api.read(
            "drive_catalog_at", DriveCatalogAtInput, drive=drive_id, on=snaps2[1]["captured_on"]
        )
        self.assertIn("session2.mov", {e["path"] for e in at2["entries"]})

    def test_agent_queues_when_api_down_and_sends_on_next_wake_when_up(self) -> None:
        import socket

        drive_id = self.api.add_drive(label="Outage Agent Drive", serial="SN-OUTAGE-AGENT")
        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="SN-OUTAGE-AGENT")

        vol = self.make_volume("OUTAGE_VOL")
        self.run_agent(env)

        self.api.mode = "down"
        shutil.rmtree(vol)
        self.run_agent(env)

        self.assertEqual(self.work_files(), [])
        queued = self.queue_files()
        self.assertEqual(len(queued), 1)
        queued_key = json.loads(queued[0].read_text(encoding="utf-8"))["idempotency_key"]
        self.assertTrue(queued_key)
        self.assertNotIn("/v1/drive/catalog-snapshot", self.api.requests)
        logs = self.helper_log_lines()
        self.assertTrue(any(" waiting " in line and "try_later" in line for line in logs), logs)

        # Network/connection failure also keeps the scan queued with the same idempotency_key.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]
        down_cfg = self.write_config(
            "silent", url=f"http://127.0.0.1:{closed_port}", filename="config-closed"
        )
        self.run_agent(self.helper_env(down_cfg, serial="SN-OUTAGE-AGENT"))
        queued_after_net = self.queue_files()
        self.assertEqual(len(queued_after_net), 1)
        self.assertEqual(
            json.loads(queued_after_net[0].read_text(encoding="utf-8"))["idempotency_key"],
            queued_key,
        )

        # Next agent wake after API returns sends it and empties queue/.
        self.api.mode = "up"
        self.run_agent(env)
        self.assertEqual(self.queue_files(), [])
        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)
        logs = self.helper_log_lines()
        self.assertTrue(any(" sent " in line and queued_key in line for line in logs), logs)

    def test_agent_unknown_serial_waits_in_queue_then_sends_after_drive_add(self) -> None:
        silent_env = self.helper_env(self.write_config("silent"), serial="SN-NEW-DRIVE-1")
        vol = self.make_volume("UNKNOWN_SERIAL_VOL")
        self.run_agent(silent_env)
        shutil.rmtree(vol)
        self.run_agent(silent_env)

        self.assertEqual(self.work_files(), [])
        self.assertEqual(len(self.queue_files()), 1)
        logs = self.helper_log_lines()
        self.assertTrue(any(" waiting " in line and "no_drive" in line for line in logs), logs)

        # In ask mode, canceling the label prompt still keeps the scan queued (exit 10).
        ask_cancel_env = self.helper_env(
            self.write_config("ask"), serial="SN-NEW-DRIVE-1", label_exit=1
        )
        self.run_agent(ask_cancel_env)
        self.assertEqual(len(self.queue_files()), 1)
        self.assertEqual(self.read_lines(self.dialog_log), ["label UNKNOWN_SERIAL_VOL"])

        # Once the Drive is added, the next agent wake sends the queued scan and empties queue/.
        drive_id = self.api.add_drive(label="Newly Added Drive", serial="SN-NEW-DRIVE-1")
        self.run_agent(silent_env)
        self.assertEqual(self.queue_files(), [])
        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)
        logs = self.helper_log_lines()
        self.assertTrue(
            any(" sent " in line and "UNKNOWN_SERIAL_VOL" in line for line in logs), logs
        )

    def test_agent_too_large_scan_is_logged_never_queued_and_never_sent(self) -> None:
        drive_id = self.api.add_drive(label="Too Large Drive", serial="SN-TOO-LARGE")
        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="SN-TOO-LARGE", max_bytes=100)

        vol = self.make_volume("BIG_VOL")
        self.run_agent(env)

        # Direct send also rejects over T6_HELPER_MAX_BYTES with exit 13.
        work_scan = self.state_dir / "work" / "BIG_VOL.json"
        self.assertTrue(work_scan.is_file())
        direct_send = subprocess.run(
            [str(HELPER_DIR / "t6-drive-send"), str(work_scan)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(direct_send.returncode, 13, direct_send.stderr)

        shutil.rmtree(vol)
        self.run_agent(env)

        self.assertEqual(self.work_files(), [])
        self.assertEqual(self.queue_files(), [])
        self.assertEqual(
            self.api.read("drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id)[
                "snapshots"
            ],
            [],
        )
        logs = self.helper_log_lines()
        self.assertTrue(any(" too_large " in line and "BIG_VOL" in line for line in logs), logs)
        self.assertFalse(any(" sent " in line for line in logs), logs)

    def test_agent_older_scan_refused_is_logged_and_dropped_from_queue(self) -> None:
        drive_id = self.api.add_drive(label="Order Refusal Drive", serial="SN-ORDER-1")
        self.api.scan(
            drive_id,
            "2099-01-01T12:00:00+08:00",
            [{"path": "future.txt", "kind": "file", "size_bytes": 1}],
            root_path="ORDER_VOL",
        )

        cfg = self.write_config("silent")
        env = self.helper_env(cfg, serial="SN-ORDER-1")
        vol = self.make_volume("ORDER_VOL")
        self.run_agent(env)
        shutil.rmtree(vol)
        self.run_agent(env)

        self.assertEqual(self.work_files(), [])
        self.assertEqual(self.queue_files(), [])
        snaps = self.api.read(
            "drive_catalog_snapshots", DriveCatalogSnapshotsInput, drive=drive_id
        )["snapshots"]
        self.assertEqual(len(snaps), 1)
        logs = self.helper_log_lines()
        self.assertTrue(any(" refused " in line and "never" in line for line in logs), logs)

    def test_agent_hub_log_records_updates_and_sends_nothing(self) -> None:
        usb_json_path = self.work / "usb.json"
        usb_stub = self.stubs / "usb.sh"
        usb_stub.write_text(f'#!/bin/sh\ncat "{usb_json_path}"\n')
        usb_stub.chmod(0o755)

        usb_tree = {
            "SPUSBDataType": [
                {
                    "_name": "USB31Bus",
                    "host_controller": "AppleUSBXHCITR",
                    "_items": [
                        {
                            "_name": "Anker 10-Port Hub",
                            "manufacturer": "Anker",
                            "product_id": "0x0610",
                            "vendor_id": "0x291a",
                            "serial_num": "ANKER-HUB-001",
                            "_items": [
                                {
                                    "_name": "Extreme Portable SSD",
                                    "manufacturer": "SanDisk",
                                    "product_id": "0x55a5",
                                    "vendor_id": "0x0781",
                                    "serial_num": "DISK-SN-999",
                                }
                            ],
                        },
                        {
                            "_name": "USB2.0 Hub",
                            "vendor_id": "0x05e3",
                            "product_id": "0x0610",
                        },
                        {
                            "_name": "Standalone Flash Drive",
                            "manufacturer": "Samsung",
                            "vendor_id": "0x090c",
                            "product_id": "0x1000",
                            "serial_num": "FLASH-SN-1",
                        },
                    ],
                }
            ]
        }
        usb_json_path.write_text(json.dumps(usb_tree), encoding="utf-8")

        cfg = self.write_config("silent")
        env = self.helper_env(cfg, usb_cmd=usb_stub)

        # Wake 1: records both hubs, ignores non-hub disks and host controller.
        self.run_agent(env)
        hubs_log = self.log_dir / "hubs.log"
        lines1 = self.read_lines(hubs_log)
        self.assertEqual(len(lines1), 2, lines1)
        rows1 = [line.split("\t") for line in lines1]
        for row in rows1:
            self.assertEqual(len(row), 5, row)
            self.assertEqual(row[3], row[4])
            self.assertIsNotNone(datetime.fromisoformat(row[3]).utcoffset())
        self.assertEqual(rows1[0][:3], ["Anker", "Anker 10-Port Hub", "ANKER-HUB-001"])
        self.assertEqual(rows1[1][:3], ["0x05e3", "USB2.0 Hub", "-"])
        raw_log1 = hubs_log.read_text(encoding="utf-8")
        self.assertNotIn("Extreme Portable SSD", raw_log1)
        self.assertNotIn("Standalone Flash Drive", raw_log1)
        self.assertNotIn("USB31Bus", raw_log1)

        # Wake 2: updates last_seen without duplicating or altering first_seen.
        time.sleep(1.1)
        self.run_agent(env)
        lines2 = self.read_lines(hubs_log)
        self.assertEqual(len(lines2), 2, lines2)
        rows2 = [line.split("\t") for line in lines2]
        for r1, r2 in zip(rows1, rows2, strict=True):
            self.assertEqual(r2[:3], r1[:3])
            self.assertEqual(r2[3], r1[3])
            self.assertGreater(datetime.fromisoformat(r2[4]), datetime.fromisoformat(r1[4]))

        # Wake 3: unplugged hub stays in hubs.log with preserved timestamps.
        time.sleep(1.1)
        usb_tree["SPUSBDataType"][0]["_items"] = [usb_tree["SPUSBDataType"][0]["_items"][0]]
        usb_json_path.write_text(json.dumps(usb_tree), encoding="utf-8")
        self.run_agent(env)
        lines3 = self.read_lines(hubs_log)
        self.assertEqual(len(lines3), 2, lines3)
        rows3 = [line.split("\t") for line in lines3]
        self.assertEqual(rows3[0][:4], rows2[0][:4])
        self.assertGreater(datetime.fromisoformat(rows3[0][4]), datetime.fromisoformat(rows2[0][4]))
        self.assertEqual(rows3[1], rows2[1])

        # Zero HTTP requests were sent to the drives app or VerbAPI.
        self.assertEqual(self.app_requests, [])
        self.assertEqual(self.api.requests, [])

    def test_agent_continues_when_usb_tree_fails_or_is_empty(self) -> None:
        fail_usb = self.stubs / "usb-fail.sh"
        fail_usb.write_text("#!/bin/sh\nexit 1\n")
        fail_usb.chmod(0o755)

        empty_usb = self.stubs / "usb-empty.sh"
        empty_usb.write_text("#!/bin/sh\n")
        empty_usb.chmod(0o755)

        self.log_dir.mkdir(parents=True, exist_ok=True)
        hubs_log = self.log_dir / "hubs.log"
        preserved = "Anker\tAnker Hub\tSN-1\t2026-11-25T10:00:00+08:00\t2026-11-25T10:00:00+08:00\n"
        hubs_log.write_text(preserved, encoding="utf-8")

        vol = self.make_volume("USB_FAIL_VOL")
        cfg = self.write_config("silent")

        self.run_agent(self.helper_env(cfg, serial="SN-USB-FAIL", usb_cmd=fail_usb))
        self.assertTrue((self.state_dir / "work" / f"{vol.name}.json").is_file())
        self.assertEqual(hubs_log.read_text(encoding="utf-8"), preserved)

        self.run_agent(self.helper_env(cfg, serial="SN-USB-FAIL", usb_cmd=empty_usb))
        self.assertEqual(hubs_log.read_text(encoding="utf-8"), preserved)
        self.assertEqual(self.app_requests, [])
        self.assertEqual(self.api.requests, [])


if __name__ == "__main__":
    unittest.main()
