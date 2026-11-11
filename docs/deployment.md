# Ticket #8: synthetic packaging and operator procedure

**Not operational approval. G1–G5 remain unresolved.** No selected real host,
allowed office sources, accepted actor convention/equal-write policy, approved
recovery destination/key custodian/operator, or production activation authority
is implied. No accounts or authenticated application roles exist. Current actor
policy is unknown; imported unknown attribution stays unknown. This document is
for newly generated synthetic data only. Never use the netops public host,
public proxy, general tailnet or a shared queue/registry database by inference.

## Build and isolated check

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock -r requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy
.venv/bin/python -m build
docker build --iidfile "$TMPDIR/drives-ticket8-image.id" .
.venv/bin/python tools/container_smoke.py "$(cat "$TMPDIR/drives-ticket8-image.id")"
```

Use only a private scratch directory for the image ID file; it contains no data.
The smoke generates a unique Compose project and dedicated disposable named
volume, synthetic workbooks/cache/evidence, and an unpredictable runtime secret.
It verifies loopback binding, nonroot execution, read-only root, dropped caps,
immutable image identity, HTTP persistence and complete SQLite logical-state
checks across restart/replacement. The state includes permanent IDs/facts,
Assignments, ordered events, shipment members/snapshots, receipts, original
source BLOBs, private exception evidence and Project cache. It also checks
integrity, supported migration, future-schema refusal, backup-based rollback,
health privacy, CSRF, administrative-route absence and sanitized logs.

The smoke removes **only its generated project/volume** in its finalizer.
Never run a system prune or delete unrelated images/volumes to make it pass.
If disk exhaustion prevents SQLite writes, report the check blocked, not passed.
A host bind mount or host-backed volume uses different storage and cannot prove
the default named-volume acceptance. Do not silently substitute one in CI.

## Manual synthetic start/replace

```sh
export DRIVES_IMAGE="$(cat "$TMPDIR/drives-ticket8-image.id")"
export DRIVES_SESSION_SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export DRIVES_PORT=5000
docker compose -p drives-synthetic config --quiet
docker compose -p drives-synthetic up -d
curl --fail -i http://127.0.0.1:5000/health
docker compose -p drives-synthetic restart app
docker compose -p drives-synthetic up -d --force-recreate app
# Stop/remove the container, but preserve the data volume:
docker compose -p drives-synthetic down
unset DRIVES_SESSION_SECRET
```

Retain the same private runtime secret across replacements; rotating it
invalidates form tokens. Never commit secrets, pass them as build arguments,
print resolved Compose environment, or publish inspect output. The secret is
visible to host/Docker administrators; this is not role-based access. Root
filesystem is read-only; /tmp is disposable tmpfs and /data is the dedicated
persistent store. Import sources/evidence are stored in SQLite, not /tmp.
Gunicorn is the runtime server, not Flask's development server. Health returns
only empty HTTP 204 for process/startup readiness, not backup/disk guarantees.
Only exact localhost/127.0.0.1 authorities are accepted; forwarded headers are
not trusted. The published port remains host loopback. Do not change it before
G1–G3 approval and independent allowed/denied-vantage testing.

## Schema and disposable rollback procedure

Version 0 is the **existing app's synthetic unversioned schema**, not an
invented legacy production database. Startup invokes the existing additive,
idempotent adapters and stamps version 1 only after initialization succeeds.
Fresh and repeated initialization are supported. There is no generalized
migration engine or claimed production migration history. A failed additive
initialization may leave completed additive steps; restart retries them. Future
versions are refused before adapter writes; downgrade is recovery-point restore,
not changing user_version to bypass compatibility checks.

For a disposable recovery rehearsal (automated in the smoke):

1. Stop every writer: `docker compose -p drives-synthetic stop app`. Ensure no
   host-admin import/cache command is running against the volume.
2. Run the pinned image with `--network none --read-only --cap-drop ALL`, the
   same dedicated data mount, and `--entrypoint python`. Use Python's SQLite
   backup API to copy `/data/drives.sqlite` to `/data/recovery.sqlite`:
   `with sqlite3.connect(source) as s, sqlite3.connect(recovery) as d: s.backup(d)`.
   Never blindly copy an active database while ignoring WAL files.
3. For **disposable synthetic rehearsal only**, alter the synthetic database to
   a future version and incompatible structure. Startup must refuse it. Do not
   attempt this fault injection on an operational store.
4. With all writers still stopped, use the same backup API in reverse from the
   compatible recovery point to the database. Do not merely lower user_version.
5. Start the original pinned release; verify integrity_check, foreign_key_check,
   version, complete logical state and HTTP pages including saved manifests and
   private exceptions against the pre-fault baseline. The smoke performs these
   comparisons and fails on a mismatch.

This same-volume recovery point tests schema rollback only. It is **not** an
encrypted off-host backup, scheduled recovery service, host-loss recovery proof,
RPO/RTO result or G4 acceptance. Ticket #9 owns that service; no operational
restore is authorized here.

## Boundary and privacy checks before any real deployment

G1: approve the actual host, office sources, routes and effective Docker
bindings/firewall behavior; demonstrate office reachability and rejected public,
non-office and general-tailnet access independently. Private DNS, tailnet
membership or a VPS alone does not prove authorization. Docker publication can
bypass host firewall assumptions. No firewall/routing changes are made here.

G2/G3: approve attribution and equal operational-write limits or replace the
proposal. Host-only import/cache refresh/restore/deployment are shell operations,
not unprotected browser endpoints. An allowed office user is not an authenticated
identity; append-only app interfaces do not prevent host-admin tampering.

G4: approve encrypted off-host destination, keys and responsible restore operator
and verify complete restore. G5: separately authorize exact production
activation/import and workbook freeze. These gates remain open even if synthetic
CI passes. Real inputs and private rehearsal remain out of this change.

Publish only synthetic pass/fail counts, immutable release identifiers and
sanitized blocker categories. Do not attach request bodies, raw logs, resolved
environments, database files, manifests, source paths or exception payloads.
