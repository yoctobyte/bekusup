# Bekusup Review And What's Next

## Purpose

This document reviews what the current implementation produced by "sis AI" already achieves, where it diverges from the refined design, and what the next implementation pass should focus on.

It is intentionally not a bug-fix patch plan. The goal is to preserve momentum while making the next coding step deliberate.

## Current Implementation Snapshot

The current codebase already establishes a usable skeleton:

- CLI entrypoints for `run`, `scan`, and `enroll` exist in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:1).
- YAML config loading exists in [bekusup/config.py](/home/rene/bekusup/bekusup/config.py:1).
- Candidate-disk scanning and a basic system-disk rejection heuristic exist in [bekusup/scanner.py](/home/rene/bekusup/bekusup/scanner.py:1).
- A local JSON index and marker-file trust checks exist in [bekusup/store.py](/home/rene/bekusup/bekusup/store.py:1).
- Session creation/finalization exists in [bekusup/session.py](/home/rene/bekusup/bekusup/session.py:1).
- Transport wrappers for `rsync` and `scp` exist in [bekusup/transports.py](/home/rene/bekusup/bekusup/transports.py:1).
- Single-instance locking exists in [bekusup/lock.py](/home/rene/bekusup/bekusup/lock.py:1).
- A small test base exists for scanner and transport behavior in [tests/test_scanner.py](/home/rene/bekusup/tests/test_scanner.py:1) and [tests/test_transports.py](/home/rene/bekusup/tests/test_transports.py:1).

This is meaningful progress. The implementation is not just scaffolding; it already encodes several architectural bets.

## What It Gets Right

### 1. It captured the right product shape

The code is clearly building the right kind of tool:

- enrollment exists
- run locking exists
- snapshot lineage exists
- per-host execution exists
- host parallelism exists
- marker-file trust exists

Those are all central to the refined plan, even if the current details still need adjustment.

### 2. The trust model moved beyond volume labels

The implementation does not rely purely on labels. Marker-file plus local-index verification is already present in [bekusup/store.py](/home/rene/bekusup/bekusup/store.py:54), which is directionally correct and much safer than trusting `LABEL` alone.

### 3. Snapshot reuse is already wired into the transfer layer

`RsyncProvider.sync()` already decides between `--link-dest` and `--copy-dest` based on whether the snapshot base lives on the same filesystem in [bekusup/transports.py](/home/rene/bekusup/bekusup/transports.py:23). That means the implementation is already thinking in terms of per-session snapshot materialization, not just blind copies.

### 4. It anticipated slow or unavailable hosts

The `run` path already parallelizes hosts with `ThreadPoolExecutor` in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:68), which aligns with the requirement that one slow or dead machine should not dominate the whole run.

## Main Divergences From The Refined Plan

### 1. Dual-drive handling has already become active product behavior

The biggest divergence is that the current implementation did not merely "keep dual drives in mind." It implemented an active two-drive strategy:

- `cmd_run()` gathers all verified targets, sorts them by freshness, and loops through all of them in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:14).
- If a later disk has no local snapshot base, it reuses a just-completed snapshot from another drive as `foreign_snapshot_base` in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:39).
- The transport layer then converts that to `--copy-dest` when the snapshot base is on another filesystem in [bekusup/transports.py](/home/rene/bekusup/bekusup/transports.py:34).

This is clever and potentially very valuable.

It is also materially beyond the current manual-first plan. The refined plan intentionally left multi-disk selection conservative: fail safely when multiple eligible disks are present unless explicit policy is introduced. The code has already chosen a policy: "process all trusted targets, newest first, then seed later ones from earlier ones."

That is not necessarily wrong. It just means the codebase has moved ahead of the plan and now needs an explicit product decision:

- either bless this dual-drive strategy and document it as a supported feature
- or scale it back to match the safer single-target MVP

### 2. Disk selection semantics are broader than the plan

The current scanner returns every label-matching candidate that passes trust verification in [bekusup/scanner.py](/home/rene/bekusup/bekusup/scanner.py:48), and `run` processes all of them in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:31).

That differs from the refined plan, which moved toward:

- labels as discovery hints
- enrollment as trust
- explicit refusal when multiple candidate disks are present, unless policy says otherwise

This matters because dual-drive support is no longer just an optimization detail. It changes the operator model:

- one inserted disk means "one target"
- two inserted disks means "two targets in sequence"

The implementation already picked the latter.

### 3. The backup index is still too session-thin for the intended operator queries

The current index stores:

- drive identity
- first/last seen
- session id
- session timestamp
- session outcome

This is visible in [bekusup/store.py](/home/rene/bekusup/bekusup/store.py:33).

What it does not yet persist is the real query payload the refined plan cares about:

- per-host status by session
- host-to-drive history
- failure stage detail
- enough structured metadata to answer "which drives contain successful backups for laptop Z" without walking disk contents

The current manifest contains host statuses in [bekusup/session.py](/home/rene/bekusup/bekusup/session.py:18), but the index does not ingest them. So the design intent exists, but the durable operator-facing history is not there yet.

### 4. Session status modeling is coarser than the refined design

The current code distinguishes:

- host-level `succeeded` / `failed`
- session-level `complete` / `complete_with_warnings`

See [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:65) and [bekusup/session.py](/home/rene/bekusup/bekusup/session.py:53).

The refined design wants richer host outcomes:

- `succeeded`
- `partial`
- `failed`
- `unreachable`

That distinction matters because operator questions are mostly host-centric. The current implementation can report "not succeeded," but it cannot yet say whether a host never connected, partially copied, or failed during finalize.

### 5. Reachability is implicit rather than explicit

The current implementation does not probe reachability separately before sync. It effectively treats transport failure as host failure in [bekusup/cli.py](/home/rene/bekusup/bekusup/cli.py:49) and [bekusup/transports.py](/home/rene/bekusup/bekusup/transports.py:56).

That is a reasonable first cut, but it means:

- "unreachable" is not distinct from "copy failed"
- failure-stage reporting is not available
- operator logs are less informative than the refined plan intends

### 6. The trust model is directionally right but still minimal

The marker file currently records:

- `serial`
- `uuid`
- `label`
- `enrollment_time`

See [bekusup/store.py](/home/rene/bekusup/bekusup/store.py:45).

That is enough for a first pass, but the refined plan expects disk enrollment to become the main trust boundary. The current code does not yet carry:

- explicit Bekusup volume id
- explicit machine/enroller identity
- a clearer conflict model between marker file and local index

This is not a reason to rewrite the approach. It is a reason to harden the same approach.

### 7. System-disk safety is heuristic and underdefined

`is_system_device()` currently rejects mounts like `/`, `/boot`, `/boot/efi`, `/var*`, and `/sys*` in [bekusup/scanner.py](/home/rene/bekusup/bekusup/scanner.py:17).

That is useful, but it is still a heuristic shield, not a well-defined safety model. Since the project explicitly aims to be fail-proof for sloppy operators, this area should be treated as policy work, not just helper-code work.

### 8. The documentation currently overcommits relative to the code

The README describes:

- ".incomplete session markers locally" as part of the resilience story
- "Dual-Drive Smartass Optimization"
- local cache-forwarding over `--copy-dest`

See [README.md](/home/rene/bekusup/README.md:13).

Some of this is directionally true, but parts of the README already read like productized behavior rather than reviewed MVP behavior. The docs currently reflect the ambitious implementation path more than the refined manual-first plan.

## What This Means Strategically

The current codebase is not "wrong." It is ahead of the refined plan in one specific direction:

- it is evolving toward a multi-target rotation engine

The plan, meanwhile, was intentionally narrowed toward:

- manual-first operation
- explicit disk trust
- safe and queryable host/session history
- future cron compatibility

So the next step is not "fix bugs first." The next step is to decide which product line to stabilize:

1. **Conservative MVP line**
   Keep the tool single-target per run unless explicit multi-disk mode is enabled later.

2. **Dual-drive-first line**
   Officially adopt the current multi-target strategy and tighten the policy, logs, and safety model around it.

Right now the implementation is leaning toward option 2, but the written plan is still closer to option 1.

## Recommended Next Steps

### 1. Make a product decision on multi-disk behavior first

Before more implementation work, decide:

- when two enrolled backup disks are present, should `run`:
  - fail safely
  - pick one deterministically
  - back up both sequentially

This is the most important unresolved question because the current code has already chosen "back up both sequentially."

### 2. Promote the backup index from session log to operator query store

The next pass should preserve the current index approach, but make it actually answer the intended questions:

- which drives contain successful backups for host X
- when host X was last backed up successfully
- which session on drive Y included host X
- whether host X was unreachable, failed early, or partially copied

That is the durable state model the product actually needs.

### 3. Separate host outcome classes cleanly

The next implementation pass should make host outcomes first-class:

- `succeeded`
- `partial`
- `failed`
- `unreachable`

This will improve both manifests and the local index without forcing a major architectural rewrite.

### 4. Tighten disk-enrollment semantics

The current marker-file and local-index model should be kept, but made more explicit:

- stable Bekusup volume identity
- clearer marker/index reconciliation
- explicit treatment of serial-missing hardware
- explicit operator messaging for untrusted vs conflicting disks

### 5. Decide whether `--copy-dest` cross-drive seeding is MVP or post-MVP

The implementation already does something sophisticated and likely useful:

- hardlink reuse for same-disk snapshots
- copy-dest reuse for a second disk in the same run

That is a good optimization, but it should be deliberately classified:

- supported MVP behavior
- experimental optimization
- or deferred feature

### 6. Expand tests around state and policy, not just helpers

The current tests exercise:

- scanner candidate selection
- transport argument construction

The next useful tests should target:

- enrollment and trust verification
- single-disk vs dual-disk run policy
- session outcome classification
- index persistence of host outcomes
- cross-drive reuse policy
- protection against unsafe candidate disks

## Suggested Work Order

If another implementation round starts immediately, the cleanest order is:

1. Freeze the product decision for one-disk vs two-disk `run`.
2. Align the plan and README to that decision.
3. Upgrade the index schema to carry host outcomes and per-session host records.
4. Upgrade session/transport/orchestrator code to emit the richer outcome model.
5. Add tests around trust, index queries, and selected multi-disk policy.

## Bottom Line

Sis AI delivered real progress quickly. The code is not just a stub; it already contains a concrete worldview:

- enrolled disks are trusted
- runs are locked
- hosts can execute in parallel
- snapshots are reused intelligently
- multiple drives can be processed in one run

The main thing missing now is not more code. It is one round of product clarification so the next code pass does not keep widening behavior without a settled operator model.
