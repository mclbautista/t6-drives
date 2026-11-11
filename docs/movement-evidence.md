# Movement import verification

Synthetic inputs only. This slice adds host-side log-sheet import, durable individually traceable exceptions and immutable shipment reconstruction. It does not establish operational deployment or production cutover acceptance.

## Executed checks

- Full unittest discovery: 59 tests passed, including four Chromium scenarios.
- Mypy: no issues in 21 source files.
- Ruff lint and formatting: passed.
- Compileall: passed.
- Source distribution and wheel build: passed; wheel includes all seven application modules, including imported shipment reconstruction.
- Diff whitespace validation: passed.

Coverage includes repeated movements/manifest lines, entrypoint locking, membership schema migration, source evidence and provenance, assembly/failure recovery, completed-lineage reconciliation stability after serial corrections, concurrent import/serial mutation, escaped read-only exception views and historical rendering after restart and current-fact/Project changes.

Private real-data reconciliation, independent review, deployment isolation and recovery gates remain separate. Real workbook and Project exports were not used or included in these checks.

## Ticket #7 independent-review regressions

`tests/test_import_review_regressions.py` adds four synthetic scenarios, exercised
first as focused discovery and then in the full suite:

- Pause the actual pinned importer immediately after its monthly ambiguity guard,
  complete a CSRF/revision-guarded HTTP serial correction, then resume. The row
  becomes an individually traceable `ambiguous serial` exception: no replacement
  accepted Drive, Assignment, event, snapshot, membership or successful receipt.
- Reverse the ordering at the SQLite row-commit boundary. The original Drive
  receives the event; the later HTTP edit cannot change its source-recorded
  shipment serial. Restart and unchanged rerun preserve the complete state.
- Inject a failure in a later safe movement after the unsafe rejection commits.
  The failing row rolls back; all source rows remain durable. Even after the
  suspected candidate is removed, retry retains the earlier rejection and imports
  only the safe movement, without duplicate events, receipts or snapshots.
- Import a log with an unattached `PROJECT NAME:` row before its header. It remains
  searchable in the exception UI after restart/rerun. Actual log date/header/group
  control rows (including offset columns and serial-column aliases) are recognized
  without introducing exception noise. Per-project regression coverage is retained.

The two primary regressions were observed failing before their production fixes.
The adapter revalidates monthly suspected-serial safety inside `BEGIN IMMEDIATE`,
before accepted association or creation. A separate durable rejection key preserves
unsafe decisions across failed runs without fabricating a Drive receipt. Ordinary
inventory association semantics remain unchanged; no workbook-wide write lock is
held. Classification uses the pinned reader's movement rows and its column/date
grammar, with an explicit compatibility seam because core exposes no public
structural-row API; recheck that seam when changing the core pin.
