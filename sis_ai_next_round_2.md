# Sis AI Next Round 2

## Read First

- [sis_ai_next_round.md](/home/rene/bekusup/sis_ai_next_round.md) — previous round's goals (partially addressed)
- [deliverables.md](/home/rene/bekusup/deliverables.md) — what the previous round claimed to deliver
- [sis_ai_status_note.md](/home/rene/bekusup/sis_ai_status_note.md) — lifecycle assessment
- the code itself in `bekusup/` (≈650 LoC) — verify against claims; the prose in `deliverables.md` overstates what actually landed

## Product Decisions (still settled — do not revert)

- `bekusup run` backs up all enrolled disks present in one cycle.
- Fresher local disk accelerates the other via `--copy-dest` when that saves network.
- Each disk must remain independently useful.
- No cloud, no global per-file catalog, must run on system Python without a virtualenv.
- Safe refusal beats guessing on ambiguous identity.

## Current State (audited 2026-04-20)

What actually landed from the previous round:

- Per-session per-host records in `IndexStore` (store.py:48-56). Good.
- `unreachable` host state via `nc -z` probe (cli.py:54-66). Good.
- Freshness-ordered dual-disk execution (cli.py:21-36). Good.
- `st_dev`-based link-dest vs. copy-dest decision (transports.py:36-42). Good.
- HARD REJECT on marker/lsblk serial or UUID mismatch (store.py:99-103). Good.
- `fcntl` RunLock (lock.py). Good.
- Operator-style `scan` dashboard (cli.py:116-155). Good.

What `deliverables.md` claimed but that **does not exist in code**:

- The "partial" host state. `sync_host` only emits `succeeded` / `failed` / `unreachable` (cli.py:95). A host with N paths where 1 of N fails is flattened to `failed` — losing the fact that N-1 paths actually synced.
- Any index *query* API. `IndexStore` has `enroll_drive` / `get_drive` / `log_session`. Nothing answers "which drive last succeeded for host X" or "when did host X last succeed anywhere." The operator still has to grep `~/.local/share/bekusup/index.json` by hand.
- Meaningful tests for the dual-disk policy. `tests/test_cli.py` has a single test (one drive, one unreachable host). Freshness ordering, `--copy-dest` cross-drive selection, and "partial-A blocks B" are all untested.
- Per-host history in `scan` output. The dashboard shows only the latest session on each drive, not "host X last succeeded 2026-04-15 on Drive Y."

Real bugs / sharp edges worth fixing:

- **Identifier collapse when both serial and UUID are empty.** `drive_id = serial if serial else f"{uuid}-{label}"` (store.py:29, cli.py:41, scanner.py caller) becomes `-{label}` when both are missing. Two unidentified disks with the same label would collide in the index. Enrollment must refuse when both serial and uuid are empty.
- **`completed_sessions` gate is too strict for cross-drive reuse.** cli.py:112-114 only appends when `outcome == "complete"`. A single unreachable host on Drive A downgrades the whole session to `complete_with_warnings` and strips Drive B of copy-dest acceleration for *every* host — including hosts that succeeded on A. Reuse should be decided per-host, not per-drive-session.
- **`is_host_online` hardcodes port 22** (cli.py:63). Non-default SSH ports are misreported as unreachable. The `HostConfig.uri` already encodes the port if present; parse it.
- **rsync/scp error log leaks less than it looks.** `str(e)` on `CalledProcessError` does include command args. The redaction in transports.py:71 is correct only when `use_sshpass and password`; fine today, but fragile if the password path ever changes. Worth a small test.

## Priority Work (this round)

### 1. Fill in the `partial` host state

Treat per-path outcomes as first-class. A host with mixed path success/failure should record `partial` with details of which `dest_subdir`s succeeded and which failed. Update `sync_host` in cli.py:68-98 and extend the manifest written by `SessionManager.record_host_status`.

### 2. Add the index query API the operator actually needs

Add read-side methods to `IndexStore` (store.py):

- `last_success_for_host(host_name) -> (drive_id, session_id, timestamp) | None`
- `drives_with_successful_host(host_name) -> list[drive_id]`
- `most_recent_drive_for_host(host_name) -> drive_id | None`

Then wire them into `cmd_scan` so the dashboard shows, per host, the last-known-good drive and timestamp across the whole fleet — not just the latest session on the disk currently plugged in.

### 3. Fix cross-drive reuse gating to be per-host

Replace the `completed_sessions` list-of-drive-dirs with a structure that records, for each completed drive in this run, which hosts succeeded on it. When Drive B evaluates `foreign_snapshot_base` for host H, require that H succeeded on A — don't block B solely because *some other* host was unreachable on A.

### 4. Reject enrollment when identity is unknowable

In `cmd_enroll` (cli.py:157-171) and `IndexStore.enroll_drive` (store.py:27-42), refuse to enroll a disk that has neither a serial nor a filesystem UUID. Surface a clear operator message pointing at `lsblk -o NAME,SERIAL,UUID`.

### 5. Honor non-default SSH ports in the reachability probe

Parse host/port out of `host.uri` in `is_host_online` (cli.py:54-66). Default to 22 only when the URI omits a port.

### 6. Tests

Cover the above. Specifically:

- multi-drive freshness ordering drives the execution order (`cmd_run` with two mocked verified targets)
- cross-drive reuse picks a completed drive's session as `--copy-dest` for the next drive
- partial-A does **not** strip Drive B of reuse for the hosts that succeeded on A
- a host with 2 paths where 1 fails records `partial`, not `failed`
- `IndexStore.last_success_for_host` returns the most recent drive+session across multiple drives
- `cmd_enroll` refuses a disk with no serial and no UUID
- `is_host_online` uses a non-standard SSH port when the URI carries one

Keep `pytest -q` green and add no new runtime dependencies.

## Out of Scope (again)

- Cloud, off-site sync, global per-file catalogs.
- Stale-lock PID liveness checks.
- Slack / email / webhook notifications.
- Mid-transfer block corruption recovery.
- Requiring a virtualenv.

## Deliverables

- Code changes scoped to the six priorities above.
- Tests for each.
- A short note covering: what changed, what is still not done, what assumptions you made (especially for edge cases the tests don't cover).
- Update `deliverables.md` and `sis_ai_status_note.md` to reflect reality — not aspirations.
