# Spec #1 implementation checkpoint

Acceptance source: https://github.com/mclbautista/t6-drives/issues/1 and native children #2–#10. This records code delivery and verification, not operational acceptance.

## Integration

All application work is on `feat/spec-1-drive-app` in draft PR #11. The previously empty default branch was initialized with private-state ignore rules only (`a0e6df5`). No application feature or unapproved agent configuration was committed directly to main.

## Delivered code

| Ticket | Integrated capability | Remaining acceptance boundary |
| --- | --- | --- |
| #2 | Durable inventory, identification/serial edits, optimistic concurrency, browser validation | Operational use not activated |
| #3 | Offline Project cache, host-side refresh, Assignment history and explicit ending | Live refresh configuration remains operational |
| #4 | Append-only custody, correcting events, append-order status | Actor names are self-declared, not authenticated |
| #5 | Atomic batch release and immutable printable historical snapshots | No production shipments recorded |
| #6 | Durable inventory import, atomic receipts and private exceptions | Real inputs not imported |
| #7 | Combined movement import, reconstructed snapshots and read-only exceptions | Real source reconciliation not performed |
| #8 | Pinned hardened container, persistent volume, migrations/rollback checks | Actual office host/network access unverified |
| #9 | Encrypted complete-state backup/restore, retention and failure gates | No off-host destination, key custodian, restore operator or installed schedule |
| #10 | Synthetic rehearsal/reconciliation and fail-closed real preflight | Real-data rehearsal remains blocked; ticket is incomplete |

Every integrated slice received independent review; reproduced findings were corrected before integration. The latest preparation review passed without actionable findings. This does not replace eventual whole-spec acceptance or operational verification.

## Executed verification

At application head `511c7138993a7a93de210fa9f8626db9ca437f6b`, the coordinator reran 74 tests, including Chromium scenarios, strict configured mypy across 30 files and Ruff: all passed. Both GitHub CI runs passed at that exact head, including mandatory real Docker build and synthetic named-volume restart/replacement, encrypted recovery and rollback smoke checks.

The local Docker VM is full. Local container retries failed on storage exhaustion; no unrelated images/volumes were pruned or resized. Successful GitHub container checks are independent positive evidence, not a claim that the local storage issue is fixed.

The approved primary testing seam is HTTP/UI against real temporary SQLite and pinned core, substituting the external Project reader only. All committed tests use invented data. Synthetic recovery timing and CI volumes do not prove off-host operational RPO/RTO.

## Owner decisions and holds

- Rehearsal target: this Mac, loopback-only, isolated database. This is not selection or verification of the production office host/network.
- No accounts. Permitted office users may perform operational writes; custody/shipment actors are required self-declared names, explicitly unauthenticated. Imports, cache refresh, backup/restore and deployment remain host-side administration, not browser roles.
- Off-host destination/key custody/restore operator: none configured for now. This is not a waiver of the recovery gate.
- G1 remains open for production host, allowed office sources and effective routing/bindings.
- G2/G3 policy choices are accepted; actual office-boundary deployment is not verified.
- G4 remains open. Real rehearsal preflight holds before source/database access, and supplying metadata alone cannot authorize execution. Existing host-admin APIs are not an OS sandbox and must not bypass the hold.
- G5 remains open for separately approved production activation and workbook freeze.

No real input files, Project exports or production databases were accessed by this implementation. No office deployment, production import or workbook freeze occurred. AC16 and conditional operational AC17 are not complete. Parent #1, operational core #55 and operational child criteria must not be closed or checked merely because code and synthetic CI are green.

## Repository setup

The skill-setup draft remains separate from implementation. No unapproved AGENTS.md/CLAUDE.md configuration is included.
