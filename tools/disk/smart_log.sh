#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_NAME=$(basename "$0")
LOG_DIR_DEFAULT="/var/log/bekusup/disk-smart"
LOG_DIR="$LOG_DIR_DEFAULT"
TAG=""

usage() {
    cat <<EOF
Usage: sudo $SCRIPT_NAME [options] /dev/sdX

Saves a timestamped smartctl report for the given disk into a dedicated
folder so SMART state can be tracked over time and compared between runs.

Options:
      --dir DIR      Output directory (default: $LOG_DIR_DEFAULT)
      --tag TAG      Optional tag inserted into the filename (e.g. 'reclaim',
                     'preflight'); restricted to [A-Za-z0-9_.-]
  -h, --help         Show this help

Output filename:
  <serial>[_<tag>]_<YYYYMMDD-HHMMSS>.smart.log
  (falls back to kernel name when the serial cannot be read)
EOF
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

DEVICE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            shift
            [[ $# -gt 0 ]] || die "--dir requires a value"
            LOG_DIR=$1
            ;;
        --tag)
            shift
            [[ $# -gt 0 ]] || die "--tag requires a value"
            TAG=$(tr -cd '[:alnum:]_.-' <<<"$1")
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        /dev/*)
            [[ -z "$DEVICE" ]] || die "specify only one device"
            DEVICE=$1
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
    shift
done

[[ -n "$DEVICE" ]] || { usage >&2; die "missing target device"; }
[[ $EUID -eq 0 ]] || die "run as root"
[[ -b "$DEVICE" ]] || die "$DEVICE is not a block device"
command -v smartctl >/dev/null 2>&1 || die "smartctl not installed"

SERIAL=$(lsblk -dn -o SERIAL "$DEVICE" 2>/dev/null | sed 's/[[:space:]]\+$//')
MODEL=$(lsblk -dn -o MODEL "$DEVICE" 2>/dev/null | sed 's/[[:space:]]\+$//')
KNAME=$(lsblk -dn -o KNAME "$DEVICE" 2>/dev/null | sed 's/[[:space:]]\+$//')
WWN=$(lsblk -dn -o WWN "$DEVICE" 2>/dev/null | sed 's/[[:space:]]\+$//')

SAFE_SERIAL=$(tr -cd '[:alnum:]_.-' <<<"${SERIAL:-}")
[[ -n "$SAFE_SERIAL" ]] || SAFE_SERIAL=$KNAME

TS=$(date '+%Y%m%d-%H%M%S')
mkdir -p "$LOG_DIR"

if [[ -n "$TAG" ]]; then
    OUT="$LOG_DIR/${SAFE_SERIAL}_${TAG}_${TS}.smart.log"
else
    OUT="$LOG_DIR/${SAFE_SERIAL}_${TS}.smart.log"
fi

{
    printf 'device:    %s\n' "$DEVICE"
    printf 'kname:     %s\n' "$KNAME"
    printf 'model:     %s\n' "${MODEL:-unknown}"
    printf 'serial:    %s\n' "${SERIAL:-unknown}"
    printf 'wwn:       %s\n' "${WWN:-unknown}"
    printf 'host:      %s\n' "$(hostname)"
    printf 'timestamp: %s\n' "$(date -Iseconds)"
    [[ -n "$TAG" ]] && printf 'tag:       %s\n' "$TAG"
    printf '\n--- smartctl -x ---\n'
    smartctl -x "$DEVICE" 2>&1 || true
} >"$OUT"

chmod 0644 "$OUT"
printf 'Wrote %s\n' "$OUT"
