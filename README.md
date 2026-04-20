# Bekusup - Tape-Drive Style Rotating Backup System

> [!CAUTION]
> **THICK DISCLAIMER:** This project actively modifies file systems and executes elevated block-device probing logic to handle disk mounts programmatically. While highly guarded, manipulating physical block partitions (especially over dynamic `/dev/` paths) always carries the risk of data loss. Do NOT run this tool to manage your only copy of critical data without fully understanding the underlying configuration files, `.bekusup-volume.json` marker structures, and the behavior of `rsync --delete`. The authors bear no responsibility for destroyed data, accidentally formatted system partitions, or backup failures. Test with disposable hard drives first!

## Philosophy
`bekusup` sits on the idea that backup behavior shouldn't be overly "magical." Typical modern rotation setups enforce entirely opaque differential blob catalogs or cloud-tied subscriptions where user visibility into file structure is strictly zero. 

Our philosophy is built on tape-drive rotation mentality mixed with native system primitives: A backup drive should exist as a complete, fully traversable, point-in-time filesystem tree. You should be able to plug the disk into any foreign Linux machine, browse perfectly uncompressed file directories, and pull out a single file manually without parsing intermediate delta layers. At the same time, maintaining point-in-time snapshots should not waste space nor drastically tax network pipes.

## Goals
- Provide **Offline Resilience**: Allow mobile hosts (like laptops) to gracefully be skipped when inaccessible, safely generating `.incomplete` session markers locally.
- Prevent **Operator Sloppiness**: Guard against dual-mounting mistakes, accidentally using internal root-drives, and overlaps via `fcntl` locking boundaries.
- Ensure **High Spatial/Network Efficiency**: Use native filesystem hardlinks (`--link-dest`) and SATA differential network intercepting (`--copy-dest` cache-forwarding) instead of pulling immutable files iteratively.

## Strengths and Weaknesses
**Strong Points:**
- Extremely transparent underlying data structure (directories instead of proprietary binary chunks).
- Space efficient! Running 5 sessions across the same day costs no more physical disk space than a single session (given files aren't changed) due to native hardlinking.
- Incredibly fast Dual-Drive caching (`rsync --copy-dest`) that clones unaltered data across a fresh SATA drive locally rather than routing back up across your internet layer.
- Handles parallel, concurrent remote endpoints efficiently allowing unresponsive SSH connections to comfortably timeout off the primary processing thread.

**Weak Points:**
- If you use a non-POSIX capable filesystem (e.g. `FAT32` or `exFAT`) as the destination media, hardlinks physically cannot be created, obliterating the spatial optimizations and converting every snapshot into an exhaustive raw file-copy.
- The `IndexStore` lives strictly on the execution machine's `~/.local/share/bekusup/` folder. This means `bekusup` tracks disk trust perfectly locally, but pulling the rotated backups to an entirely foreign server won't transfer its rotation history organically without manual indexing.

## Limitations
- Relies directly on `rsync` being available efficiently on the remote nodes (though an `scp` base fallback triggers if configured).
- Not designed to resolve mid-transfer block corruptions iteratively; it assumes standard `rsync -aH` executions succeed if their subprocesses emit standard zero-exits.

## Installation
`bekusup` depends strictly on Python 3 and basic OS `util-linux` primitives (`lsblk`, `mount`). 

1. Ensure standard networking tools are installed globally:
```bash
sudo apt install rsync sshpass util-linux
```
2. Inject the bare-bones Python dependencies in your target environment:
```bash
git clone <repo-url> bekusup
cd bekusup
pip install -r requirements.txt
```

## Workflows and Variations

### 1. The Single Swap Rotation
The traditional workflow:
1. Every Sunday, you physically un-plug **Disk A** from the USB/SATA dock.
2. You insert **Disk B** (which contains last week's snapshots).
3. If not enrolled, you run `./bekusup-cli.sh enroll`.
4. You run `./bekusup-cli.sh run` manually or via a basic cron task. The tool naturally locates the drive's previous session, builds a hardlink tree over it, and queries your target hosts. 

### 2. The Dual-Drive Smartass Optimization
You own a 2-bay NAS structure, or use both drives simultaneously.
1. You slot in yesterday's drive (**Disk A**).
2. You slot in the totally fresh, newly enrolled drive out of your 42-drive box (**Disk B**).
3. Both disks match the internal label identifiers.
4. When `bekusup run` is fired, the engine internally sequences the drives by 'Freshness' (consulting the index).
5. **Disk A** is backed-up first. It uses its own snapshots via `--link-dest` saving immediate space.
6. **Disk B** is backed-up second. Identifying that it does not possess a viable recent snapshot, but `Disk A` literally just successfully created one natively, it flips arguments across block devices! `rsync` utilizes `--copy-dest=/mnt/disk_A/sessions/today` fetching the updated directory trees straight from your local dock, hitting 0 bytes of network transit. 

## CLI Usage

| Command | Action |
|---|---|
| `scan` | Evaluates `/dev/` topologies reporting connected matches containing the `label` parameter mapped in config alongside their internal `Trust/Enrolled` configurations. |
| `enroll` | Evaluates a single connected, label-matched target placing `.bekusup-volume.json` physically on it and synchronizing its Serial parameter securely inside `IndexStore`. |
| `run` | Initiates the backup rotation loop synchronously across all hosts. | 

```bash
# Example syntax wrapper execution
./bekusup-cli.sh --config ./config.yaml scan
```
