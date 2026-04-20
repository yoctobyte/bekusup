# Sis AI Handoff

## Product Decision

If the user inserts two enrolled backup drives, `bekusup run` should back up to both.

This is intentional product behavior, not an accident and not a temporary workaround.

## Why

The user may rotate only one disk at a time.

That means:

- one older local disk can remain inserted as a reference source
- one newer or freshly swapped disk can receive an updated backup
- the local on-machine copy can be used to reduce network transfer dramatically on slow links

So dual-drive runs are a feature:

- keep both drives current
- let one drive act as a local cache source for the other
- preserve the operator-friendly rotation workflow

## Required Behavior

When `bekusup run` sees two enrolled, trusted backup disks:

1. It should process both disks in the same run.
2. It should run them in a deterministic order.
3. It should prefer using the more up-to-date local disk as the snapshot/cache source for the less up-to-date disk when that reduces network traffic.
4. It should still keep each disk as an independent, self-contained snapshot history.

This means:

- same-disk reuse should use hardlinks when supported
- cross-disk reuse may use local copy-seeding such as `rsync --copy-dest`
- no disk should depend on the continued presence of the other after the run finishes

## Constraints

- Only enrolled and trusted disks participate.
- Labels are discovery hints, not authorization.
- Internal/system disks must still be rejected conservatively.
- Host failures must not abort the whole run.
- Overlapping invocations must still fail safely.
- The backup index must record enough host/session/drive outcome data to answer:
  - which drives contain successful backups for host X
  - when host X was last backed up
  - which drive was most recently successful for host X

## Non-Goals For This Round

- Do not collapse back to single-disk-only behavior.
- Do not remove dual-drive optimization.
- Do not build a per-file global catalog.
- Do not depend on cloud or off-site media behavior yet.

## What Needs To Be Tightened Next

The next implementation pass should focus on:

1. Making multi-disk run policy explicit and well-tested.
2. Storing host outcomes in the local backup index, not only in session manifests.
3. Distinguishing `succeeded`, `partial`, `failed`, and `unreachable`.
4. Hardening disk enrollment and trust reconciliation.
5. Making operator-visible logs clearly show:
   - which disks were selected
   - which disk was used as local reuse source
   - which hosts succeeded or failed on each disk

## Bottom Line

Two inserted enrolled backup disks should mean:

- back up both
- use the better one to accelerate the other
- keep each disk independently useful afterward
