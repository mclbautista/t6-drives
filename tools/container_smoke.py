"""Isolated Docker smoke: synthetic inputs only, unique disposable project/volume.

Run: python tools/container_smoke.py sha256:<built image ID>
Never accepts a mutable tag, an existing volume, a host or a network override.
"""

import http.cookiejar
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(*args: str, source: str | None = None, env=None) -> str:
    result = subprocess.run(
        args, input=source, text=True, capture_output=True, cwd=ROOT, env=env, check=False
    )
    if result.returncode:
        # Do not print command environments, private logs or response payloads.
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode}): {result.stderr}")
    return result.stdout.strip()


def smoke(image: str) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Use an immutable local image ID or registry digest, not a tag.")
    project = "drives-smoke-" + secrets.token_hex(6)
    env = dict(
        os.environ, DRIVES_IMAGE=image, DRIVES_PORT="0", DRIVES_SESSION_SECRET=secrets.token_hex(32)
    )
    compose = ("docker", "compose", "-p", project)
    volume = project + "_drives_data"
    release = json.loads(command("docker", "image", "inspect", image))[0]["Id"]

    def dc(*args):
        return command(*compose, *args, env=env)

    def offline(source):
        return command(
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--mount",
            f"type=volume,source={volume},target=/data",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
            "--entrypoint",
            "python",
            image,
            "-",
            source=source,
        )

    def target():
        container = dc("ps", "-q", "app")
        data = json.loads(command("docker", "inspect", container))[0]
        bindings = data["NetworkSettings"]["Ports"]["8080/tcp"]
        assert len(bindings) == 1 and bindings[0]["HostIp"] == "127.0.0.1"
        assert data["Config"]["User"] == "10001:10001"
        assert data["HostConfig"]["ReadonlyRootfs"]
        assert data["HostConfig"]["CapDrop"] == ["ALL"]
        assert any(
            m["Name"] == volume and m["Destination"] == "/data"
            for m in data["Mounts"]
            if m["Type"] == "volume"
        )
        expected = json.loads(command("docker", "image", "inspect", image))[0]["Id"]
        assert data["Image"] == expected
        url = "http://127.0.0.1:" + bindings[0]["HostPort"]
        for _ in range(100):
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as response:
                    assert response.status == 204 and response.read() == b""
                return url
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        raise RuntimeError("Synthetic container did not become ready")

    try:
        # Create the dedicated volume through Compose; never attach an existing store.
        dc("create")
        offline("""
import sqlite3
from app import encode
from t6.drives import Drive
with sqlite3.connect('/data/drives.sqlite') as db:
    db.execute('CREATE TABLE drives(id TEXT PRIMARY KEY, serial TEXT UNIQUE, facts TEXT NOT NULL)')
    d = Drive(label='Synthetic version zero')
    db.execute('INSERT INTO drives VALUES(?,?,?)', (d.id, d.serial, encode(d)))
""")
        dc("up", "-d")
        url = target()
        assert b"Synthetic version zero" in urllib.request.urlopen(url).read()
        print(
            "PASS version-0 migration + loopback binding + nonroot/read-only/cap-drop + pinned image"
        )
        # Seed synthetic imported raw bytes, Assignments/events, snapshots, receipts,
        # exceptions and cache using existing host-only import/cache interfaces.
        offline("""
from pathlib import Path
from openpyxl import Workbook
from app import create_app
from imports import InventoryImports
from assignments import Assignments
class Reader:
    def read(self):
        return {'complete': True, 'projects': [{'t6_id': 't6p_'+'1'*32, 'name': 'Synthetic cached project', 'active_page_id': '2'*32, 'legacy_page_ids': []}]}
Assignments('/data/drives.sqlite').refresh(Reader())
b = Workbook()
s = b.active
s.title = 'Synthetic batch'
s.append(['DATE: Jan 2, 2025'])
s.append(['HARD DRIVE LABEL','SERIAL NUMBER','SPACE','T6 IN'])
s.append(['Synthetic imported','SYN-DEPLOY','4TB','YES'])
m = b.create_sheet('Synthetic month')
m.append(['HARD DRIVE LABEL','DATE','IN/OUT','RECEIVED BY','REMARKS'])
m.append(['Synthetic imported','Jan 3, 2025','OUT','Synthetic recipient','S/N: SYN-DEPLOY'])
m.append(['Synthetic exception',None,'OUT',None,'unattached synthetic evidence'])
p = Path('/tmp/synthetic-log.xlsx')
b.save(p)
b.close()
a = create_app('/data/drives.sqlite')
r = a.test_cli_runner().invoke(args=['import-log-sheet','--lineage','synthetic','--workbook',str(p)])
assert r.exit_code == 0
""")
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        form = browser.open(url + "/new").read().decode()
        token = re.search(r'name="csrf" value="([^"]+)"', form)
        assert token
        request = urllib.request.Request(
            url + "/new",
            data=urllib.parse.urlencode(
                {"csrf": token[1], "label": "Synthetic HTTP persisted"}
            ).encode(),
            headers={"Origin": url},
        )
        detail = browser.open(request).geturl().removeprefix(url)
        pages = ["/", detail, "/shipments", "/exceptions"]
        listing = browser.open(url + "/shipments").read().decode()
        pages += sorted(set(re.findall(r'href="(/shipments/[^"?]+)"', listing)))
        pages = [p for p in pages if p != "/shipments/new"]
        before = {p: browser.open(url + p).read() for p in pages}
        assert b"Synthetic exception" in before["/exceptions"]
        # Complete logical state digest includes raw BLOB bytes, ordered rows and schema.
        snapshot = """
import hashlib, sqlite3
with sqlite3.connect('/data/drives.sqlite') as db:
    dump = '\\n'.join(db.iterdump()).encode()
    assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert db.execute('PRAGMA user_version').fetchone() == (1,)
    for table in ['assignments','custody_events','shipment_members','shipments','import_receipts','import_sources','import_evidence','project_cache']:
        assert db.execute('SELECT count(*) FROM '+table).fetchone()[0] > 0
    print(hashlib.sha256(dump).hexdigest())
"""
        digest = offline(snapshot)
        for action in (("restart", "app"), ("up", "-d", "--force-recreate", "app")):
            dc(*action)
            url = target()
            assert {p: browser.open(url + p).read() for p in pages} == before
            assert offline(snapshot) == digest
        print("PASS HTTP + complete SQLite state unchanged across restart and replacement")
        dc("stop", "app")
        offline(
            """
import json, sqlite3, time
from pathlib import Path
from cryptography.fernet import Fernet
from backups import BackupConfig, backup, restore
root = Path('/data/synthetic-backups')
root.mkdir(mode=0o700)
key = root / 'key'
key.write_bytes(Fernet.generate_key())
key.chmod(0o600)
config = BackupConfig(Path('/data/drives.sqlite'), root, key, RELEASE)
archive = backup(config, 'nightly')
backup(config, 'pre-deploy')
assert b'SQLite format' not in archive.read_bytes()
with sqlite3.connect('/data/drives.sqlite') as db:
    db.execute('PRAGMA user_version=999')
    db.execute('DROP TABLE drives')
try:
    from app import create_app
    create_app('/data/drives.sqlite')
except RuntimeError:
    pass
else:
    raise AssertionError('future schema accepted')
# Fresh target refuses overwrite; validate the complete logical state before promotion.
started = time.monotonic()
fresh = restore(config, archive, Path('/data/fresh-recovery'))
with sqlite3.connect(fresh) as recovery, sqlite3.connect('/data/drives.sqlite') as destination:
    recovery.backup(destination)
assert time.monotonic() - started < 4 * 3600
print('PASS synthetic encrypted fresh-target restore within 4h; off-host NOT verified')
""".replace("RELEASE", repr(release))
        )
        dc("up", "-d")
        url = target()
        assert offline(snapshot) == digest
        assert {p: browser.open(url + p).read() for p in pages} == before
        print("PASS incompatible schema refused + encrypted fresh-target rollback + HTTP recovery")
        # No administrative web route, no payload health, no cross-origin mutation.
        for path in ("/import", "/refresh-projects", "/restore", "/deploy"):
            try:
                browser.open(url + path)
                raise AssertionError("Administrative route exposed")
            except urllib.error.HTTPError as error:
                assert error.code == 404
        try:
            browser.open(
                urllib.request.Request(
                    url + "/new",
                    data=b"label=Synthetic-rejected",
                    headers={"Origin": "http://example.invalid"},
                )
            )
            raise AssertionError("Cross-origin mutation accepted")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        logs = dc("logs", "--no-color", "app")
        assert "Synthetic" not in logs and "SYN-DEPLOY" not in logs
        print("PASS admin-route/CSRF/diagnostic safety; G1-G3/AC14 NOT verified")
    finally:
        # Destructive cleanup is scoped exclusively to this generated synthetic project.
        dc("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tools/container_smoke.py <immutable-image>")
    smoke(sys.argv[1])
