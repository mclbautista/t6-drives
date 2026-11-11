# Inventory import candidate — ticket #6

This is the per-Project inventory slice only. No movement workbook import,
imported shipment snapshots or exception browser page is delivered here.
No production or real-data rehearsal was performed, and G1–G5 remain unchanged.

## Host-only use

From a restricted host shell with the app database configured as documented in
README, invoke Flask's `import-inventory --lineage <stable-run-id> --workbook
<absolute-private-input.xlsx>` command. The input must be a regular stable
absolute location, not a symlink. Keep the database and source directory inside
the approved private app-data boundary; database access is access to complete
original workbook bytes and row evidence. This command is not an authenticated
administrator role and has no HTTP upload counterpart.

A lineage binds the resolved original path and SHA-256 of the bytes captured at
start. Changed content, renamed/moved inputs and reusing identical bytes under
another lineage fail closed. There is no automatic reconciliation or workbook
synchronization tool: preserve the original and reconcile intentionally outside
this slice before registering replacement inputs.

The complete original workbook is journaled in SQLite before parsing. Every
nonblank row (including structure and unreadable/unattached rows) is journaled
before the first core mutation. Corrupt workbooks retain their original bytes
and failed status even if no rows can be decoded. A failed run leaves pending
rows for private inspection/retry; a completed run classifies them as structure,
usable or exception. `InventoryImports.evidence(lineage)` is a private Python
inspection boundary, not a log/report export. Never print its return value into
routine logs or public artifacts. `complete` means processing finished, not
operator preservation verification.

Core accepts a filesystem path, so each run creates an owner-only temporary
snapshot from exactly the same captured bytes used for hashing and evidence.
The importer never reopens the mutable original. Temporary core row keys are
translated to keys for the registered original path/sheet/row. Temporary paths
are not persisted in review items. Mid-run source replacement cannot substitute
unretained data, and a later retry of changed content is rejected. Temporary
snapshots are removed on ordinary exit; abrupt process/host termination may
leave private temporary files, so use a restricted temporary directory too.
The database and temporary filesystem must have sufficient space for the
complete workbook; this is not a streaming/size-limited upload endpoint.

## Durable adapter guarantees

`SQLiteDriveStore` implements the pinned core protocol against the app tables.
Row import takes `BEGIN IMMEDIATE`, checks the receipt, matches accepted serial
only, preserves existing physical facts, combines review notes, deduplicates
equal Assignments, appends every supplied event and commits its permanent-ID
receipt together. Conflicting observations are also recorded inside that
transaction, so the core's pre-transaction serial lookup is not the sole source
of conflict evidence. Serial edits use the same SQLite writer serialization.
Earlier exception reasons are retained across reruns and serial removal. No
source ownership evidence means unknown ownership, not an asserted T6 owner.

The cache adapter supplies only Projects with active page bindings. Exact
ambiguous names and page-less Projects remain unresolved. It does not write to
the registry, infer codes, refresh upstream or match Drives by Project/label.

Protocol shipment helpers are tested for core atomicity/order only. They do not
create app historical display snapshots and are not an operational batch-release
UI; ticket #5 owns that app behavior. Ticket #7 owns movement-workbook semantics
and the exception browser list. Private real-data rehearsal remains separately
authorized work; no real workbook names or contents are included here.

## Synthetic verification

`uv run python -m unittest discover -s tests -v` exercises HTTP with real temporary
SQLite, pinned core and synthetic XLSX inputs, including:

- CLI/list/search/detail, missing/annotated/shared serial uncertainty and unknown
  ownership; unresolved, resolved, ambiguous and page-less cached Projects;
- unreadable/unattached rows, original-byte retention and sanitized corrupt-input
  CLI errors (no source path, filename, lineage or payload echoed);
- event-write and pre-commit faults, rollback of facts/Assignments/events/receipt/
  observations, restart and exact retry; permanent receipt identity after serial
  correction/removal; unchanged exception reasons;
- parallel import/rerun and serial mutation; same captured bytes even when the
  original source is replaced after evidence capture;
- direct persistent protocol validation, duplicate serial/promotion rollback,
  accepted-only lookup, review/history independence, open Assignments, shipment
  ordering/deduplication/unknown-ID atomicity and append-order status.

The full suite includes real Chromium inventory, custody and shipment-print
navigation tests. All three use OS-selected loopback ports with test-only exact
Host/Origin translation; the production authority guard is unchanged. The combined
HTTP/SQLite regression imports an unknown-owner Drive with a cached Project,
releases it through shipments, verifies rerun evidence/event stability, and checks
that a cache rename and app restart leave the saved manifest unchanged.
The type, compile, Ruff and build gates include shipments and both import modules.
The built wheel includes all six application modules. Runtime and
dev export locks include `openpyxl` through the pinned core `drives` extra and
pinned `types-openpyxl` stubs; no missing-import blanket type suppression is used.
