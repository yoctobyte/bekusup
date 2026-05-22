#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME=$(basename "$0")
MOUNT_ROOT="/mnt/disk-reclaim"
TEST_FILE_SIZE_MB=512
ASSUME_YES=0
SKIP_SMART=0
SKIP_BENCH=0
FORCE=0
LABEL_PREFIX="backupdisk"
LABEL_PREFIX_SET=0
FS_TYPE="ext4"

cleanup() {
    local rc=$?

    if mountpoint -q "${TMP_MOUNTPOINT:-}" 2>/dev/null; then
        umount "${TMP_MOUNTPOINT}"
    fi

    if [[ -n "${TMP_MOUNTPOINT:-}" && -d "${TMP_MOUNTPOINT}" ]]; then
        rmdir "${TMP_MOUNTPOINT}" 2>/dev/null || true
    fi

    exit "$rc"
}

trap cleanup EXIT

usage() {
    cat <<EOF
Usage: sudo $SCRIPT_NAME [options] /dev/sdX

Reclaims an old HDD after a safety preflight:
  - checks device existence and that it is a whole disk
  - verifies it appears to be rotational media
  - checks for existing partitions, filesystems, and signatures
  - reports SMART health when smartctl is available
  - asks for confirmation before destructive actions
  - removes stale mdraid metadata and on-disk signatures
  - creates GPT + one filesystem partition across the whole disk
  - runs a simple write/read smoke test on the new filesystem

Options:
  -y, --yes              Proceed without interactive confirmation
      --force            Skip RAID / existing-partition safety checks and re-roll
                         the disk anyway (requires two extra confirmations unless
                         combined with -y)
      --skip-smart       Skip SMART health checks
      --skip-bench       Skip post-format write/read smoke test
      --fs TYPE          Filesystem to create: ext4, xfs, btrfs (default: ext4)
      --label-prefix P   Filesystem label prefix (default: backupdisk; the
                         default appends a random number, an explicit prefix
                         appends the disk serial). The label is offered as a
                         default and can be overridden interactively (skipped
                         under -y/--yes).
  -h, --help             Show this help
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%F %T')" "$*"
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

require_root() {
    [[ $EUID -eq 0 ]] || die "run as root"
}

require_cmd() {
    local missing=()
    local cmd
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    [[ ${#missing[@]} -eq 0 ]] || die "missing required command(s): ${missing[*]}"
}

extract_dd_rate() {
    awk -F, '/copied/ {gsub(/^ +| +$/, "", $3); print $3}' | tail -n1
}

yesno() {
    local prompt=$1
    local reply
    read -r -p "$prompt [yes/NO]: " reply
    [[ "$reply" == "yes" ]]
}

yesno_default_yes() {
    local prompt=$1
    local reply
    read -r -p "$prompt [Y/n]: " reply
    [[ -z "$reply" || "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

human_bytes() {
    local bytes=$1
    numfmt --to=iec-i --suffix=B "$bytes"
}

get_sysfs_value() {
    local path=$1
    [[ -r "$path" ]] && cat "$path" || true
}

parse_args() {
    DEVICE=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -y|--yes)
                ASSUME_YES=1
                ;;
            --force)
                FORCE=1
                ;;
            --skip-smart)
                SKIP_SMART=1
                ;;
            --skip-bench)
                SKIP_BENCH=1
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
                LABEL_PREFIX_SET=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            /dev/*)
                [[ -z "${DEVICE:-}" ]] || die "specify only one device"
                DEVICE=$1
                ;;
            *)
                die "unknown argument: $1"
                ;;
        esac
        shift
    done

    [[ -n "${DEVICE:-}" ]] || die "missing target device"

    case "$FS_TYPE" in
        ext4|xfs|btrfs)
            ;;
        *)
            die "--fs must be one of: ext4, xfs, btrfs"
            ;;
    esac
}

collect_facts() {
    [[ -b "$DEVICE" ]] || die "$DEVICE is not a block device"

    DISK_TYPE=$(lsblk -dn -o TYPE "$DEVICE" 2>/dev/null | head -n1 | tr -d '[:space:]')
    [[ "$DISK_TYPE" == "disk" ]] || die "$DEVICE is not a whole-disk block device"

    KNAME=$(lsblk -dn -o KNAME "$DEVICE")
    MODEL=$(lsblk -dn -o MODEL "$DEVICE" | sed 's/[[:space:]]\+$//')
    SERIAL=$(lsblk -dn -o SERIAL "$DEVICE" | sed 's/[[:space:]]\+$//')
    TRANSPORT=$(lsblk -dn -o TRAN "$DEVICE" | sed 's/[[:space:]]\+$//')
    SIZE_BYTES=$(blockdev --getsize64 "$DEVICE")
    SIZE_HUMAN=$(human_bytes "$SIZE_BYTES")
    SECTOR_SIZE=$(blockdev --getss "$DEVICE")
    PHYS_SECTOR_SIZE=$(blockdev --getpbsz "$DEVICE")
    ROTA=$(lsblk -dn -o ROTA "$DEVICE" | tr -d '[:space:]')
    RM=$(lsblk -dn -o RM "$DEVICE" | tr -d '[:space:]')
    STATE=$(lsblk -dn -o STATE "$DEVICE" | sed 's/[[:space:]]\+$//')
    WWN=$(lsblk -dn -o WWN "$DEVICE" | sed 's/[[:space:]]\+$//')
    VENDOR=$(lsblk -dn -o VENDOR "$DEVICE" | sed 's/[[:space:]]\+$//')
    PART_COUNT=$(lsblk -ln -o TYPE "$DEVICE" | awk '$1 == "part" {count++} END {print count+0}')
    CHILDREN=$(lsblk -ln -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINT "$DEVICE")
    BLKID_OUTPUT=$(blkid -p "$DEVICE" 2>/dev/null || true)
    WIPEFS_OUTPUT=$(wipefs -n "$DEVICE" 2>/dev/null || true)
    PARTED_OUTPUT=$(parted -s "$DEVICE" print 2>&1 || true)
    MD_EXAMINE_OUTPUT=""
    SYS_ROTATIONAL=$(get_sysfs_value "/sys/block/$KNAME/queue/rotational")

    if command -v mdadm >/dev/null 2>&1; then
        MD_EXAMINE_OUTPUT=$(mdadm --examine "$DEVICE" 2>&1 || true)
    fi

    HAS_RAID_SIG=0
    HAS_NONRAID_SIG=0

    if grep -qi 'linux_raid_member' <<<"$BLKID_OUTPUT$WIPEFS_OUTPUT$MD_EXAMINE_OUTPUT"; then
        HAS_RAID_SIG=1
    fi

    if grep -qiE 'ext[234]|xfs|btrfs|ntfs|vfat|exfat|swap|crypto|LVM2_member|zfs_member|gpt|dos|bsd|sun' <<<"$BLKID_OUTPUT$WIPEFS_OUTPUT$PARTED_OUTPUT"; then
        HAS_NONRAID_SIG=1
    fi
}

run_smart() {
    SMART_SUMMARY="not run"
    SMART_FULL=""
    SMART_HEALTH=""

    if [[ $SKIP_SMART -eq 1 ]]; then
        SMART_SUMMARY="skipped"
        return
    fi

    if ! command -v smartctl >/dev/null 2>&1; then
        SMART_SUMMARY="smartctl not installed"
        return
    fi

    SMART_FULL=$(smartctl -H -A "$DEVICE" 2>&1 || true)

    if grep -qE 'SMART overall-health self-assessment test result: PASSED|SMART Health Status: OK' <<<"$SMART_FULL"; then
        SMART_HEALTH="PASSED"
    elif grep -qE 'FAILED|BAD' <<<"$SMART_FULL"; then
        SMART_HEALTH="FAILED"
    else
        SMART_HEALTH="UNKNOWN"
    fi

    SMART_SUMMARY=$SMART_HEALTH
}

validate_safety() {
    [[ "$ROTA" == "1" || "$SYS_ROTATIONAL" == "1" ]] || die "$DEVICE does not appear to be a rotational HDD"
    [[ "$RM" != "1" ]] || die "$DEVICE is marked removable; refusing"

    if [[ $FORCE -eq 1 ]]; then
        if [[ "$PART_COUNT" -gt 0 || "$HAS_NONRAID_SIG" -eq 1 || "$HAS_RAID_SIG" -eq 1 ]]; then
            log "--force: ignoring existing partitions / RAID / filesystem signatures on $DEVICE"
        fi
    else
        [[ "$PART_COUNT" -eq 0 ]] || die "$DEVICE already has partition(s) (use --force to override)"
        [[ "$HAS_NONRAID_SIG" -eq 0 ]] || die "$DEVICE has a non-RAID filesystem or partition-table signature (use --force to override)"
    fi

    if [[ "$SMART_HEALTH" == "FAILED" ]]; then
        die "SMART reports a failure"
    fi
}

print_report() {
    cat <<EOF
Preflight report for $DEVICE
  Model:            ${MODEL:-unknown}
  Vendor:           ${VENDOR:-unknown}
  Serial:           ${SERIAL:-unknown}
  WWN:              ${WWN:-unknown}
  Transport:        ${TRANSPORT:-unknown}
  Size:             $SIZE_HUMAN ($SIZE_BYTES bytes)
  Logical sector:   $SECTOR_SIZE
  Physical sector:  $PHYS_SECTOR_SIZE
  Rotational:       ${ROTA:-unknown}
  Sysfs rotational: ${SYS_ROTATIONAL:-unknown}
  Removable:        ${RM:-unknown}
  Kernel state:     ${STATE:-unknown}
  Existing parts:   $PART_COUNT
  Target filesystem: $FS_TYPE
  SMART summary:    $SMART_SUMMARY
  Stale mdraid sig: $([[ "$HAS_RAID_SIG" -eq 1 ]] && echo yes || echo no)

lsblk:
$CHILDREN

blkid -p:
${BLKID_OUTPUT:-<none>}

wipefs -n:
${WIPEFS_OUTPUT:-<none>}

parted print:
${PARTED_OUTPUT:-<none>}

mdadm --examine:
${MD_EXAMINE_OUTPUT:-<not available or no metadata>}
EOF

    if [[ -n "$SMART_FULL" ]]; then
        echo
        echo "SMART excerpt:"
        echo "$SMART_FULL" | grep -E 'SMART overall-health|SMART Health Status|Reallocated_Sector_Ct|Current_Pending_Sector|Offline_Uncorrectable|Power_On_Hours|Raw_Read_Error_Rate|Seek_Error_Rate' || true
    fi
}

confirm_or_exit() {
    if [[ $ASSUME_YES -eq 1 ]]; then
        return
    fi

    echo
    echo "This will destroy any stale metadata on $DEVICE and create a new GPT + $FS_TYPE filesystem."
    yesno "Proceed with destructive actions on $DEVICE?" || die "aborted by user"

    if [[ $FORCE -eq 1 ]]; then
        echo
        echo "WARNING: --force is set. Existing partitions, filesystems, or RAID metadata"
        echo "on $DEVICE will be wiped without the usual safety checks."
        yesno "Are you SURE you want to force-wipe $DEVICE?" || die "aborted by user"
        echo
        echo "Last chance. ${MODEL:-unknown model} serial ${SERIAL:-unknown} ($SIZE_HUMAN) will be erased."
        yesno "Really proceed and re-roll $DEVICE?" || die "aborted by user"
    fi
}

zap_metadata() {
    log "Clearing signatures and stale RAID metadata on $DEVICE"

    if command -v mdadm >/dev/null 2>&1; then
        mdadm --zero-superblock --force "$DEVICE" >/dev/null 2>&1 || true
    fi

    wipefs -a "$DEVICE"
    dd if=/dev/zero of="$DEVICE" bs=1M count=16 conv=fsync status=progress
    blockdev --rereadpt "$DEVICE" || true
    udevadm settle
}

partition_disk() {
    log "Creating GPT and one primary partition on $DEVICE"
    parted -s -a optimal "$DEVICE" mklabel gpt
    parted -s -a optimal "$DEVICE" mkpart primary 1MiB 100%
    blockdev --rereadpt "$DEVICE" || true
    udevadm settle

    PARTITION=$(lsblk -lnpo NAME,TYPE "$DEVICE" | awk '$2 == "part" {print $1; exit}')
    [[ -n "${PARTITION:-}" ]] || die "failed to detect newly created partition"
}

label_max_length() {
    case "$FS_TYPE" in
        xfs)
            echo 12
            ;;
        ext4)
            echo 16
            ;;
        btrfs)
            echo 256
            ;;
    esac
}

make_fs_label() {
    local serial_tag
    local cleaned_prefix
    local max_len
    local rand_tag

    cleaned_prefix=$(tr -cd '[:alnum:]_.-' <<<"$LABEL_PREFIX")
    [[ -n "$cleaned_prefix" ]] || cleaned_prefix="backupdisk"

    if [[ $LABEL_PREFIX_SET -eq 0 ]]; then
        rand_tag=$(printf '%05d' $((RANDOM % 100000)))
        FS_LABEL="${cleaned_prefix}${rand_tag}"
    else
        serial_tag=$(tr -cd '[:alnum:]' <<<"${SERIAL:-$KNAME}" | tail -c 8)
        FS_LABEL="${cleaned_prefix}-${serial_tag:-$KNAME}"
    fi

    max_len=$(label_max_length)
    FS_LABEL=${FS_LABEL:0:max_len}
}

prompt_for_label() {
    [[ $ASSUME_YES -eq 1 ]] && return

    local input cleaned max_len
    max_len=$(label_max_length)

    while true; do
        echo
        read -r -p "Filesystem label [$FS_LABEL]: " input
        if [[ -n "$input" ]]; then
            cleaned=$(tr -cd '[:alnum:]_.-' <<<"$input")
            if [[ -z "$cleaned" ]]; then
                echo "Label must contain at least one of [A-Za-z0-9_.-]."
                continue
            fi
            if [[ ${#cleaned} -gt $max_len ]]; then
                echo "Label too long for $FS_TYPE (max $max_len chars)."
                continue
            fi
            FS_LABEL=$cleaned
        fi
        yesno "Use filesystem label '$FS_LABEL'?" && break
    done
}

format_partition() {
    make_fs_label
    prompt_for_label

    log "Formatting $PARTITION as $FS_TYPE (label: $FS_LABEL)"
    case "$FS_TYPE" in
        ext4)
            mkfs.ext4 -F -L "$FS_LABEL" -m 0 -E lazy_itable_init=0,lazy_journal_init=0 "$PARTITION"
            ;;
        xfs)
            mkfs.xfs -f -m reflink=1 -L "$FS_LABEL" "$PARTITION"
            ;;
        btrfs)
            mkfs.btrfs -f -L "$FS_LABEL" "$PARTITION"
            ;;
    esac

    udevadm settle
}

run_bench() {
    [[ $SKIP_BENCH -eq 0 ]] || return

    TMP_MOUNTPOINT="$MOUNT_ROOT/$KNAME"
    mkdir -p "$TMP_MOUNTPOINT"

    log "Mounting $PARTITION on $TMP_MOUNTPOINT for smoke test"
    mount "$PARTITION" "$TMP_MOUNTPOINT"

    TEST_FILE="$TMP_MOUNTPOINT/io-smoke.bin"
    log "Writing ${TEST_FILE_SIZE_MB}MiB test file"
    WRITE_MBPS=$(dd if=/dev/zero of="$TEST_FILE" bs=1M count="$TEST_FILE_SIZE_MB" conv=fdatasync status=progress 2>&1 | extract_dd_rate)
    sync

    READ_MBPS=$(dd if="$TEST_FILE" of=/dev/null bs=4M iflag=direct status=progress 2>&1 | extract_dd_rate || true)
    if [[ -z "${READ_MBPS:-}" ]]; then
        READ_MBPS=$(dd if="$TEST_FILE" of=/dev/null bs=4M status=progress 2>&1 | extract_dd_rate)
    fi

    log "Filesystem check on mounted volume"
    touch "$TMP_MOUNTPOINT/.rw-ok"
    ls -l "$TMP_MOUNTPOINT/.rw-ok" >/dev/null

    rm -f "$TEST_FILE" "$TMP_MOUNTPOINT/.rw-ok"
    sync
    umount "$TMP_MOUNTPOINT"
    rmdir "$TMP_MOUNTPOINT"
    TMP_MOUNTPOINT=""
}

maybe_save_smart_log() {
    if ! command -v smartctl >/dev/null 2>&1; then
        return
    fi

    local script_dir logger
    script_dir=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
    logger="$script_dir/smart_log.sh"

    if [[ ! -x "$logger" ]]; then
        log "smart_log.sh not found at $logger; skipping SMART log archive"
        return
    fi

    if [[ $ASSUME_YES -eq 0 ]]; then
        echo
        yesno_default_yes "Save SMART log for $DEVICE for later comparison?" || return
    fi

    "$logger" --tag reclaim "$DEVICE" || log "warning: smart_log.sh failed for $DEVICE"
}

print_summary() {
    local final_blkid
    final_blkid=$(blkid "$PARTITION" 2>/dev/null || true)

    cat <<EOF

Completed successfully
  Disk:             $DEVICE
  Partition:        $PARTITION
  Filesystem:       $FS_TYPE
  Filesystem label: $FS_LABEL
  blkid:            ${final_blkid:-unknown}
EOF

    if [[ $SKIP_BENCH -eq 0 ]]; then
        cat <<EOF
  Write test:       ${WRITE_MBPS:-unavailable}
  Read test:        ${READ_MBPS:-unavailable}
EOF
    fi
}

main() {
    parse_args "$@"
    require_root
    require_cmd lsblk blkid wipefs parted dd blockdev udevadm numfmt awk sed mount umount mountpoint mkdir rmdir

    case "$FS_TYPE" in
        ext4)
            require_cmd mkfs.ext4
            ;;
        xfs)
            require_cmd mkfs.xfs
            ;;
        btrfs)
            require_cmd mkfs.btrfs
            ;;
    esac

    collect_facts
    run_smart
    print_report
    validate_safety
    confirm_or_exit
    zap_metadata
    partition_disk
    format_partition
    run_bench
    maybe_save_smart_log
    print_summary
}

main "$@"
