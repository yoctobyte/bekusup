# Sis AI Status Note

## Current Status

Closer to a trustworthy operator-grade MVP than it was, but still not done. The dual-disk, per-host caching, identity-refusal, and index-query logic all landed *and* have targeted tests. Several operator-facing gaps remain.

## What is already there

- `run`, `scan`, `enroll` commands.
- YAML config loading (dataclasses in `bekusup/config.py`).
- Disk scanning with system-partition rejection heuristics (`loop*`, `ram*`, `mapper`, `crypto`, plus `/`, `/boot`, `/var`, `/sys` mount-point checks).
- `.bekusup-volume.json` marker-file trust paired with a local `~/.local/share/bekusup/index.json`.
- Hard rejection of spoofed serial/UUID in `verify_trust`.
- Hard rejection of disks with neither a serial nor a UUID, both at enrollment (`cmd_enroll`, `IndexStore.enroll_drive`) and at trust-verification time.
- Four host states: `succeeded` / `partial` / `failed` / `unreachable`.
- Per-host cross-drive `--copy-dest` caching (drive A's successful hosts seed drive B's sync, independently per host).
- `IndexStore` query API: `get_last_success_for_host`, `drives_with_successful_host`, `most_recent_drive_for_host`.
- `scan` dashboard shows per-drive trust/coverage and a "Global Host Coverage" section derived from the index.
- 2-second `nc -z` reachability probe that honors a non-default SSH port parsed from the `ssh://user@host:port` URI form.
- `fcntl` single-instance RunLock.
- 22 passing tests (`pytest -q`), covering partial-state aggregation, cross-drive reuse (including the partial-A case), freshness ordering, identity refusal, probe port parsing, and index query correctness across multiple drives/sessions.

## What is still missing or incomplete

### 1. Freshness ordering is too strict
`cmd_run` sorts drives by the timestamp of their most recent `outcome == "complete"` session. A drive whose latest session is `complete_with_warnings` is treated as freshness 0. In practice that means "one unreachable host ever" pushes a drive to the back of the queue forever. Should probably count any finalized session.

### 2. Reporting does not surface all available history
The index now knows, for each host, *every* drive that has a successful snapshot of it. The `scan` dashboard only shows the most recent one. Listing all drives where host X has a recoverable copy would be cheap and useful.

### 3. No notifications
No Slack / email / webhook integration. An unattended cron run that ends in `complete_with_warnings` is silent unless the operator reads the session manifest.

### 4. No auto-unmount / eject
`config.destination.auto_unmount` exists in the dataclass but no code reads it. After `SessionManager.finalize` the drive is left mounted.

### 5. No stale-lock recovery
`RunLock` trusts the OS to release `fcntl` locks on process death. A leftover lock file from an abnormal exit will block future runs with a message that does not suggest recovery.

### 6. Integration coverage is thin
All the `cmd_run` tests mock `SessionManager`, `IndexStore`, `get_verified_targets`, and `get_provider`. They exercise orchestration logic. Real behavior against a real filesystem — session dir finalization, marker/manifest files, actual `rsync` command assembly with `--link-dest` vs `--copy-dest` picked by `st_dev` — is covered only indirectly via the transport-level unit tests, which themselves mock `subprocess.run`.

## Bottom line

Architecturally coherent. The dual-disk contract is now explicit in code *and* tested. The remaining gaps are operator-UX (freshness strictness, dashboard depth, no notifications, no auto-unmount, no stale-lock recovery) and integration-level test coverage. Do not ship this against irreplaceable data without at least one real-hardware dry run.
