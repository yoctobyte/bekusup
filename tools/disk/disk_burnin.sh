#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME=$(basename "$0")
MOUNT_ROOT="/mnt/disk-burnin"
DURATION_SEC=300
FILE_SIZE_GB=4
ASSUME_YES=0
KEEP_FILE=0
DROP_CACHES=1

cleanup() {
    local rc=$?

    if [[ -n "${TEST_FILE:-}" && -f "${TEST_FILE}" && $KEEP_FILE -eq 0 ]]; then
        rm -f "${TEST_FILE}" 2>/dev/null || true
    fi

    if [[ -n "${TEMP_MOUNTPOINT:-}" ]] && mountpoint -q "${TEMP_MOUNTPOINT}" 2>/dev/null; then
        umount "${TEMP_MOUNTPOINT}" || true
    fi

    if [[ -n "${TEMP_MOUNTPOINT:-}" && -d "${TEMP_MOUNTPOINT}" ]]; then
        rmdir "${TEMP_MOUNTPOINT}" 2>/dev/null || true
    fi

    exit "$rc"
}

trap cleanup EXIT

usage() {
    cat <<EOF
Usage: sudo $SCRIPT_NAME [options] <mountpoint|/dev/sdXN>

Runs a prolonged read/write smoke test against a filesystem:
  - accepts either a mounted directory or a block-device partition
  - writes a large temporary file, syncs it, then reads it back
  - repeats until the configured duration elapses
  - prints per-pass and final throughput totals

Options:
      --duration SEC     Total test time in seconds (default: 300)
      --size-gb N        Test file size in GiB (default: 4)
      --keep-file        Keep the temporary test file
      --no-drop-caches   Do not drop Linux page cache before reads
  -y, --yes              Proceed without interactive confirmation
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

yesno() {
    local prompt=$1
    local reply
    read -r -p "$prompt [yes/NO]: " reply
    [[ "$reply" == "yes" ]]
}

human_bytes() {
    numfmt --to=iec-i --suffix=B "$1"
}

extract_dd_rate() {
    awk -F, '/copied/ {gsub(/^ +| +$/, "", $3); print $3}' | tail -n1
}

parse_args() {
    TARGET=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --duration)
                shift
                [[ $# -gt 0 ]] || die "--duration requires a value"
                DURATION_SEC=$1
                ;;
            --size-gb)
                shift
                [[ $# -gt 0 ]] || die "--size-gb requires a value"
                FILE_SIZE_GB=$1
                ;;
            --keep-file)
                KEEP_FILE=1
                ;;
            --no-drop-caches)
                DROP_CACHES=0
                ;;
            -y|--yes)
                ASSUME_YES=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                [[ -z "$TARGET" ]] || die "specify only one target"
                TARGET=$1
                ;;
        esac
        shift
    done

    [[ -n "$TARGET" ]] || die "missing target"
    [[ "$DURATION_SEC" =~ ^[0-9]+$ ]] || die "--duration must be an integer"
    [[ "$FILE_SIZE_GB" =~ ^[0-9]+$ ]] || die "--size-gb must be an integer"
    (( DURATION_SEC > 0 )) || die "--duration must be greater than zero"
    (( FILE_SIZE_GB > 0 )) || die "--size-gb must be greater than zero"
}

resolve_target() {
    if [[ -d "$TARGET" ]]; then
        MOUNTPOINT=$(realpath "$TARGET")
        SOURCE_DEVICE=$(findmnt -n -o SOURCE --target "$MOUNTPOINT" 2>/dev/null || true)
        FSTYPE=$(findmnt -n -o FSTYPE --target "$MOUNTPOINT" 2>/dev/null || true)
        [[ -n "$SOURCE_DEVICE" ]] || die "$MOUNTPOINT is not a mounted filesystem"
        TARGET_DESC="$MOUNTPOINT"
        return
    fi

    [[ -b "$TARGET" ]] || die "$TARGET is neither a mounted directory nor a block device"

    DEVICE_TYPE=$(lsblk -dn -o TYPE "$TARGET" 2>/dev/null | tr -d '[:space:]')
    [[ "$DEVICE_TYPE" == "part" ]] || die "block-device targets must be partitions, not whole disks"

    EXISTING_MOUNT=$(lsblk -dn -o MOUNTPOINT "$TARGET" | sed 's/[[:space:]]\+$//')
    FSTYPE=$(lsblk -dn -o FSTYPE "$TARGET" | sed 's/[[:space:]]\+$//')
    SOURCE_DEVICE=$TARGET

    [[ -n "$FSTYPE" ]] || die "$TARGET does not appear to contain a filesystem"

    if [[ -n "$EXISTING_MOUNT" ]]; then
        MOUNTPOINT="$EXISTING_MOUNT"
    else
        TEMP_MOUNTPOINT="$MOUNT_ROOT/$(basename "$TARGET")"
        mkdir -p "$TEMP_MOUNTPOINT"
        mount "$TARGET" "$TEMP_MOUNTPOINT"
        MOUNTPOINT="$TEMP_MOUNTPOINT"
    fi

    TARGET_DESC="$TARGET"
}

collect_facts() {
    TOTAL_BYTES=$(findmnt -b -n -o SIZE --target "$MOUNTPOINT")
    FREE_BYTES=$(findmnt -b -n -o AVAIL --target "$MOUNTPOINT")
    USED_BYTES=$(findmnt -b -n -o USED --target "$MOUNTPOINT")
    FILE_SIZE_BYTES=$(( FILE_SIZE_GB * 1024 * 1024 * 1024 ))

    [[ "$FREE_BYTES" =~ ^[0-9]+$ ]] || die "could not determine free space for $MOUNTPOINT"
    (( FILE_SIZE_BYTES < FREE_BYTES )) || die "not enough free space on $MOUNTPOINT for a ${FILE_SIZE_GB}GiB test file"

    KNAME=$(lsblk -no PKNAME "$SOURCE_DEVICE" 2>/dev/null | head -n1)
    if [[ -z "$KNAME" ]]; then
        KNAME=$(basename "$SOURCE_DEVICE")
    fi

    MODEL=$(lsblk -dn -o MODEL "/dev/$KNAME" 2>/dev/null | sed 's/[[:space:]]\+$//')
    SERIAL=$(lsblk -dn -o SERIAL "/dev/$KNAME" 2>/dev/null | sed 's/[[:space:]]\+$//')
    ROTA=$(lsblk -dn -o ROTA "/dev/$KNAME" 2>/dev/null | tr -d '[:space:]')
    PHY_SEC=$(lsblk -dn -o PHY-SEC "/dev/$KNAME" 2>/dev/null | tr -d '[:space:]')
    LOG_SEC=$(lsblk -dn -o LOG-SEC "/dev/$KNAME" 2>/dev/null | tr -d '[:space:]')
    MIN_IO=$(lsblk -dn -o MIN-IO "/dev/$KNAME" 2>/dev/null | tr -d '[:space:]')
    OPT_IO=$(lsblk -dn -o OPT-IO "/dev/$KNAME" 2>/dev/null | tr -d '[:space:]')
    TEST_FILE="$MOUNTPOINT/.disk-burnin-${HOSTNAME:-host}-$$.bin"
}

print_report() {
    cat <<EOF
Burn-in report
  Target:           $TARGET_DESC
  Source device:    ${SOURCE_DEVICE:-unknown}
  Mountpoint:       $MOUNTPOINT
  Filesystem:       ${FSTYPE:-unknown}
  Disk model:       ${MODEL:-unknown}
  Disk serial:      ${SERIAL:-unknown}
  Rotational:       ${ROTA:-unknown}
  Logical sector:   ${LOG_SEC:-unknown}
  Physical sector:  ${PHY_SEC:-unknown}
  Minimum I/O:      ${MIN_IO:-unknown}
  Optimal I/O:      ${OPT_IO:-unknown}
  Filesystem size:  $(human_bytes "$TOTAL_BYTES")
  Used:             $(human_bytes "$USED_BYTES")
  Free:             $(human_bytes "$FREE_BYTES")
  Test file size:   $(human_bytes "$FILE_SIZE_BYTES")
  Duration:         ${DURATION_SEC}s
  Drop caches:      $([[ $DROP_CACHES -eq 1 ]] && echo yes || echo no)
  Temp file:        $TEST_FILE
EOF
}

confirm_or_exit() {
    if [[ $ASSUME_YES -eq 1 ]]; then
        return
    fi

    echo
    echo "This will repeatedly write and read a large temporary file on $MOUNTPOINT."
    yesno "Proceed with burn-in test?" || die "aborted by user"
}

drop_caches_if_enabled() {
    [[ $DROP_CACHES -eq 1 ]] || return
    [[ -w /proc/sys/vm/drop_caches ]] || return
    sync
    echo 3 > /proc/sys/vm/drop_caches
}

run_write_pass() {
    local dd_output
    dd_output=$(dd if=/dev/zero of="$TEST_FILE" bs=1M count=$(( FILE_SIZE_BYTES / 1024 / 1024 )) conv=fdatasync status=progress 2>&1)
    printf '%s\n' "$dd_output" | extract_dd_rate
}

run_read_pass() {
    local dd_output

    drop_caches_if_enabled

    if dd_output=$(dd if="$TEST_FILE" of=/dev/null bs=4M iflag=direct status=progress 2>&1); then
        :
    else
        dd_output=$(dd if="$TEST_FILE" of=/dev/null bs=4M status=progress 2>&1)
    fi

    printf '%s\n' "$dd_output" | extract_dd_rate
}

run_burnin() {
    START_TS=$(date +%s)
    DEADLINE=$(( START_TS + DURATION_SEC ))
    PASS=0
    TOTAL_WRITTEN=0
    TOTAL_READ=0
    LAST_WRITE_RATE=""
    LAST_READ_RATE=""

    while (( $(date +%s) < DEADLINE )); do
        PASS=$(( PASS + 1 ))
        log "Pass $PASS: writing $(human_bytes "$FILE_SIZE_BYTES")"
        LAST_WRITE_RATE=$(run_write_pass)
        TOTAL_WRITTEN=$(( TOTAL_WRITTEN + FILE_SIZE_BYTES ))

        log "Pass $PASS: reading $(human_bytes "$FILE_SIZE_BYTES")"
        LAST_READ_RATE=$(run_read_pass)
        TOTAL_READ=$(( TOTAL_READ + FILE_SIZE_BYTES ))

        sync
        if [[ $KEEP_FILE -eq 0 ]]; then
            rm -f "$TEST_FILE"
        fi

        log "Pass $PASS complete: write ${LAST_WRITE_RATE:-unknown}, read ${LAST_READ_RATE:-unknown}"

        if [[ $KEEP_FILE -eq 1 ]]; then
            TEST_FILE="$MOUNTPOINT/.disk-burnin-${HOSTNAME:-host}-$$-pass$((PASS + 1)).bin"
        fi
    done

    ELAPSED=$(( $(date +%s) - START_TS ))
}

print_summary() {
    cat <<EOF

Burn-in complete
  Elapsed:          ${ELAPSED}s
  Passes:           $PASS
  Total written:    $(human_bytes "$TOTAL_WRITTEN")
  Total read:       $(human_bytes "$TOTAL_READ")
  Last write rate:  ${LAST_WRITE_RATE:-unknown}
  Last read rate:   ${LAST_READ_RATE:-unknown}
EOF
}

main() {
    parse_args "$@"
    require_root
    require_cmd lsblk findmnt mount umount mountpoint dd sync numfmt awk sed rm mkdir rmdir realpath

    resolve_target
    collect_facts
    print_report
    confirm_or_exit
    run_burnin
    print_summary
}

main "$@"
