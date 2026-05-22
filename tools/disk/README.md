# Bekusup Disk Tools

These scripts are low-level operator tools for preparing and checking disks
before they become Bekusup rotation media. They came from the old `~/disktools`
workspace and now live here because disk lifecycle work belongs with Bekusup.

## Tools

| Script | Purpose |
|---|---|
| `disk_reclaim.sh` | Destructively reclaim a whole disk after safety checks, create one filesystem partition, and run a smoke test. |
| `disk_burnin.sh` | Run a prolonged read/write smoke test against a mounted filesystem or block-device partition. |
| `smart_log.sh` | Save timestamped `smartctl -x` reports for later comparison. |

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
