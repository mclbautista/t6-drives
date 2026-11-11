"""Host-admin encrypted recovery. Directory transport must be independently approved off-host.

All durable evidence currently lives in SQLite (including original workbook BLOBs).
A bounded, authenticated single-database envelope avoids archive extraction entirely.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from cryptography.fernet import Fernet

CORE = "f4b7618c2eb14bb5f85ea3c1fa597843448bc3af"
LIMIT = 32 * 1024 * 1024
NAME = re.compile(
    r"drives-v1-(nightly|pre-import|pre-deploy)-([0-9]{8}T[0-9]{6}Z)-[0-9a-f]{32}\.fernet"
)


@dataclass(frozen=True)
class BackupConfig:
    database: Path
    destination: Path
    key_file: Path
    release: str

    @classmethod
    def load(cls, path: Path) -> "BackupConfig":
        value = json.loads(path.read_text())
        return cls(
            Path(value["database"]),
            Path(value["destination"]),
            Path(value["key_file"]),
            value["release"],
        )

    def validate(self) -> None:
        if not all(p.is_absolute() for p in (self.database, self.destination, self.key_file)):
            raise ValueError("Absolute paths required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.release):
            raise ValueError("Immutable deployment image ID required")
        for path in (self.destination, self.key_file):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Private owner-only destination and key required")
        if not self.destination.is_dir() or not stat.S_ISREG(self.key_file.lstat().st_mode):
            raise ValueError("Invalid destination or key provider")


def cipher(config: BackupConfig) -> Fernet:
    # File path, not key bytes, is accepted in config/argv. No secrets in diagnostics.
    return Fernet(config.key_file.read_bytes().strip())


def atomic(path: Path, data: bytes) -> None:
    temp = path.parent / (".pending-" + uuid4().hex)
    try:
        descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def locked(config: BackupConfig) -> Iterator[None]:
    config.validate()
    descriptor = os.open(
        config.destination / ".backup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def check_database(path: Path) -> int:
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 1:
            raise ValueError("Unsupported recovery schema")
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity failure")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database references invalid")
    return int(version)


def decode(config: BackupConfig, token: bytes) -> tuple[dict, bytes]:
    if len(token) > LIMIT * 2:
        raise ValueError("Recovery size limit exceeded")
    value = json.loads(cipher(config).decrypt(token))
    if (
        value["format"] != 1
        or value["schema"] != 1
        or value["core"] != CORE
        or value["release"] != config.release
    ):
        raise ValueError("Recovery version mismatch; use the recorded pinned release")
    data = base64.b64decode(value["database"], validate=True)
    if len(data) > LIMIT or hashlib.sha256(data).hexdigest() != value["sha256"]:
        raise ValueError("Recovery integrity failure")
    return value, data


def backup(config: BackupConfig, reason: str) -> Path:
    if reason not in ("nightly", "pre-import", "pre-deploy"):
        raise ValueError("Unknown recovery reason")
    with locked(config):
        started = datetime.now(UTC)
        try:
            if config.database.is_symlink() or not config.database.is_file():
                raise ValueError("Source must be an existing regular database")
            with TemporaryDirectory(prefix="drives-backup-") as directory:
                snapshot = Path(directory) / "drives.sqlite"
                # SQLite online backup includes committed WAL state, never partial row
                # transactions. Imports are row-atomic, not whole-workbook atomic.
                deadline = time.monotonic() + 300

                def progress(status: int, remaining: int, total: int) -> None:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Snapshot timeout")

                with (
                    sqlite3.connect(config.database.as_uri() + "?mode=ro", uri=True) as source,
                    sqlite3.connect(snapshot) as target,
                ):
                    source.backup(target, pages=256, progress=progress, sleep=0.05)
                schema = check_database(snapshot)
                if snapshot.stat().st_size > LIMIT:
                    raise ValueError("Recovery size limit exceeded")
                data = snapshot.read_bytes()
            metadata = {
                "format": 1,
                "schema": schema,
                "core": CORE,
                "release": config.release,
                "started": started.isoformat(),
                "reason": reason,
                "sha256": hashlib.sha256(data).hexdigest(),
                "database": base64.b64encode(data).decode(),
            }
            token = cipher(config).encrypt(json.dumps(metadata).encode())
            _, verified = decode(config, token)
            if verified != data:
                raise ValueError("Encryption verification failed")
            name = f"drives-v1-{reason}-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}.fernet"
            published = config.destination / name
            atomic(published, token)
            # Read back the exact published artifact before deleting any recovery point.
            decode(config, read_artifact(published))
            retain(config, started)
            atomic(
                config.destination / "status.json",
                json.dumps(
                    {
                        "result": "ok",
                        "reason": reason,
                        "started": started.isoformat(),
                        "completed": datetime.now(UTC).isoformat(),
                        "release": config.release,
                        "artifact": published.name,
                    }
                ).encode(),
            )
            if reason == "nightly":
                atomic(
                    config.destination / "nightly.json",
                    (config.destination / "status.json").read_bytes(),
                )
            return published
        except Exception:
            # No path, private payload, exception message or key in status or stderr.
            try:
                atomic(
                    config.destination / "status.json",
                    json.dumps(
                        {"result": "failed", "reason": reason, "attempted": started.isoformat()}
                    ).encode(),
                )
            except OSError:
                pass  # Unavailable destination: nonzero command + stderr remain mandatory.
            raise


def retain(config: BackupConfig, now: datetime) -> None:
    """Only authenticated artifacts of this release/key; 14 calendar days, hooks too."""
    cutoff = now.date() - timedelta(days=13)
    for path in config.destination.iterdir():
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = datetime.strptime(match[2], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            if stamp.date() >= cutoff:
                continue
            metadata, _ = decode(config, read_artifact(path))
            if (
                datetime.fromisoformat(metadata["started"]).strftime("%Y%m%dT%H%M%SZ") != match[2]
                or metadata["reason"] != match[1]
            ):
                continue
        except Exception:  # noqa: BLE001, S112 — foreign/corrupt artifacts are never deleted.
            continue
        path.unlink()


def read_artifact(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LIMIT * 2:
            raise ValueError("Invalid recovery artifact")
        data = stream.read(LIMIT * 2 + 1)
        if len(data) > LIMIT * 2:
            raise ValueError("Recovery size limit exceeded")
        return data


def restore(config: BackupConfig, archive: Path, target: Path) -> Path:
    """Fresh directory only. Never overwrite a running or incompatible store."""
    config.validate()
    if not target.is_absolute():
        raise ValueError("Absolute fresh target required")
    _, data = decode(config, read_artifact(archive))
    # mkdir fails for *any* existing target (including symlinks); no archive paths,
    # compression, links, extraction, file modes or filenames can influence writes.
    target.mkdir(mode=0o700)
    # Leave private failed targets for administrator diagnosis; never overwrite them.
    database = target / "drives.sqlite"
    atomic(database, data)
    check_database(database)
    from app import create_app

    if create_app(str(database)).test_client().get("/health").status_code != 204:
        raise ValueError("Restored application failed readiness")
    return database


def pre_import(database: str) -> None:
    path = os.environ.get("DRIVES_BACKUP_CONFIG")
    if path:
        config = BackupConfig.load(Path(path))
        if config.database.resolve() != Path(database).resolve():
            raise ValueError("Recovery configuration database mismatch")
        backup(config, "pre-import")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    save = sub.add_parser("backup")
    save.add_argument("--reason", choices=["nightly", "pre-import", "pre-deploy"], required=True)
    recover = sub.add_parser("restore")
    recover.add_argument("--archive", type=Path, required=True)
    recover.add_argument("--target", type=Path, required=True)
    sub.add_parser("status")
    args = parser.parse_args()
    try:
        config = BackupConfig.load(args.config)
        if args.action == "backup":
            backup(config, args.reason)
        elif args.action == "restore":
            restore(config, args.archive, args.target)
        else:
            config.validate()
            status = json.loads((config.destination / "status.json").read_text())
            nightly = json.loads((config.destination / "nightly.json").read_text())
            age = datetime.now(UTC) - datetime.fromisoformat(nightly["started"])
            if (
                status["result"] != "ok"
                or nightly["result"] != "ok"
                or age < timedelta(0)
                or age > timedelta(hours=24)
                or not NAME.fullmatch(nightly["artifact"])
            ):
                raise ValueError("Recovery stale or failed")
            metadata, _ = decode(config, read_artifact(config.destination / nightly["artifact"]))
            if metadata["started"] != nightly["started"] or metadata["reason"] != "nightly":
                raise ValueError("Nightly provenance invalid")
        print("Recovery operation ok")
        return 0
    except Exception:  # noqa: BLE001 — sanitize host/private crypto and storage failures.
        print("Recovery operation failed; host administrator action required", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
