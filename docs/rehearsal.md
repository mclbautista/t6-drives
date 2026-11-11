# Ticket #10 — gated rehearsal preparation (not completion)

## Approved choices and holds

- The isolated real-data rehearsal target is **this Mac, loopback only**, with a
  dedicated rehearsal database. This is not approval of a production host or
  office/LAN/general-tailnet routing. No network/firewall changes are made.
- G2/G3: permitted office users may make equal operational writes without
  accounts. New operational custody events and shipment releases require a
  **self-declared actor**, explicitly **not authenticated**. No application roles
  are enforced. Imports, Project-cache refresh, backup/restore and deployment
  remain host-administrator commands, never browser upload/admin routes.
- **G4 is not configured.** The destination, key custody and restore operator
  answer is “none for now”; that is not a waiver. No real workbook, Project
  export or real database may be read/imported for this work. No actual off-host
  endpoint or key has been provisioned. No scheduler is installed.
- G1 remains open for the production office host/boundary. **G5 remains a separate
  explicit approval for the exact production import/cutover and workbook freeze.**
  This tooling cannot perform production activation, freeze or promotion.

Ticket #10/AC16 is **blocked, not passed**. Synthetic success does not satisfy
AC15/G4, real rehearsal, production preservation/recovery, parent #1 or core #55.
The existing [deployment](deployment.md) and [recovery](recovery.md) procedures
remain candidates, with the approved G2/G3 update above taking precedence over
older “all gates unresolved” wording.

## Commands (host only, no real input execution path)

Use the pinned Python 3.12 environment from the reviewed source checkout:

```sh
python -m rehearsal preflight-real --plan /absolute/private/rehearsal-plan.json
# exits 1: Rehearsal held: G4_NOT_CONFIGURED
```

The private plan is metadata only. Its G4 fields are `offhost_destination`,
`key_custodian` and `restore_operator`; retain null while unconfigured. Do not
put keys in JSON. Other metadata such as input paths is never dereferenced by
this preflight. It rejects missing G4 facts **before input/database/key/destination
stat/read/connect**, covered by synthetic tripwires. Even filling every field
cannot enable real execution: independent off-host availability, key retrieval,
scheduled freshness/retention, capacity and full timed restore must first be
verified and reviewed. It then returns
`REAL_EXECUTION_DISABLED_PENDING_INDEPENDENT_G4_VERIFICATION`.
There is intentionally no `run-real`, approval/waiver flag, freeze or cutover flag.
Do not bypass this hold with the underlying generic import/backup APIs.

For preparation only:

```sh
# PRIVATE_RUN must be a new absolute directory under an existing, resolved,
# private host directory OUTSIDE every git checkout. It must not already exist.
# Use a durable private state directory, not temporary/scratch storage for
# evidence you intend to retain. Do not set DRIVES_BACKUP_CONFIG for this command.
python -m rehearsal synthetic --private-run "$PRIVATE_RUN"
```

The command takes **no workbook, export, existing database, destination or key
argument**. It exclusively creates its own synthetic inputs and isolated store.
It refuses existing paths, symlinked/noncanonical parents, git-contained evidence
and inherited external backup configuration. Run roots are mode 0700 and the
report/source/key/recovery artifacts are mode 0600. Retain failed roots privately;
rerun into a new root rather than deleting or reusing evidence silently.
Only allowlisted status goes to stdout. Never publish `report.json`, database,
source files, key, manifests or raw exceptions, even for this synthetic run.

## What the private report verifies

- Both generated workbook import kinds use the actual durable SQLite adapter and
  pinned core. A before-row-commit interruption is retried from retained failed
  lineage/evidence, with no silently committed partial row/receipt.
- Each nonempty source row is independently enumerated and matched exactly to
  its durable evidence, original source BLOB/checksum and stable absolute path.
  Usable rows must reference an existing permanent Drive through a receipt;
  exception rows must retain reasons/evidence; structural rows remain accounted
  for. Pending/missing/extra coverage is a hard hold, not “pass with omissions”.
- Unsafe/unattached movements and an unreadable synthetic workbook are retained.
  The unreadable file is separately reported as a source-level quarantine with
  its complete BLOB and failed status, **not** as enumerable/usable rows.
- Retry, unchanged reruns, a fresh-interpreter app restart, authenticated decrypt
  and fresh-target restore, and a restored-store rerun compare complete logical
  SQLite state. This includes permanent IDs/facts, Assignment and append-ordered
  custody history, legitimate repeated events, shipment membership/snapshots,
  receipts, cache, retained source BLOBs, exceptions and failed quarantine.
- HTTP inventory/detail/history, exception and shipment/print HTML digests are
  compared (only per-session CSRF tokens excluded). This is local app HTTP-client
  and process-rehydration evidence, **not** a new container or live routing check.
- Private lineage includes source checksums, app commit/source-tree fingerprint
  and pinned core. Local runtime records `image_pin: null`; it never invents an
  image digest. The backup API's synthetic release label is the source-tree hash,
  explicitly **not an immutable deployment image identity**. Container verification
  remains the separate pinned-image smoke in the deployment runbook.
- Capacity is checked before import against the **32 MiB recovery envelope**
  using a conservative source-BLOB estimate, then against actual store size.
  Require 512 MiB free snapshot disk; the documented spare-memory requirement
  still applies. Oversize/insufficient scratch capacity fails closed. Local
  decrypt/restore/comparison elapsed time is measured against four hours; it
  excludes remote retrieval/key access and is **not operational RTO evidence**.

Same-host synthetic encrypted backups test recovery mechanics only. They do not
prove off-host survival, installed nightly copies, 24-hour RPO or observed 14-day
retention. A failed check is nonzero; incomplete reports never say rehearsal passed.

## Actor configuration

`runtime:application()` defaults `DRIVES_ACTOR_POLICY` to
`self-declared-required`; `python app.py` uses the same default. Server-side
validation on single custody and batch-release POSTs rejects absent/blank actors,
more than 120 trimmed characters and nonprintable/control characters, without
partial event/shipment writes. Actor text is escaped and saved with the explicit
`self-declared (not authenticated)` mode in events/snapshots, never as a forged
Notion account/page reference. Unknown imported actors remain unknown imported
actors; the policy does not rewrite history or claim an authenticated audit log
for ordinary fact/Assignment edits.

The `create_app` factory's historical `unknown` default and optional
`self-declared-synthetic` policy remain for isolated synthetic compatibility.
Operational launchers must use `self-declared-required`. Unknown/synthetic policies
are not accepted operational attribution. No role-specific permissions are implied.

## Candidate verification (synthetic only)

- Full unittest discovery: **74 tests passed**, including Chromium navigation/print,
  both generated workbook kinds, injected failure/retry, fresh-process restart,
  encrypted restore/restored rerun and no-input-touch G4 tripwires.
- Required-actor HTTP regression first reproduced the Unicode line-separator
  failure on both custody and shipment POSTs. Validation now rejects Unicode
  line/paragraph separators as well as controls; blank/oversize/escaped actor
  persistence and rejection atomicity pass.
- Mypy: **30 source files passed**. Ruff check/format, compileall, Python sdist and
  wheel build, `uv lock --check` and locked-environment dependency check passed.
- Container build **blocked**, not passed: the pinned build stage's apt metadata
  verification failed; an independent no-network disposable base-container disk
  check reported **zero available blocks / 100% used**. No signature bypass,
  storage substitution or Docker pruning was attempted. Named-volume container
  smoke remains unverified on this candidate; CI still requires it.
- Docker context remains an explicit runtime allowlist. `rehearsal.py` is packaged
  for reviewed host-shell use but deliberately excluded from the web image; no
  administrative browser route or real-input execution command was added.

## Later cutover evidence checklist — intentionally open

- [ ] Name and verify approved production host, permitted/denied office/public/
      general-tailnet vantage points, effective bindings and routing (G1).
- [ ] Provision and independently verify the approved off-host destination,
      independent key custody and named restore operator (G4).
- [ ] Verify installed nightly/failure monitoring, 24-hour freshness, 14-day
      retention, pre-import/pre-deploy checkpoints, full-size bounded capacity
      and complete decrypt/restore within the four-hour operational RTO.
- [ ] Enable real rehearsal only after reviewed recovery/isolation prerequisites;
      privately reconcile both authorized real inputs, exceptions and interrupted
      retries, stable paths/IDs, histories, snapshots, cache, restart and restore.
- [ ] Obtain separate explicit G5 authority for the exact production import and
      workbook freeze. Until then original workbooks remain unchanged/unfrozen;
      no production import or source-of-truth activation is executed here.
- [ ] After separately authorized production import, verify actual durable-store
      preservation/history/private exceptions and complete recovery. Only that
      later evidence can satisfy conditional AC17/core #55 operational acceptance.

No acceptance boxes, GitHub issues, infrastructure or other worktrees are changed
by the preparation commands.
