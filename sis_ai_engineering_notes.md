# Sis AI Engineering Notes

## Purpose

This note is about engineering direction, not just product behavior.

It captures:

- where implementation should go next
- what important features are still missing
- where the current design is likely fragile
- where race conditions or operator hazards may exist
- where usability can still go wrong even if the code "works"

## Highest-Priority Next Work

### 1. Settle multi-disk execution semantics in code and tests

The product decision is now clear:

- if two enrolled backup disks are present, back up both

What still needs to be made explicit in implementation:

- deterministic ordering
- how the "fresher" disk is chosen
- when cross-disk reuse is allowed
- how failures on disk A affect disk B
- whether disk B may still run if disk A partially failed

This is the biggest missing behavioral spec in the code.

### 2. Upgrade the local backup index

The index currently does not yet carry the operator-useful state we actually want.

It should be able to answer:

- which drives contain successful backups for host X
- when host X was last successfully backed up
- which drive last succeeded for host X
- whether a host was unreachable, partial, failed, or succeeded

This means the index needs per-session per-host records, not only drive-level session outcomes.

### 3. Make host outcome modeling first-class

The code should explicitly distinguish:

- `succeeded`
- `partial`
- `failed`
- `unreachable`

Without this, logs and operator decisions stay blurry.

### 4. Harden disk enrollment and trust

The trust model is good in direction but still too thin operationally.

Need clearer behavior for:

- serial number missing
- filesystem UUID missing
- marker present but index absent
- index present but marker absent
- marker/index mismatch
- same label on two different disks

### 5. Strengthen safety policy around internal/system disks

This area is still heuristic-heavy and should be treated as a first-class safety feature.

## Missing Features

### 1. Explicit host reachability probing

Right now transport failure and host unreachability are not clearly separated.

The implementation should eventually distinguish:

- cannot resolve or connect
- authentication failed
- transport tool missing
- transfer failed after starting

### 2. Better disk status inspection

`scan` should evolve into a real operator inspection tool:

- enrolled vs unenrolled
- trusted vs conflicting
- last session per disk
- last success per host
- whether a disk is being used as a cache source candidate

### 3. Manifest/index reconciliation

Session manifests live on-disk.
The local index lives on the machine.

The software needs a clear reconciliation model for cases like:

- local index deleted
- disk moved between machines
- marker exists but local history does not
- incomplete session exists on disk but not in index

### 4. Better free-space prediction

Current free-space checks are rough.

Given dual-disk behavior and repeated sessions, better heuristics are needed:

- last successful session size
- host/path-level prior sizes
- margin for metadata and incomplete runs

### 5. Operator-oriented reporting

The tool will need concise but high-signal output:

- selected disks
- trust state
- chosen ordering
- snapshot base used
- copy-dest source used
- host results per disk

This matters because a backup tool is only useful if the operator can understand what happened quickly.

## Likely Fragile Areas

### 1. Concurrent manifest and index updates

Host jobs already run in parallel.

That means the code must be careful about:

- concurrent writes into shared manifest structures
- concurrent writes into the local JSON index
- partial process failure while state is being updated

This is a likely race-condition area.

### 2. Cross-disk reuse policy

Using one disk as a local seed source for another is powerful, but subtle.

Questions that need explicit answers:

- can a partially completed session ever be used as a source
- can `complete_with_warnings` be used as a source
- must source and target host sets match
- what happens if disk A succeeded for host 1 but failed for host 2

The safe answer is probably "only use clearly valid completed host data as reuse source."

### 3. Mount-state assumptions

Mounting and writable-path checks are easy to get wrong.

Fragile cases:

- automount races
- mount succeeds but permissions are wrong
- mountpoint exists but points to the wrong filesystem
- removable drive is unplugged during a run

### 4. Locking scope

There is already a process lock, which is good.

Still unresolved:

- stale lock recovery
- whether lock metadata is rich enough for diagnosis
- how to behave if another run dies mid-session

### 5. Trust drift between marker and device identity

USB bridges and some storage stacks can produce odd or unstable serial reporting.

If identity rules are too strict, good disks may be rejected.
If identity rules are too loose, wrong disks may be trusted.

This needs a deliberate policy, not ad hoc conditionals.

## Vulnerability / Safety Concerns

These are not "internet attacker" concerns first. They are mostly operator-safety and local-security concerns.

### 1. Passwords in config URIs

This is an explicit requirement, but it is still sensitive.

Risks:

- world-readable config files
- passwords appearing in process listings if wrapped carelessly
- leaking secrets through logs or exceptions

At minimum:

- config permission expectations should be documented
- logs must never print raw credentials

### 2. Shelling out to system tools

The design depends heavily on:

- `lsblk`
- `mount`
- `rsync`
- `scp`
- `sshpass`

Risk areas:

- unexpected command failures
- environment differences across distros
- unsafe assumptions about exit codes or output shape

### 3. Dangerous destination mistakes

The biggest real danger is still "write backups to the wrong disk."

This is worse than many classical software bugs.

The code should keep biasing toward safe refusal if trust or identity is ambiguous.

### 4. `--delete` semantics

The current transport layer uses `rsync --delete`.

That is correct for snapshot materialization in many cases, but it is also a foot-gun if paths are resolved incorrectly.

This area deserves extra care in tests and path normalization.

## Race Conditions To Watch

### 1. Two host workers updating the same session metadata

The destination trees are separate per host, which is good.
But the in-memory manifest and local index are shared state.

### 2. Two process instances starting close together

The lock should prevent overlap, but startup/cleanup behavior still needs testing.

### 3. Scan/enroll/run interacting with the same disk rapidly

Examples:

- user runs `scan`, then `enroll`, then `run`
- automount changes state between those calls
- disk identity changes due to reconnect/replug

### 4. Using just-created session data as a reuse source for a second disk

This is a useful optimization, but it introduces sequencing sensitivity.

The code must be clear about when newly created data is considered valid enough to seed another target.

## Usability Concerns

### 1. Too much magic

The software should automate aggressively, but it must not feel mysterious.

Operators need to see:

- why a disk was accepted or rejected
- why a host was skipped
- why one disk was used as a cache source
- what outcome actually resulted

### 2. Error messages need to be operational

Messages should not just say "failed."
They should say what the operator should check next.

### 3. Enrollment must feel safe

The one-time approval path should make it obvious:

- which disk is being enrolled
- why it is considered safe enough
- what identity was recorded

### 4. Recovery paths matter

The software should eventually make it easy to answer:

- what happened in the last run
- which disk should I insert next
- which host has gone stale

## Recommendation For Next Implementation Pass

The next coding round should focus on correctness and observability, not more feature width.

Recommended order:

1. Make multi-disk policy explicit in code and tests.
2. Expand the index schema to track per-host outcomes per session.
3. Add clearer host outcome classification.
4. Harden trust and disk-identity reconciliation.
5. Improve operator-visible reporting and diagnostics.
6. Add tests around races, state reconciliation, and dual-disk execution.

## Bottom Line

The project now has the right backbone.

The main risk is not lack of ideas. The main risk is that clever behaviors become real before their safety and state model are nailed down.

So the next implementation round should prioritize:

- policy clarity
- durable state correctness
- operator trust
- concurrency safety
- understandable behavior
