# Bekusup - Implementation Plan

## MVP Intent

`bekusup` is a Python CLI that writes timestamped full-backup sessions to removable disks whose volume label contains `backup`.

The MVP is **manual-run first**:

- the user invokes `bekusup run` after swapping disks or whenever they want an immediate backup
- the tool should detect the currently eligible disk automatically without requiring the user to declare that rotation happened

Scheduled execution from cron/systemd is not the main MVP workflow, but the design must remain compatible with it. In practice, that means repeated runs on the same disk should still create additional timestamped sessions rather than overwriting prior ones.

## Proposed Architecture

### 1. Configuration Model

The config should describe:

- which destination disks are eligible
- which hosts exist
- how to authenticate to each host
- which paths to capture from each host
- optional transfer controls such as bandwidth limits
- as little incidental complexity as possible for the operator

**Proposed configuration structure:**

```yaml
destination:
  label_contains: "backup"
  fallback_mount_root: "/mnt/bekusup"
  auto_unmount: false

run_policy:
  min_free_space_gb: 20
  incomplete_suffix: ".incomplete"
  complete_marker: "SESSION_COMPLETE"
  max_parallel_hosts: 2

hosts:
  - name: "workstation-a"
    transport: "ssh"
    uri: "ssh://keysuser@10.0.0.5"
    bandwidth_limit_kbps: 0
    paths:
      - source: "/home/keysuser/"
        dest_subdir: "home/keysuser"
      - source: "/etc/nginx/"
        dest_subdir: "etc/nginx"

  - name: "laptop-b"
    transport: "ssh"
    uri: "ssh://passuser:supersecret@10.0.0.10"
    bandwidth_limit_kbps: 4000
    paths:
      - source: "/home/passuser/"
        dest_subdir: "home/passuser"

  - name: "local-machine"
    transport: "local"
    paths:
      - source: "/etc/"
        dest_subdir: "etc"
      - source: "/home/rene/"
        dest_subdir: "home/rene"
```

Configuration design rules:

- keep the schema shallow and readable rather than highly abstract
- default most behavior so a small config is enough for common cases
- require only the fields the operator genuinely needs to know
- use stable host names because they become part of session layout and catalog history

### 2. Run Modes

The CLI should keep one execution path and stay minimal in MVP:

- `bekusup run`: perform one backup cycle immediately against the detected eligible disk.
- `bekusup scan`: report eligible disks, mount state, free space, and the most recent session on each disk.
- `bekusup enroll`: perform one-time approval of a new backup disk and write the Bekusup marker file.

Cron/systemd compatibility remains a design constraint, but `bekusup` itself does not need to become a scheduler in MVP.

### 3. Destination Disk Selection

The plan must define deterministic selection behavior when multiple disks are present:

1. Enumerate block devices via `lsblk -J -o NAME,LABEL,MOUNTPOINT,FSTYPE,SIZE`.
2. Reject obvious internal/system disks before anything else.
3. Treat the label match as a discovery hint, not authorization.
4. If more than one candidate device exists, fail with a clear error unless config later adds an explicit selection rule.
5. If exactly one candidate device exists, use it.
6. If the chosen device is not mounted, attempt a safe mount into `fallback_mount_root/<device-label-or-name>` or request confirmation before privileged mount behavior.

This avoids silently writing to the wrong rotated disk in a multi-bay setup, while still allowing the user to swap disks without telling the tool what changed.

### 4. Disk Enrollment and Trust

The volume label should only help discover candidate disks. Actual trust should come from a Bekusup marker file stored on the disk after one-time operator approval.

The enrolled disk should contain a small marker file such as `.bekusup-volume.json`.

The marker should contain at minimum:

- Bekusup volume identifier
- enrollment timestamp
- drive serial number when available
- filesystem UUID when available
- label at enrollment time
- optional machine identifier that enrolled the disk

Enrollment behavior:

- if a candidate disk already contains a valid marker file and its current identity still matches the recorded identity closely enough, treat it as an approved backup disk
- if a candidate disk matches the discovery rules but has no marker file, treat it as untrusted and require explicit `bekusup enroll`
- when enrolling, write the marker file and add the disk to the local backup index
- if the marker file is present but current serial number or filesystem UUID conflicts with the recorded identity, refuse to use the disk

Safety rules:

- never trust the label alone
- never trust the marker file alone if the current disk identity disagrees with it
- keep safeguards against selecting the local OS or internal data disk unintentionally
- keep the design open to future off-site disk workflows without changing the trust model

### 5. Session Layout, History, and Snapshot Reuse

Every execution creates a new timestamped session directory, even if the same disk was used earlier that day.

**Example layout:**

```text
/mnt/bekusup/backup_disk_a/
  sessions/
    2026-04-20T08-00-00.incomplete/
    2026-04-20T12-00-00/
    2026-04-20T16-00-00/
```

Rules:

- Start each run in `sessions/<timestamp><incomplete_suffix>/`.
- Each host writes into its own subtree inside that session.
- If a previous successful session exists on the same disk, use it as the snapshot base for the new session.
- Reuse unchanged files via hardlinks by default so each session still appears as a full backup while consuming less space.
- Copy changed or new files normally into the new session.
- Leave files absent from the new snapshot when they no longer exist in the source.
- Disable hardlink reuse automatically on filesystems that do not support it, or when the operator turns it off.
- On successful completion of the entire run, rename the session directory to remove the incomplete suffix and write the `complete_marker` file.
- If the process is interrupted or the destination becomes unreliable, leave the session marked incomplete and record per-host status in a manifest/log file.
- Multiple runs per day are expected and desirable. This preserves point-in-time history when files change during development.

This satisfies both "full backup per session" and "keep history if files change several times per day" while keeping repeated sessions on the same disk space-efficient.

### 6. Backup Index and Drive History

The system should maintain a persistent local index outside the removable disks so it can reason about backup history even when a given disk is not currently inserted.

The index should be keyed by drive identity, preferring the drive serial number when available from the OS and falling back to filesystem UUID plus label when necessary.

This index is not a file catalog. It should not track every file ever seen. It should only track drive identity, sessions, and host outcomes.

The index must record, at minimum:

- drive serial number
- drive label
- filesystem UUID when available
- first-seen and last-seen timestamps
- most recent mountpoint observed
- session identifier
- session timestamp
- session outcome
- host identifier
- host outcome
- optional failure stage such as `connect`, `probe`, `transfer`, or `finalize`

Operational uses:

- identify whether the currently inserted disk is known
- tell the user which drive serial number last received a successful backup
- tell the user which drive serial number last held a backup for a given host
- tell the user which drives contain successful backups for a given host
- help detect operator mistakes such as reusing the same disk repeatedly while another rotated disk has gone stale

This index will grow over time, but much more slowly than a file catalog. For MVP, it may grow append-only as long as it remains machine-readable and queryable.

### 7. Run Locking and Non-Interference

Manual runs and future scheduled runs must not bite each other.

The plan should require a lock before any session work begins:

- acquire a single canonical host-local process lock at startup, for example under `/var/lock/bekusup.lock` or `/tmp/bekusup.lock`
- if another run is already active, fail fast with a clear message rather than running concurrently
- include lock metadata such as PID, hostname, and start time for operator visibility
- release the lock on normal exit and make stale-lock recovery an explicit implementation concern

This ensures a manual invocation and a cron-triggered invocation cannot write into the same destination tree at the same time.

### 8. Transport Abstraction

We will define a `SyncProvider` interface around the transport engine rather than baking `rsync` assumptions into the orchestrator.

Providers:

- **`RsyncProvider`**: preferred when available locally and remotely.
- **`ScpProvider`**: fallback when SSH is available but `rsync` is not.
- **Future**: `SmbProvider`, `RcloneProvider`.

The provider contract must specify:

- recursive copy of a declared source path into a declared destination path
- provider-specific bandwidth limiting when configured
- authentication support for SSH keys and password-based access
- structured success/failure reporting
- consistent handling of missing paths, partial transfers, and non-zero exit codes

The system may wrap commands with `sshpass` only when a password is explicitly present in config.

### 9. Reachability, Parallelism, and Failure Handling

For each configured host:

1. Determine whether the host is reachable enough to attempt transfer.
2. If unreachable, record a warning in the run manifest and continue.
3. If reachable, choose the best provider available for that host.
4. Attempt all configured paths for that host.
5. Record host-level and path-level status.

Offline hosts must never abort the whole run, but they must not be skipped silently.

The orchestrator should also support bounded parallelism across hosts:

- run hosts independently so one slow source does not block the entire backup window
- cap concurrency with `max_parallel_hosts`
- ensure each host only writes to its own session subtree so concurrent workers never share destination paths
- keep path execution for a single host sequential unless later requirements justify more complexity
- serialize manifest and backup-index updates even if host transfers run concurrently

Session outcome rules must distinguish between different classes of completion:

- **complete**: all configured hosts and paths succeeded
- **complete_with_warnings**: the run finished, but some hosts were unreachable or some non-fatal warnings occurred
- **incomplete**: the run was interrupted, the destination became unavailable, or a transfer failed in a way that makes the session unreliable

This avoids treating ordinary offline laptops as if the whole backup run were corrupt.

Host outcome rules should also be explicit:

- **succeeded**: the host snapshot completed successfully for that session
- **partial**: some data transferred for that host, but the host snapshot is not trustworthy as complete
- **failed**: the host backup did not produce trustworthy output
- **unreachable**: the host could not be contacted, so no transfer began

### 10. Manifest, Logging, and Operator Safety

Each session should contain machine-readable metadata, for example:

- session timestamp
- selected disk identity
- selected disk serial number
- mountpoint used
- config snapshot or config hash
- session outcome
- snapshot base session if one was used
- hardlink reuse enabled or disabled
- per-host status
- per-path status
- provider chosen
- bytes transferred if available
- warnings and failures

Console output should stay concise, but the session manifest should preserve enough detail to audit what happened during unattended runs.

Because operators are sloppy and the tool should be fail-proof by default, the user-facing behavior should prefer safe refusal over silent guesswork:

- fail if no eligible disk is present
- fail if more than one eligible disk is present
- fail if the destination is not writable
- fail if required tooling is unavailable and no fallback provider can run
- continue past individual host outages, but never hide them
- print the selected disk label and serial number before transfers begin
- write enough metadata that the user can recover what happened after the fact

### 11. Space and Retention

Because repeated runs may append many snapshot-style sessions to the same disk, the MVP must check free space before starting:

- inspect available free space on the selected disk
- compare it against the size of the last successful session when available, plus `min_free_space_gb` as a safety margin
- otherwise fall back to the fixed `min_free_space_gb` threshold
- surface a clear message that the disk should be rotated or cleaned

Automatic pruning of old sessions can be a later feature. MVP should preserve history rather than delete it implicitly.

## Implementation Steps

1. **Define CLI and Config Schema**: Build the `argparse` entrypoints for `run`, `scan`, and `enroll`, and validate the YAML schema around destination, run policy, hosts, and paths.
2. **Implement Locking**: Add a single-run lock so overlapping invocations fail safely.
3. **Implement Disk Scanner**: Detect candidate disks, reject obvious internal/system disks, collect serial numbers and filesystem identifiers, resolve deterministic selection, validate writable mountpoints, and mount when required.
4. **Implement Disk Enrollment**: Add one-time enrollment, write the disk marker file, and verify marker-plus-identity checks on every run.
5. **Implement Backup Index Store**: Persist drive identity, session records, and host outcomes outside the removable media so the tool can answer "what was the last drive used" and "which drives contain backups for host X."
6. **Implement Session Manager**: Create timestamped session directories, choose the previous successful session on the same disk as the snapshot base, write manifests, classify session outcome, and expose helpers for per-host/per-path status recording.
7. **Implement Snapshot Reuse**: Reuse unchanged files via hardlinks when supported by the destination filesystem, and fall back safely when hardlink mode cannot be used.
8. **Implement Transport Layer**: Define `SyncProvider`, then implement `RsyncProvider` and `ScpProvider`, including SSH key usage, `sshpass` wrapping, and optional bandwidth limits.
9. **Implement Orchestrator**: Loop through hosts and paths, support bounded host-level parallelism, keep each host isolated to its own subtree, skip unreachable hosts with warnings, and keep the run alive across partial failures.
10. **Implement Scan/Status Output**: Report discovered disks, enrollment state, prior sessions, index history, and mount/free-space status so the user can inspect current and last-seen backup media.
11. **Verify Manual Workflow and Future Scheduler Safety**: Test enrollment, repeated runs on the same disk, interrupted runs, unreachable hosts, missing `rsync`, overlapping invocations, index behavior when a disk is absent, and hardlink snapshot reuse.

## Explicit MVP Decisions

- Manual runs are the primary MVP workflow.
- The tool should detect whichever eligible backup disk is currently inserted without requiring explicit user confirmation that a swap occurred.
- The user should only need to approve a new backup disk once via enrollment.
- Volume labels are discovery hints, not the trust boundary.
- Multiple sessions in one day are valid and should preserve file history across development changes.
- A persistent local index should track drive identity, sessions, and host outcomes independently of whether a disk is currently inserted.
- Hardlink-based snapshot reuse on the same disk should be enabled by default when the destination filesystem supports it.
- Host outages are expected and should not abort the full run.
- Bounded host-level parallelism is desirable so one slow machine does not hold up all others.
- Full-session correctness matters more than deduplication for MVP.
- `bekusup` will remain compatible with cron/systemd later, but embedded scheduling is out of scope for MVP.
- If more than one eligible backup disk is simultaneously present, MVP should fail safely instead of guessing.
- Overlapping invocations must fail safely rather than running concurrently.

## Deferred for Later

- Incremental or deduplicated backups between sessions
- Automatic pruning/retention policies
- Backup index compaction or pruning policies
- Explicit disk-preference rules when multiple backup-labeled disks are present
- Additional providers such as SMB and `rclone`
- Exclude/include pattern tuning beyond the minimal required path list
