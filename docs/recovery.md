# Ticket #9: encrypted recovery candidate (synthetic only)

**G1–G5 remain unresolved; AC15 is not accepted.** This implements recovery
commands and a runnable scheduling template, not an installed backup service.
The destination, key custody and restore operator must be approved under G4.
No off-host provider, real data, cron installation or production activation is
selected/authorized here. A same-host directory or Docker volume is not off-host.

## Configuration and boundary

Run `python -m backups --config /absolute/private/recovery.json ...` using the
pinned release environment. Example configuration (replace every synthetic path
and the image ID with the approved values; paths refer to the executing host or
container namespace):

```json
{
  "database": "/private/synthetic/drives.sqlite",
  "destination": "/private/approved-mounted-destination/drives",
  "key_file": "/private/separate-key-custody/recovery.key",
  "release": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
}
```

The destination must already exist, be owned by the executing UID and mode 0700;
the existing regular key file must be owned by that UID and mode 0600 (or 0400).
Do not run as root to bypass permissions. Generate a Fernet key through the
approved private provisioning channel, never stdout/chat/argv or git. Keep key
custody independent of the backup store; preserve old keys/releases for older
recovery points. Protect configuration and parent directories from other writers.
Never publish resolved environments, keys, evidence, database files or manifests.
The application release identifier is the immutable Docker image ID; obtain it
with `docker image inspect --format '{{.Id}}' "$DRIVES_IMAGE"`. Restore requires
that recorded image and pinned core commit, not an arbitrary newer release.

All durable state currently resides in SQLite, including raw workbook BLOBs,
exceptions, receipts, cache, saved manifests, permanent IDs and ordered history.
The online SQLite backup API includes committed WAL state and excludes incomplete
row transactions. Imports are row-atomic, not whole-workbook-atomic. Private
snapshot temporaries are removed after use. The authenticated Fernet envelope
contains a single database plus schema/core/release/digest/reason/time metadata;
there is no tar extraction, path traversal, archive-link or decompression input.
Restore accepts a bounded regular non-symlink, non-hardlinked artifact and only a
new absolute target directory. It never overwrites an existing store.

This deliberately non-streaming implementation rejects snapshots over **32 MiB**
and input ciphertext over **64 MiB**. Fernet/JSON/base64 create multiple in-memory
copies; provision at least 512 MiB spare RAM and private snapshot disk for this
bounded candidate. Larger real-data recovery requires a separately reviewed
streaming design and capacity rehearsal, not raising the limit blindly. The
64 MiB container /tmp must also fit the consistent snapshot. Failure is nonzero;
no successful recovery claim is made for inputs exceeding the bound.

## Nightly template and failure visibility

After approval, an administrator can adapt this cron template to the pinned
interpreter/module directory and approved config. **It is not installed here.**
Use UTC scheduling (scheduler-specific timezone configuration required). Missing
mounts must fail closed: configure the approved off-host mount/transport so its
absence cannot silently fall back to a local directory. A directory path alone
cannot prove off-host durability, remote acknowledgements or mount identity.

```cron
# UTC, example only: interpreter and cwd must refer to the pinned release.
0 1 * * * cd /private/pinned-release && /private/pinned-release/.venv/bin/python -m backups --config /private/recovery.json backup --reason nightly
0 * * * * cd /private/pinned-release && /private/pinned-release/.venv/bin/python -m backups --config /private/recovery.json status
```

Route scheduler nonzero exit/stderr and missed-run alerts to the named operator
through an approved existing monitoring channel. Commands emit only sanitized
success/failure messages, not private diagnostics. Local `status.json` records
the latest operation; `nightly.json` separately records the last successful
nightly. `status` authenticates the referenced nightly artifact and fails for
missing, corrupt, future or older-than-24-hour nightly points and latest recorded
backup failure. Hooks cannot disguise stale nightly coverage. If configuration,
locking, storage or permissions prevent status writes, command exit/stderr and
external missed-run monitoring are mandatory. `/health` is not backup health.
Verify alerts by inducing a synthetic failure and withholding a scheduled run.

After a new recovery point is published atomically, fsynced and decrypted from
the exact published file, retention removes only authenticated, filename/metadata
matching points for this release/key older than the current UTC day plus the
previous 13 calendar days (14-day retention). Unrelated, corrupt, future and
other-release/key artifacts are not deleted. Old releases/keys therefore require
an explicitly approved separate lifecycle policy. Failed backup never expires
old good points. Remote retention/transport policy must preserve these guarantees.

## Pre-import and pre-deployment checkpoints

Set `DRIVES_BACKUP_CONFIG=/private/recovery.json` in every host-admin importer
process. Both workbook command paths call the backup before importing; any
configured backup failure aborts the import. Database path mismatch fails closed.
Unset means no checkpoint: this remains necessary for isolated tests, and is
**not** an operationally safe default. Container imports must explicitly mount
the config/key/destination in the same namespace and pass the variable; the
ordinary web Compose service intentionally receives no backup credentials.

For deployment, use the **old pinned release** and its database/config before
replacement. This command is real, returns nonzero on failure, and must gate
replacement rather than merely appear in a checklist:

```sh
set -eu
cd /private/pinned-release
.venv/bin/python -m backups --config /private/recovery.json backup --reason pre-deploy
# Only after success: replace using the separately approved deployment procedure.
```

Stop all writers before an incompatible schema deployment and keep them stopped
through checkpoint/replacement if a complete pre-change boundary is required.
No deployment script, system config or live service is changed by this ticket.

## Fresh-target restore and rollback

1. Isolate the recovery host/store and stop every writer before promotion. Retrieve
   the approved off-host artifact, its recorded image/core release and key through
   approved custody; do not use logs or public artifacts as transport.
2. In that pinned environment, run (target must not already exist):

   ```sh
   time .venv/bin/python -m backups --config /private/recovery.json restore \
     --archive /private/retrieved/drives-v1-nightly-RECORDED.fernet \
     --target /private/new-isolated-recovery
   ```

   Use the actual recorded artifact filename, not this placeholder. The command
   verifies authenticated metadata/digest, SQLite integrity/foreign keys/schema
   and application readiness. It writes mode-0600 `drives.sqlite` inside the new
   mode-0700 directory. Failed targets are retained privately, never overwritten.
3. Start the same pinned application against the new database on an isolated
   loopback boundary with a private runtime session secret. Compare inventory,
   Assignment/custody order, saved shipment/print pages, exceptions and cache to
   the private baseline. Verify complete logical state including raw sources and
   receipts; rerun unchanged synthetic inputs at stable paths and verify no new
   rows/events/receipts. Never lower `user_version` to force a downgrade.
4. For schema rollback, retain the incompatible store unchanged for diagnosis;
   after checks, explicitly repoint the stopped app to the restored fresh store.
   Never copy over an active SQLite file or ignore WAL. Promotion is a separate
   approved administrator operation, not an automatic effect of restore.
5. Record sanitized elapsed time including retrieval, key access, pinned-runtime
   startup and comparisons. Target RPO 24 hours and RTO 4 hours require actual
   off-host/scheduler and full-size restore measurements, not just a local test.

## Automated evidence and remaining verification

`tests/test_backups.py` uses only generated workbooks and temporary SQLite. It
checks HTTP equality, complete logical dump, unchanged receipts on rerun, schema
rollback, WAL transaction consistency, encryption/wrong-key/corruption/version,
permissions/links/size/overwrite refusal, retention, fail-closed import and
fresh authenticated nightly status. The mandatory CI container smoke now also
uses encrypted backup and fresh-target restore under the nonroot pinned runtime,
compares the complete SQLite digest and HTTP before/after schema failure, and
removes only its generated disposable project/volume. It does not prove off-host
storage, independent key custody or an installed schedule. Local Docker disk
exhaustion blocks container verification; do not prune unrelated resources.

G4 still requires named destination/key custodian/operator, off-host availability
and host-loss rehearsal, installed nightly/monitoring checks, size/capacity
validation, 14-day observed retention and a timed full restore. G1–G3 and G5 remain
independently required. Nothing here authorizes real-workbook rehearsal or cutover.
