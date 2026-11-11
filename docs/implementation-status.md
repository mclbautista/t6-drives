# Spec #1 implementation checkpoint

Acceptance source: https://github.com/mclbautista/t6-drives/issues/1 and its approved native children #2–#10. This document records delivery state, not acceptance approval.

## Bootstrap

The remote was empty and local `main` unborn. To make a reviewable integration PR possible, the default branch was initialized with only private-state `.gitignore` exclusions (commit `a0e6df5`). No application feature or unapproved agent configuration was committed directly to `main`.

All app work belongs on `feat/spec-1-drive-app`. Ticket implementation uses isolated worktrees/branches; integration retains one draft PR. No automatic merge or ready-for-review transition is authorized while acceptance remains incomplete.

## Task graph

- #2: first inventory/create/detail/edit slice; current frontier.
- #3 after #2: Project cache and Assignments.
- #4 after #2: append-only custody.
- #5 after #3 and #4: atomic shipment release and immutable print snapshots.
- #6 after #3: inventory import, durable receipts and exceptions.
- #7 after #5 and #6: combined movement import and reconstruction.
- #8 after #7: isolated deployment package and boundary verification.
- #9 after #8: complete encrypted off-host backup and restore.
- #10 after #7, #8 and #9: specifically authorized private real-data rehearsal, never cutover.

The primary approved test seam is observable HTTP/UI against real temporary SQLite and pinned t6-core. Only the external Project reader may be substituted. Synthetic tests are not operational isolation/recovery proof.

## Unresolved operational gates

- G1: actual host, permitted office sources, routing and effective port binding.
- G2: logged-by convention, imported/unknown actor treatment; no authenticated identity.
- G3: operational write access versus host-only administration; no enforced application roles.
- G4: authorized encrypted off-host destination, key custody and restore operator, independently verified restore.
- G5: separately approved exact production activation and workbook freeze.

No operational deployment, infrastructure modification, real-input access, production import or workbook freeze has been performed. Real-data rehearsal cannot start before the restricted boundary and recovery prerequisites are independently verified. Keep all real inputs, evidence, paths and identifiers outside git/GitHub. Parent #1 and core #55 must remain open; unchecked acceptance criteria are not evidence of completion.

Repository agent setup remains pending user decisions. No `AGENTS.md`, `CLAUDE.md` or fabricated setup approvals are included.
