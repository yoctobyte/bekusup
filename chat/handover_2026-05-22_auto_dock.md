# Handover: Remote Dock Auto Mode

Date: 2026-05-22

## Summary

`disktools` has been merged into Bekusup under `tools/disk/`, and a remote
dock automation path was added for the "hard drive inserted into remote docking
station" workflow.

The intended user launcher is:

```bash
./bekusup-auto-dock.sh
```

Default target:

- remote host: `borg`
- remote repo: `/home/rene/bekusup`
- remote config: `/home/rene/bekusup/config.yaml`
- filesystem: `ext4`
- label prefix: `backupdisk`

The launcher calls:

```bash
tools/disk/dock_auto_proceed.sh --remote borg \
  --repo /home/rene/bekusup \
  --config /home/rene/bekusup/config.yaml \
  --detect-one \
  --reclaim \
  --allow-destructive \
  --enroll \
  --run \
  --yes \
  --fs ext4 \
  --label-prefix backupdisk
```

## Safety Model

- `dock_auto_proceed.sh` refuses to format unless both `--reclaim` and
  `--allow-destructive` are present.
- Auto-detection only proceeds when exactly one plausible non-system rotational
  HDD is found.
- `--force-reclaim` is not used by the launcher. Existing partitions or
  signatures should still make `disk_reclaim.sh` stop unless the operator
  explicitly adds `--force-reclaim`.
- `--run` performs a real `bekusup --no-dry-run run`.
- The destructive launcher was not executed during implementation.

## Useful Variants

Use a different remote:

```bash
BEKUSUP_DOCK_HOST=piborg ./bekusup-auto-dock.sh
```

Use a different filesystem:

```bash
BEKUSUP_DOCK_FS=xfs ./bekusup-auto-dock.sh
```

Stop after reclaim/enroll, without running a backup:

```bash
./bekusup-auto-dock.sh --no-run
```

Skip enrollment too:

```bash
./bekusup-auto-dock.sh --no-run --no-enroll
```

## Verification Performed

- `git status` was clean before this final handover/fix round.
- `bash -n` passed for:
  - `bekusup-auto-dock.sh`
  - `tools/disk/dock_auto_proceed.sh`
  - `tools/disk/disk_reclaim.sh`
  - `tools/disk/disk_burnin.sh`
  - `tools/disk/smart_log.sh`
- `./bekusup-auto-dock.sh --help` now prints launcher help and exits.
- `tools/disk/dock_auto_proceed.sh --help` includes the accepted `--enroll`
  flag and the `--no-run` override.
- A fake-device smoke test fails safely before destructive work:

```bash
tools/disk/dock_auto_proceed.sh --device /dev/not-a-disk --enroll --no-enroll
```

Result:

```text
Error: /dev/not-a-disk is not a block device
```

## Recent Commits

- `b853480 chore(tools): merge disktools into bekusup`
- `0717e9a feat(tools): add dock auto-proceed wrapper`
- `f82a561 chore: add remote dock auto launcher`

This handover also fixes the launcher/wrapper mismatch where the launcher
passed `--enroll` but `dock_auto_proceed.sh` did not yet accept that explicit
flag.
