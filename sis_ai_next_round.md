# Sis AI Next Round

## Read First

Before making changes, read:

- [implementation_plan.md](/home/rene/bekusup/implementation_plan.md)
- [review_and_next_steps.md](/home/rene/bekusup/review_and_next_steps.md)
- [sis_ai_handoff.md](/home/rene/bekusup/sis_ai_handoff.md)
- [sis_ai_engineering_notes.md](/home/rene/bekusup/sis_ai_engineering_notes.md)
- [sis_ai_status_note.md](/home/rene/bekusup/sis_ai_status_note.md)

## Product Decision

This is settled:

- if two enrolled backup disks are present, `bekusup run` should back up both
- use the fresher local disk to accelerate the other when that reduces network traffic
- each disk must remain independently useful afterward

Do not revert to single-disk-only behavior.

## Goal

Close the gap between the current implementation and an operator-trustworthy MVP.

Do not widen scope beyond that.

## Priority Work

### 1. Upgrade the local backup index

The index must durably store per-session per-host outcomes.

It must support queries like:

- which drives contain successful backups for host X
- when host X last succeeded
- which drive most recently succeeded for host X

### 2. Make host outcome modeling explicit

Use clear host states:

- `succeeded`
- `partial`
- `failed`
- `unreachable`

### 3. Harden dual-disk execution policy

Make these explicit in code and tests:

- ordering
- reuse-source selection
- when cross-disk reuse is allowed
- what happens if disk A partially fails before disk B runs

### 4. Harden trust and identity reconciliation

Handle clearly:

- marker present / index absent
- index present / marker absent
- missing serial
- missing filesystem UUID
- identity mismatches

### 5. Improve operator-visible reporting

The operator should be able to see:

- selected disks
- trust/enrollment status
- chosen ordering
- reuse source
- per-host result per disk

### 6. Add tests

Add tests for the above behavior.

## Constraints

- Do not remove dual-disk behavior.
- Do not build a global per-file catalog.
- Do not add cloud or off-site features now.
- Keep runtime compatible with system Python without requiring a virtualenv.
- If tests need extra tooling beyond the standard library, document it clearly.
- Be conservative about wrong-disk writes and trust ambiguity. Safe refusal is better than guessing.

## Deliverables

When done, provide:

- code changes
- tests
- short summary of what changed
- short summary of what is still not done
- assumptions or unresolved edge cases
