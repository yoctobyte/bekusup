#!/usr/bin/env bash

set -Eeuo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

REMOTE_HOST="${BEKUSUP_DOCK_HOST:-borg}"
REMOTE_REPO="${BEKUSUP_REMOTE_REPO:-/home/rene/bekusup}"
REMOTE_CONFIG="${BEKUSUP_REMOTE_CONFIG:-/home/rene/bekusup/config.yaml}"
FS_TYPE="${BEKUSUP_DOCK_FS:-ext4}"
LABEL_PREFIX="${BEKUSUP_DOCK_LABEL_PREFIX:-backupdisk}"

usage() {
  cat <<EOF
Usage: ./bekusup-auto-dock.sh [extra dock_auto_proceed.sh args]

Runs Bekusup remote dock auto mode with the usual defaults:

  remote host:   $REMOTE_HOST
  remote repo:   $REMOTE_REPO
  remote config: $REMOTE_CONFIG
  filesystem:    $FS_TYPE
  label prefix:  $LABEL_PREFIX

The default action auto-detects exactly one safe-looking docked HDD on the
remote host, reclaims/formats it, enrolls it, and runs a real Bekusup backup.

Override defaults with:
  BEKUSUP_DOCK_HOST=host
  BEKUSUP_REMOTE_REPO=/path/to/bekusup
  BEKUSUP_REMOTE_CONFIG=/path/to/config.yaml
  BEKUSUP_DOCK_FS=ext4|xfs|btrfs
  BEKUSUP_DOCK_LABEL_PREFIX=backupdisk

Extra arguments are passed to tools/disk/dock_auto_proceed.sh.
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

cat <<EOF
Bekusup remote dock auto mode

Remote host:   $REMOTE_HOST
Remote repo:   $REMOTE_REPO
Remote config: $REMOTE_CONFIG
Filesystem:    $FS_TYPE
Label prefix:  $LABEL_PREFIX

This will auto-detect exactly one safe-looking docked HDD on the remote host,
reclaim/format it, enroll it, and run a real Bekusup backup.

Override defaults with:
  BEKUSUP_DOCK_HOST=host
  BEKUSUP_REMOTE_REPO=/path/to/bekusup
  BEKUSUP_REMOTE_CONFIG=/path/to/config.yaml
  BEKUSUP_DOCK_FS=ext4|xfs|btrfs
  BEKUSUP_DOCK_LABEL_PREFIX=backupdisk

Extra arguments are passed to tools/disk/dock_auto_proceed.sh.
EOF

exec "$DIR/tools/disk/dock_auto_proceed.sh" \
  --remote "$REMOTE_HOST" \
  --repo "$REMOTE_REPO" \
  --config "$REMOTE_CONFIG" \
  --detect-one \
  --reclaim \
  --allow-destructive \
  --enroll \
  --run \
  --yes \
  --fs "$FS_TYPE" \
  --label-prefix "$LABEL_PREFIX" \
  "$@"
