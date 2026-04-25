# Deliverables

Summary of work landed against `sis_ai_next_round_2.md`. Written against the code, not against intent.

## 1. Code changes

- **`partial` host state** — `cli.sync_host` now distinguishes `succeeded` (all paths synced), `partial` (some paths synced, some failed), and `failed` (nothing synced). `unreachable` is still set by the reachability probe before any sync is attempted. `bekusup/cli.py:98-108`.
- **Index query API** — three methods on `IndexStore`:
  - `get_last_success_for_host(host_name) -> (drive_id, session_id, timestamp)`
  - `drives_with_successful_host(host_name) -> list[drive_id]`
  - `most_recent_drive_for_host(host_name) -> drive_id | None`
  All treat `succeeded` and `partial` as counting toward "successful" — the snapshot contains real data either way. `bekusup/store.py:51-82`.
- **Per-host cross-drive reuse** — `host_cache_bases` (a `dict[host_name] -> drive_session_dir`) replaces the previous drive-wide `completed_sessions` list. A host is added to the cache iff its status on the completing drive is `succeeded` or `partial`. This means a single unreachable/failed host no longer strips the next drive of `--copy-dest` acceleration for hosts that did succeed. `bekusup/cli.py:37, 73-75, 122-126`.
- **Enrollment refuses identity-less disks** — `IndexStore.enroll_drive` raises `ValueError` when both serial and UUID are empty (`bekusup/store.py:27-29`); `cmd_enroll` now validates the same condition *before* writing `.bekusup-volume.json` to the disk and catches the `ValueError` to exit cleanly with a helpful message instead of a traceback (`bekusup/cli.py:200-215`). `verify_trust` also rejects identity-less disks as a defense in depth (`bekusup/store.py:116-117`).
- **Non-default SSH port in reachability probe** — `is_host_online` parses an optional `:port` out of the URI host part (`bekusup/cli.py:8-25`). It was extracted to module level for testability; behavior is otherwise unchanged.

## 2. Tests

22 tests pass under `pytest -q` (up from 14). Added:

- `test_cmd_run_host_partial` — covers the new `partial` state.
- `test_is_host_online_uses_custom_port` / `test_is_host_online_defaults_to_22` — probe port parsing.
- `test_cmd_run_freshness_ordering` — with two verified targets of different recorded freshness, the fresher drive runs first.
- `test_cmd_run_cross_drive_copy_dest` — with two fresh drives and a host that succeeds on drive A, drive B's sync for that host receives `snapshot_base=<A's session dir>/<host>/<subdir>`.
- `test_cmd_run_partial_a_preserves_b_reuse_for_succeeded_hosts` — the key behavioral claim of the per-host caching change: one host failing on A does not strip *another* host of cross-drive reuse on B.
- `test_get_last_success_for_host_picks_newest_across_drives` — the multi-drive, multi-session variant of the existing smoke test.
- `test_drives_with_successful_host` — `succeeded` and `partial` count; `failed` does not.
- `test_most_recent_drive_for_host` — including the "nobody has ever succeeded" → `None` case.

No new runtime dependencies. `pytest` remains the only test-time requirement beyond the stdlib.

## 3. What is still NOT done

- **Stale lock detection.** `RunLock` still assumes the OS releases `fcntl` locks when the process dies. If the lock file outlives its holder in a pathological scenario (NFS, some container runtimes), a fresh run will refuse to start with no guidance. A PID-liveness check would be cheap to add.
- **`scan` does not show per-host history for all hosts against all enrolled drives.** It shows the single most recent successful drive per host in the "GLOBAL HOST COVERAGE" section, which is what the operator usually wants, but `drives_with_successful_host` is not wired into the display yet.
- **No notifications.** No Slack, email, or webhook integration on `failed` / `complete_with_warnings`.
- **No auto-unmount / eject** after `SessionManager.finalize`. `config.destination.auto_unmount` exists in the config schema but nothing reads it.

## 4. Assumptions and unresolved edge cases

- **`partial` is treated as "useful enough" to serve as a cache base.** Rationale: `rsync --link-dest` / `--copy-dest` against a partial tree is still correct — rsync diffs against what's there and transfers the rest. If `partial` snapshots should not be trusted as a base, the gate in `cli.py:125` and the status list in `store.py:60,74` are the two places to change.
- **Non-default SSH ports are parsed from `host.uri`, not from `~/.ssh/config`.** A host reachable only via an SSH config port directive will still be probed on 22. Documented, not fixed.
- **`cmd_enroll`'s new identity check runs after `resolve_target_disk` and `ensure_mounted`.** The operator still sees "Target selected" and "Identity ->" lines before the refusal — intentional, so it's clear *which* disk was rejected and *why*.
- **The `test_cmd_run_*` tests mock `SessionManager`, `IndexStore`, `get_verified_targets`, and `get_provider`.** They exercise CLI orchestration logic, not the real session-directory and rsync subprocess plumbing. End-to-end multi-disk behavior against real filesystems remains unverified by the automated suite.
