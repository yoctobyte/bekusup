# Bekusup — Tape-Drive Style Rotating Backup

A rotating backup tool that writes plain, browsable filesystem trees to enrolled backup disks. Point-in-time snapshots share storage via `rsync --link-dest` (same disk) and `--copy-dest` (across two drives in the same dock). Restore by plugging any enrolled disk into any Linux machine and copying files out — no proprietary format, no chunk catalog.

## Quick start

1. **Install** — `sudo apt install rsync sshpass util-linux python3-yaml`, then clone this repo. (Full installation details below.)
2. **Configure** — create a `config.yaml` in the repo root describing your backup-disk label and the hosts/paths to back up. Minimal example below.
3. **Enroll once, then run** — plug in a backup disk and run `./bekusup-cli.sh enroll` once per disk. After that, `./bekusup-cli.sh run` performs one backup cycle. Use `./bekusup-cli.sh scan` any time to see enrollment and history status.

## Minimal configuration

```yaml
destination:
  label_contains: backup            # partition label substring that identifies an eligible disk
  fallback_mount_root: /mnt/bekusup # where to mount a disk that isn't already mounted
  auto_unmount: false

run_policy:
  min_free_space_gb: 20
  max_parallel_hosts: 2

hosts:
  - name: laptop
    transport: ssh
    uri: ssh://user@10.0.0.5     # append :2222 for non-default SSH ports
    paths:
      - source: /home/user
        dest_subdir: home
  - name: localfiles
    transport: local
    paths:
      - source: /srv/data
        dest_subdir: srv-data
```

For password-over-SSH (not recommended, but supported): `ssh://user:password@host`. Requires `sshpass`.

## Commands

| Command | Action |
|---|---|
| `scan` | Reports eligible disks, enrollment status, session history, and per-host last-known-good coverage. |
| `enroll` | One-time approval of a newly plugged disk: writes `.bekusup-volume.json` to it and records its serial/UUID locally. |
| `run` | Performs one backup cycle across all enrolled disks present, backing up every configured host to each one. |
| `flyover` | Preflight helper: checks config, disk mountability/trust, host reachability, and source sizes without creating backup sessions. |

```bash
./bekusup-cli.sh --config ./config.yaml scan
./bekusup-cli.sh enroll
./bekusup-cli.sh run
./bekusup-flyover.sh --config ./config.yaml
```

## Disk Preparation Tools

Low-level disk lifecycle scripts live in `tools/disk/`:

- `dock_auto_proceed.sh`: orchestrate a newly inserted local or remote docked
  disk through selection, optional reclaim, enrollment, and optional backup.
- `disk_reclaim.sh`: destructively reclaim and format a whole disk after safety
  checks.
- `disk_burnin.sh`: run a write/read burn-in against a mounted filesystem or
  partition.
- `smart_log.sh`: save timestamped SMART reports for comparison.

These are operator tools for preparing rotation media before normal Bekusup
enrollment. They can erase data or record hardware identifiers; read
`tools/disk/README.md` and each script's `--help` before use.

## Installation

### System packages (recommended on Debian/Ubuntu)

```bash
sudo apt install rsync sshpass util-linux python3-yaml
```

This gets you everything Bekusup needs without pip or a virtualenv. `python3-yaml` provides the PyYAML dependency.

### Clone

```bash
git clone <repo-url> bekusup
cd bekusup
```

The CLI wrapper (`bekusup-cli.sh`) sets `PYTHONPATH` to the repo and invokes `python3 -m bekusup.cli`, so no system-wide install is required.

### Pip alternative

If you prefer pip (in a virtualenv or with `--user`):

```bash
pip install -r requirements.txt
```

### Test prerequisites

On Debian/Ubuntu:

```bash
sudo apt install python3-pytest
```

Run the suite with `python3 -m pytest -q`.

## Workflows

### 1. Single-disk rotation

The traditional swap-a-disk-each-week pattern:

1. Every Sunday, unplug last week's **Disk A** from the dock.
2. Plug in **Disk B** (which holds the previous rotation's snapshots).
3. If Disk B has never been used with Bekusup on this machine, run `./bekusup-cli.sh enroll`.
4. Run `./bekusup-cli.sh run` manually or from cron. The tool locates Disk B's previous session, builds a hardlink-based session over it via `rsync --link-dest`, and pulls from each configured host.

### 2. Dual-drive cache acceleration

If you dock two enrolled disks simultaneously (2-bay NAS, dual dock, etc.):

1. Slot in yesterday's **Disk A** and a fresher / newly enrolled **Disk B**.
2. `bekusup run` orders the two by freshness (newest local session first).
3. **Disk A** is backed up first, reusing its own prior session as a `--link-dest` base.
4. **Disk B** is backed up second. For any host that just succeeded on Disk A, Disk B uses Disk A's fresh session as a `--copy-dest` base — so the bytes are cloned locally across the dock instead of pulled again over the network.

If a host fails or is unreachable on Disk A, Disk B falls back to pulling that one host directly from the source. The cache re-use is per-host, not all-or-nothing.

## Philosophy

Bekusup is built on the idea that backup behavior shouldn't be "magical." Typical rotation tools use opaque differential blob catalogs or cloud-tied subscriptions where user visibility into the file structure is zero.

A backup disk should be a complete, fully browsable, point-in-time filesystem tree. You should be able to plug it into any Linux machine, walk the directories, and pull out a single file without parsing any intermediate format. At the same time, maintaining point-in-time snapshots should not waste space or network bandwidth.

## Goals

- **Offline resilience.** Mobile hosts (laptops) that aren't reachable are cleanly skipped and recorded as `unreachable` in the session manifest.
- **Operator sloppiness guards.** `fcntl`-based single-instance locking; refusal to back up to internal/system disks; hard rejection of spoofed serial or UUID; refusal to enroll a disk with no stable identity.
- **Space and network efficiency.** Native filesystem hardlinks (`--link-dest`) for same-disk reuse; `--copy-dest` for cross-disk local cloning instead of re-pulling unchanged data.

## Strengths and weaknesses

**Strengths**

- Transparent data structure: directories, not proprietary chunks.
- Space efficient: N sessions in one day cost roughly one session's worth of disk, because unchanged files are hardlinked across sessions.
- Cross-drive acceleration: when two enrolled disks are docked, the second one clones unchanged data from the first locally.
- Concurrent host execution with per-host timeouts so a dead SSH endpoint doesn't stall the whole run.

**Weaknesses**

- Requires a POSIX-capable destination filesystem for the hardlink optimization. `FAT32` / `exFAT` destinations degrade every session to a full file copy.
- The local index (`~/.local/share/bekusup/index.json`) is per-machine. Moving rotated disks to a different machine will not transfer the rotation history without manual re-enrollment.

## Limitations

- Requires `rsync` available on the remote side of each SSH host (falls back to `scp` if explicitly configured).
- Does not attempt mid-transfer block-level recovery. Transfers are considered successful only when the `rsync` subprocess exits zero.

---

## Disclaimer

> [!CAUTION]
> Bekusup modifies filesystems and runs elevated block-device probing to manage disk mounts programmatically. While guarded, manipulating physical block partitions (especially over dynamic `/dev/` paths) always carries the risk of data loss.
>
> **Do not run this tool against your only copy of critical data** without fully understanding the `config.yaml` options, the `.bekusup-volume.json` marker semantics, and the behavior of `rsync --delete`.
>
> The authors accept no responsibility for destroyed data, accidentally formatted partitions, or backup failures. Test with disposable drives first.
