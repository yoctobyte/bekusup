# Bekusup Disk Tools

These scripts are low-level operator tools for preparing and checking disks
before they become Bekusup rotation media. They came from the old `~/disktools`
workspace and now live here because disk lifecycle work belongs with Bekusup.

## Tools

| Script | Purpose |
|---|---|
| `dock_auto_proceed.sh` | Orchestrate a newly inserted local or remote docked disk: select, optionally reclaim, enroll, and optionally run Bekusup. |
| `disk_reclaim.sh` | Destructively reclaim a whole disk after safety checks, create one filesystem partition, and run a smoke test. |
| `disk_burnin.sh` | Run a prolonged read/write smoke test against a mounted filesystem or block-device partition. |
| `smart_log.sh` | Save timestamped `smartctl -x` reports for later comparison. |

## Dock Auto-Proceed

`dock_auto_proceed.sh` is the high-level operator entry point for a disk that
has just been inserted into a dock. It can run locally or over SSH:

```bash
tools/disk/dock_auto_proceed.sh --remote borg --repo /home/rene/bekusup \
  --detect-one --reclaim --allow-destructive --enroll --run --yes
```

Important safety behavior:

- Reclaim/format is disabled unless `--reclaim --allow-destructive` are both
  present.
- Auto-detection only proceeds when exactly one plausible non-system rotational
  HDD candidate is found.
- `--force-reclaim` is separate and only used when intentionally re-rolling a
  disk with existing signatures.
- `--run` performs a real `bekusup --no-dry-run run`; omit it to stop after
  reclaim/enroll.

## Local Evidence

The `local-evidence/` directory is intentionally ignored by git. Use it for
machine-specific burn-in notes, badblocks output, SMART reports, and other
evidence gathered from real disks.

Do not commit real disk serial inventories or one-off diagnostic logs unless
they have been sanitized and converted into documentation or fixtures.

## Safety

These tools are more dangerous than the normal Bekusup backup commands:

- `disk_reclaim.sh` can erase a disk.
- `disk_burnin.sh` writes large temporary files.
- `smart_log.sh` records hardware identity details.

Read each script's `--help` output before use and test with disposable disks.
