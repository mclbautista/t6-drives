"""Host-only, synthetic rehearsal and fail-closed real-input preflight.

There is intentionally NO real-input execution command, cutover or freeze option.
Reports contain private evidence and must never be published. Stdout is allowlisted.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from cryptography.fernet import Fernet
from openpyxl import Workbook, load_workbook

from app import create_app
from assignments import Assignments
from backups import CORE, LIMIT, BackupConfig, atomic, backup, check_database, restore
from import_store import SQLiteDriveStore
from imports import InventoryImports, row_key

ROOT = Path(__file__).resolve().parent


class Hold(ValueError):
    """Only fixed, public blocker codes may be exposed."""


def preflight_real(plan: dict[str, Any]) -> None:
    # Metadata ONLY: never stat/open an input, database, key or destination here.
    # Even supplied values cannot prove off-host access, custody or restore.
    required = ("offhost_destination", "key_custodian", "restore_operator")
    if any(
        not isinstance(plan.get(k), str)
        or not plan[k].strip()
        or plan[k].strip().casefold() in ("none", "none for now", "unconfigured")
        for k in required
    ):
        raise Hold("G4_NOT_CONFIGURED")
    raise Hold("REAL_EXECUTION_DISABLED_PENDING_INDEPENDENT_G4_VERIFICATION")


def fingerprint(database: Path) -> str:
    check_database(database)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()


def capacity(database: Path, inputs: list[Path]) -> None:
    # Raw source BLOBs + SQLite overhead + snapshot must fit the bounded envelope.
    # Conservative estimate before reading generated sources, exact check afterward.
    size = database.stat().st_size + sum(p.stat().st_size * 4 for p in inputs)
    if size > LIMIT or shutil.disk_usage(database.parent).free < 512 * 1024 * 1024:
        raise Hold("CAPACITY_HOLD_32MIB_OR_SCRATCH_SPACE")


def generate(root: Path) -> list[tuple[str, Path, str]]:
    inventory = root / "synthetic-inventory.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Synthetic unresolved Project"
    sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "SPACE", "T6 IN"])
    sheet.append(["Synthetic uncertain inventory", None, "2TB", "YES"])
    sheet.append(["Synthetic accepted inventory", "SYN-PREP-INV", "4TB", "YES"])
    sheet.append([None, None, None, "unsafe synthetic evidence"])
    book.save(inventory)
    book.close()
    movements = root / "synthetic-movements.xlsx"
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Synthetic batch"
    sheet.append(["DATE: Jan 2, 2025"])
    sheet.append(["HARD DRIVE LABEL", "SERIAL NUMBER", "SPACE", "T6 IN"])
    sheet.append(["Synthetic moving drive", "SYN-PREP-MOVE", "8TB", "YES"])
    month = book.create_sheet("Synthetic month")
    month.append(["HARD DRIVE LABEL", "DATE", "IN/OUT", "RECEIVED BY", "REMARKS"])
    for _ in range(2):
        month.append(
            [
                "Synthetic repeated label",
                "Jan 3, 2025",
                "OUT",
                "Synthetic recipient",
                "S/N: SYN-PREP-MOVE",
            ]
        )
    month.append(["Synthetic unattached", None, "OUT", None, "unattached synthetic evidence"])
    month.append(["Synthetic unknown date", "not a date", "IN", None, "S/N: SYN-PREP-MOVE"])
    book.save(movements)
    book.close()
    for path in (inventory, movements):
        path.chmod(0o600)
    return [
        ("synthetic-inventory", inventory, "inventory"),
        ("synthetic-movements", movements, "log-sheet"),
    ]


def reconcile(database: Path, sources: list[tuple[str, Path, str]]) -> dict[str, Any]:
    """Independent source enumeration vs durable row journal/receipts, private only."""
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    with sqlite3.connect(database) as db:
        for lineage, path, kind in sources:
            held = db.execute(
                "SELECT workbook,digest,source,status,kind FROM import_sources WHERE lineage=?",
                (lineage,),
            ).fetchone()
            raw = path.read_bytes()
            if held != (str(path), hashlib.sha256(raw).hexdigest(), raw, "complete", kind):
                raise Hold("SOURCE_LINEAGE_OR_COMPLETION_MISMATCH")
            expected: set[str] = set()
            book = load_workbook(path, read_only=True, data_only=False)
            try:
                for sheet in book:
                    for number, cells in enumerate(sheet.iter_rows(values_only=True), 1):
                        if not any(v is not None and str(v).strip() for v in cells):
                            continue
                        key = row_key(str(path), sheet.title, number)
                        expected.add(key)
                        evidence = db.execute(
                            "SELECT status,reasons,evidence FROM import_evidence WHERE row_key=? AND lineage=?",
                            (key, lineage),
                        ).fetchone()
                        receipt = db.execute(
                            "SELECT r.drive_id FROM import_receipts r JOIN drives d ON d.id=r.drive_id WHERE row_key=?",
                            (key,),
                        ).fetchone()
                        if not evidence or evidence[0] not in ("usable", "structure", "exception"):
                            raise Hold("SOURCE_COVERAGE_HOLD")
                        state, reasons, retained = evidence
                        if json.loads(retained) != list(cells):
                            raise Hold("SOURCE_EVIDENCE_MISMATCH")
                        if (state == "usable" and not receipt) or (
                            state == "exception" and not json.loads(reasons)
                        ):
                            raise Hold("SOURCE_RECORD_OR_EXCEPTION_HOLD")
                        counts[state] += 1
                        rows.append(
                            {
                                "lineage": lineage,
                                "path": str(path),
                                "sheet": sheet.title,
                                "row": number,
                                "key": key,
                                "status": state,
                                "drive_id": receipt[0] if receipt else None,
                                "reasons": json.loads(reasons),
                                "evidence": json.loads(retained),
                            }
                        )
            finally:
                book.close()
            actual = {
                r[0]
                for r in db.execute(
                    "SELECT row_key FROM import_evidence WHERE lineage=?", (lineage,)
                )
            }
            if actual != expected:
                raise Hold("SOURCE_ROW_SET_MISMATCH")
    return {
        "rows": rows,
        "coverage": {k: counts[k] for k in ("usable", "exception", "structure", "pending")},
    }


def pages(database: Path) -> dict[str, str]:
    client = create_app(str(database), custody_actor_policy="self-declared-required").test_client()
    listing = client.get("/shipments").text
    paths = ["/", "/exceptions", "/shipments"] + sorted(
        set(re.findall(r'href="(/shipments/[^"?]+)"', listing)) - {"/shipments/new"}
    )
    with sqlite3.connect(database) as db:
        paths += ["/drive/" + row[0] for row in db.execute("SELECT id FROM drives ORDER BY rowid")]
    result = {}
    for path in paths:
        response = client.get(path)
        if response.status_code != 200:
            raise Hold("HTTP_RECONCILIATION_HOLD")
        # Tokens are per-session, not durable evidence.
        stable = re.sub(r'name="csrf" value="[^"]+"', 'name="csrf"', response.text)
        result[path] = hashlib.sha256(stable.encode()).hexdigest()
    return result


class SyntheticReader:
    def read(self) -> dict[str, Any]:
        return {
            "complete": True,
            "projects": [
                {
                    "t6_id": "t6p_" + "1" * 32,
                    "name": "Synthetic cached Project",
                    "active_page_id": "2" * 32,
                    "legacy_page_ids": [],
                }
            ],
        }


def synthetic(root: Path) -> dict[str, Any]:
    # No input path arguments. Every workbook, key and database is created here.
    if not root.is_absolute() or root.parent.resolve() != root.parent or root.is_symlink():
        raise Hold("PRIVATE_FRESH_ABSOLUTE_DIRECTORY_REQUIRED")
    if (
        subprocess.run(
            ["git", "-C", str(root.parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    ):
        raise Hold("PRIVATE_EVIDENCE_MUST_BE_OUTSIDE_GIT")
    if os.environ.get("DRIVES_BACKUP_CONFIG"):
        raise Hold("UNSET_EXTERNAL_BACKUP_CONFIG_FOR_SYNTHETIC_RUN")
    root.mkdir(mode=0o700)  # exclusive; never attach an existing store or input
    report: dict[str, Any] = {
        "result": "failed",
        "core": CORE,
        "image_pin": None,
        "runtime": "local-python-not-container",
        "g4": "not-configured",
        "real_rehearsal_passed": False,
        "production_active": False,
        "workbooks_frozen": False,
        "g5": "separate-approval-required",
    }
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        tracked = subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files"], text=True
        ).splitlines()
        code = hashlib.sha256()
        for name in sorted(set(tracked) | {"rehearsal.py"}):
            path = ROOT / name
            if path.is_file():
                code.update(name.encode() + b"\0" + path.read_bytes())
        report.update(code_commit=revision, code_tree_sha256=code.hexdigest())
        database = root / "rehearsal.sqlite"
        create_app(str(database), custody_actor_policy="self-declared-required")
        database.chmod(0o600)
        Assignments(str(database)).refresh(SyntheticReader())
        sources = generate(root)
        capacity(database, [s[1] for s in sources])
        destination = root / "same-host-synthetic-backups"
        destination.mkdir(mode=0o700)
        key = root / "synthetic-only.key"
        atomic(key, Fernet.generate_key())
        # Backup API requires a SHA identifier. This is a synthetic envelope label,
        # explicitly NOT an image pin or deployment/container evidence.
        config = BackupConfig(database, destination, key, "sha256:" + code.hexdigest())
        report["backup_release_kind"] = "synthetic-source-tree-label-not-image"
        backup(config, "pre-import")
        lineage, path, kind = sources[0]

        def interrupt(stage: str) -> None:
            if stage == "before-row-commit":
                raise RuntimeError("Synthetic interruption")

        try:
            InventoryImports(
                str(database), store=SQLiteDriveStore(str(database), checkpoint=interrupt)
            ).run(lineage, str(path), kind=kind)
        except RuntimeError:
            if InventoryImports(str(database)).status(lineage) != "failed":
                raise Hold("FAILED_RUN_EVIDENCE_MISSING") from None
        else:
            raise Hold("FAULT_INJECTION_NOT_EXERCISED")
        report["fault_recovered"] = False
        for lineage, path, kind in sources:
            backup(config, "pre-import")
            InventoryImports(str(database)).run(lineage, str(path), kind=kind)
        report["fault_recovered"] = True
        # Unreadable source has no enumerable rows: retain and reconcile the
        # entire original BLOB as a source-level quarantine, never claim usable.
        unreadable = root / "synthetic-unreadable.xlsx"
        atomic(unreadable, b"Synthetic invalid workbook ZIP; no real data")
        backup(config, "pre-import")
        try:
            InventoryImports(str(database)).run("synthetic-unreadable", str(unreadable))
        except BadZipFile:
            pass
        else:
            raise Hold("UNREADABLE_SOURCE_NOT_REJECTED")
        with sqlite3.connect(database) as db:
            held = db.execute(
                "SELECT source,status FROM import_sources WHERE lineage=?",
                ("synthetic-unreadable",),
            ).fetchone()
        if held != (unreadable.read_bytes(), "failed"):
            raise Hold("UNREADABLE_SOURCE_NOT_DURABLE")
        report["quarantine"] = [
            {
                "lineage": "synthetic-unreadable",
                "path": str(unreadable),
                "sha256": hashlib.sha256(unreadable.read_bytes()).hexdigest(),
                "status": "source-retained-unreadable",
                "rows_enumerated": False,
            }
        ]
        report.update(reconcile(database, sources))
        report["inputs"] = [
            {
                "lineage": l,
                "path": str(p),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "kind": k,
            }
            for l, p, k in sources
        ]
        baseline = fingerprint(database)
        http = pages(database)
        report["state_sha256"] = baseline
        report["http_sha256"] = http
        report["comparisons"] = []
        for _ in range(2):
            for lineage, path, kind in sources:
                InventoryImports(str(database)).run(lineage, str(path), kind=kind)
            if fingerprint(database) != baseline or pages(database) != http:
                raise Hold("RERUN_OR_APP_RESTART_MISMATCH")
        report["comparisons"].append("retry")
        # Fresh interpreter reopens the app, not just another factory in this process.
        restarted = json.loads(
            subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json,sys; from pathlib import Path; from rehearsal import fingerprint,pages; "
                        "p=Path(sys.argv[1]); print(json.dumps([fingerprint(p),pages(p)]))"
                    ),
                    str(database),
                ],
                cwd=ROOT,
                text=True,
            )
        )
        if restarted != [baseline, http]:
            raise Hold("PROCESS_RESTART_MISMATCH")
        report["comparisons"].append("app-restart")
        capacity(database, [])
        backup(config, "pre-deploy")
        archive = backup(config, "nightly")
        started = time.monotonic()
        restored = restore(config, archive, root / "fresh-restored")
        if fingerprint(restored) != baseline or pages(restored) != http:
            raise Hold("RESTORE_RECONCILIATION_MISMATCH")
        report["comparisons"].append("decrypt-restore")
        for lineage, path, kind in sources:
            InventoryImports(str(restored)).run(lineage, str(path), kind=kind)
        if fingerprint(restored) != baseline or reconcile(restored, sources) != reconcile(
            database, sources
        ):
            raise Hold("RESTORED_RERUN_MISMATCH")
        report["comparisons"].append("restored-rerun")
        elapsed = time.monotonic() - started
        if elapsed >= 4 * 3600:
            raise Hold("SYNTHETIC_RTO_EXCEEDED")
        report["restore"] = {
            "seconds": elapsed,
            "state_sha256": fingerprint(restored),
            "scope": "same-host-synthetic-only-not-operational-RTO",
        }
        report["capacity"] = {"limit_bytes": LIMIT, "database_bytes": database.stat().st_size}
        report["result"] = "synthetic-pass"
    finally:
        # No exception strings or payload in stdout; detailed report stays private.
        atomic(root / "report.json", json.dumps(report, indent=2).encode())
    return {
        k: report[k]
        for k in ("result", "real_rehearsal_passed", "g4", "g5", "runtime", "image_pin")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    pre = sub.add_parser("preflight-real")
    pre.add_argument("--plan", type=Path, required=True)
    run = sub.add_parser("synthetic")
    run.add_argument("--private-run", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "preflight-real":
            preflight_real(json.loads(args.plan.read_text()))
        else:
            print(json.dumps(synthetic(args.private_run), sort_keys=True))
        return 0
    except Hold as error:
        print("Rehearsal held: " + str(error), file=sys.stderr)
    except Exception:  # noqa: BLE001 — never leak private parser/storage diagnostics.
        print("Rehearsal failed; inspect private report with host administrator", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
