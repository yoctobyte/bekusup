#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME=$(basename "$0")
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)

REMOTE_HOST=""
REMOTE_REPO="$REPO_ROOT"
CONFIG="config.yaml"
DEVICE=""
DETECT_ONE=0
RECLAIM=0
ALLOW_DESTRUCTIVE=0
FORCE_RECLAIM=0
ENROLL=1
RUN_BACKUP=0
FS_TYPE="ext4"
LABEL_PREFIX="backupdisk"
SKIP_BENCH=0
ASSUME_YES=0

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [options]

Operator wrapper for a freshly inserted docked backup disk. It can:
  1. select a target disk, either explicitly or by detecting exactly one
     plausible non-system rotational docked disk
  2. optionally reclaim/format it through disk_reclaim.sh
  3. enroll its new partition with Bekusup
  4. optionally run a real Bekusup backup

Local example:
  sudo tools/disk/$SCRIPT_NAME --device /dev/sdb --reclaim \\
    --allow-destructive --enroll --run --yes --config ./config.yaml

Remote example:
  tools/disk/$SCRIPT_NAME --remote borg --repo /home/rene/bekusup \\
    --detect-one --reclaim --allow-destructive --enroll --run --yes

Options:
      --remote HOST            Run the whole operation on HOST over SSH
      --repo PATH              Bekusup checkout path on the target host
                               (default: this repo path)
  -c, --config PATH            Bekusup config path on the target host
                               (default: config.yaml)
      --device /dev/sdX        Target whole disk. Required unless --detect-one
      --detect-one             Auto-select only if exactly one safe-looking
                               docked HDD candidate exists
      --reclaim                Format/reclaim the whole target disk first
      --allow-destructive      Required with --reclaim; acknowledges data loss
      --force-reclaim          Pass --force to disk_reclaim.sh
      --enroll                 Enroll with Bekusup after selection/reclaim
                               (default)
      --no-enroll              Skip Bekusup enrollment after selection/reclaim
      --run                    Run bekusup after enrollment
      --no-run                 Do not run bekusup after enrollment
      --fs TYPE                Filesystem for reclaim: ext4, xfs, btrfs
                               (default: ext4)
      --label-prefix PREFIX    Label prefix for reclaim (default: backupdisk)
      --skip-bench             Skip disk_reclaim.sh smoke benchmark
  -y, --yes                    Non-interactive mode for underlying tools
  -h, --help                   Show this help

Safety notes:
  - --reclaim destroys data on the selected whole disk.
  - auto-detection refuses if zero or multiple candidates are found.
  - this script does not pass --force unless --force-reclaim is set.
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

shell_quote() {
    printf '%q' "$1"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --remote)
                shift
                [[ $# -gt 0 ]] || die "--remote requires a value"
                REMOTE_HOST=$1
                ;;
            --repo)
                shift
                [[ $# -gt 0 ]] || die "--repo requires a value"
                REMOTE_REPO=$1
                ;;
            -c|--config)
                shift
                [[ $# -gt 0 ]] || die "--config requires a value"
                CONFIG=$1
                ;;
            --device)
                shift
                [[ $# -gt 0 ]] || die "--device requires a value"
                DEVICE=$1
                ;;
            --detect-one)
                DETECT_ONE=1
                ;;
            --reclaim)
                RECLAIM=1
                ;;
            --allow-destructive)
                ALLOW_DESTRUCTIVE=1
                ;;
            --force-reclaim)
                FORCE_RECLAIM=1
                ;;
            --enroll)
                ENROLL=1
                ;;
            --no-enroll)
                ENROLL=0
                ;;
            --run)
                RUN_BACKUP=1
                ;;
            --no-run)
                RUN_BACKUP=0
                ;;
            --fs)
                shift
                [[ $# -gt 0 ]] || die "--fs requires a value"
                FS_TYPE=${1,,}
                ;;
            --label-prefix)
                shift
                [[ $# -gt 0 ]] || die "--label-prefix requires a value"
                LABEL_PREFIX=$1
                ;;
            --skip-bench)
                SKIP_BENCH=1
                ;;
            -y|--yes)
                ASSUME_YES=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "unknown argument: $1"
                ;;
        esac
        shift
    done

    if [[ -n "$DEVICE" && $DETECT_ONE -eq 1 ]]; then
        die "--device and --detect-one are mutually exclusive"
    fi
    if [[ -z "$DEVICE" && $DETECT_ONE -eq 0 ]]; then
        die "specify --device /dev/sdX or --detect-one"
    fi
    if [[ $RECLAIM -eq 1 && $ALLOW_DESTRUCTIVE -ne 1 ]]; then
        die "--reclaim requires --allow-destructive"
    fi
    case "$FS_TYPE" in
        ext4|xfs|btrfs)
            ;;
        *)
            die "--fs must be one of: ext4, xfs, btrfs"
            ;;
    esac
}

target_script() {
    cat <<'EOS'
set -Eeuo pipefail

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

select_detected_disk() {
    local candidates
    candidates=$(
        lsblk -J -b -o NAME,PATH,TYPE,TRAN,ROTA,RM,RO,MOUNTPOINTS,SIZE,MODEL,SERIAL |
        python3 -c '
import json
import sys

data = json.load(sys.stdin)

def has_system_mount(node):
    mounts = node.get("mountpoints") or []
    if node.get("mountpoint"):
        mounts.append(node["mountpoint"])
    for mount in mounts:
        if not mount:
            continue
        if mount in {"/", "/boot", "/boot/efi"}:
            return True
        if str(mount).startswith(("/var", "/usr", "/home", "/sys", "/proc")):
            return True
    return any(has_system_mount(child) for child in node.get("children", []) or [])

def has_mounted_children(node):
    mounts = node.get("mountpoints") or []
    if node.get("mountpoint"):
        mounts.append(node["mountpoint"])
    if any(mount for mount in mounts):
        return True
    return any(has_mounted_children(child) for child in node.get("children", []) or [])

for node in data.get("blockdevices", []):
    name = str(node.get("name") or "")
    path = node.get("path") or f"/dev/{name}"
    if node.get("type") != "disk":
        continue
    if name.startswith(("loop", "ram", "zram", "fd", "sr")):
        continue
    if int(node.get("ro") or 0) != 0:
        continue
    if int(node.get("rm") or 0) != 0:
        continue
    if int(node.get("rota") or 0) != 1:
        continue
    if has_system_mount(node):
        continue
    if has_mounted_children(node):
        continue
    size = int(node.get("size") or 0)
    if size < 100 * 1000 * 1000 * 1000:
        continue
    print(path)
'
    )

    mapfile -t CANDIDATES <<<"$candidates"
    if [[ ${#CANDIDATES[@]} -ne 1 || -z "${CANDIDATES[0]:-}" ]]; then
        printf 'Candidate disks:\n' >&2
        printf '  %s\n' "${CANDIDATES[@]}" >&2
        die "expected exactly one safe-looking docked HDD candidate; specify --device"
    fi
    DEVICE="${CANDIDATES[0]}"
}

find_first_partition() {
    lsblk -lnpo NAME,TYPE "$DEVICE" | awk '$2 == "part" {print $1; exit}'
}

[[ -d "$REPO" ]] || die "repo path does not exist: $REPO"
cd "$REPO"

if [[ -z "$DEVICE" ]]; then
    select_detected_disk
fi

[[ -b "$DEVICE" ]] || die "$DEVICE is not a block device"
log "Selected target disk: $DEVICE"

if [[ "$RECLAIM" == "1" ]]; then
    cmd=(sudo "$REPO/tools/disk/disk_reclaim.sh" --fs "$FS_TYPE" --label-prefix "$LABEL_PREFIX")
    [[ "$ASSUME_YES" == "1" ]] && cmd+=(--yes)
    [[ "$FORCE_RECLAIM" == "1" ]] && cmd+=(--force)
    [[ "$SKIP_BENCH" == "1" ]] && cmd+=(--skip-bench)
    cmd+=("$DEVICE")
    log "Running disk reclaim"
    "${cmd[@]}"
fi

PARTITION=$(find_first_partition)
[[ -n "$PARTITION" ]] || die "no partition found on $DEVICE after selection/reclaim"
log "Using partition for Bekusup: $PARTITION"

if [[ "$ENROLL" == "1" ]]; then
    log "Enrolling partition with Bekusup"
    python3 -m bekusup.cli --config "$CONFIG" enroll "$PARTITION"
fi

if [[ "$RUN_BACKUP" == "1" ]]; then
    log "Running Bekusup backup"
    python3 -m bekusup.cli --config "$CONFIG" --no-dry-run run
fi
EOS
}

main() {
    parse_args "$@"

    local remote_cmd
    remote_cmd=$(
        printf 'REPO=%s\n' "$(shell_quote "$REMOTE_REPO")"
        printf 'CONFIG=%s\n' "$(shell_quote "$CONFIG")"
        printf 'DEVICE=%s\n' "$(shell_quote "$DEVICE")"
        printf 'RECLAIM=%s\n' "$RECLAIM"
        printf 'ASSUME_YES=%s\n' "$ASSUME_YES"
        printf 'FORCE_RECLAIM=%s\n' "$FORCE_RECLAIM"
        printf 'ENROLL=%s\n' "$ENROLL"
        printf 'RUN_BACKUP=%s\n' "$RUN_BACKUP"
        printf 'FS_TYPE=%s\n' "$(shell_quote "$FS_TYPE")"
        printf 'LABEL_PREFIX=%s\n' "$(shell_quote "$LABEL_PREFIX")"
        printf 'SKIP_BENCH=%s\n' "$SKIP_BENCH"
        target_script
    )

    if [[ -n "$REMOTE_HOST" ]]; then
        log "Running auto-proceed on remote host: $REMOTE_HOST"
        ssh -t "$REMOTE_HOST" "bash -s" <<<"$remote_cmd"
    else
        log "Running auto-proceed locally"
        bash -s <<<"$remote_cmd"
    fi
}

main "$@"
