import subprocess
import json
import sys
import os
import shutil


def invoking_uid_gid():
    uid = int(os.environ.get("SUDO_UID") or os.getuid())
    gid = int(os.environ.get("SUDO_GID") or os.getgid())
    return uid, gid


def sudo_available():
    return shutil.which("sudo") is not None


def run_sudo(cmd):
    if os.geteuid() == 0:
        subprocess.run(cmd, check=True)
    else:
        subprocess.run(["sudo"] + cmd, check=True)


def ensure_marker_writable(mountpoint, uid, gid):
    marker_path = os.path.join(mountpoint, ".bekusup-volume.json")
    if not os.path.exists(marker_path) or os.access(marker_path, os.W_OK):
        return
    run_sudo(["chown", f"{uid}:{gid}", marker_path])


def ensure_mountpoint_writable(mountpoint):
    uid, gid = invoking_uid_gid()
    if os.access(mountpoint, os.W_OK):
        try:
            ensure_marker_writable(mountpoint, uid, gid)
        except subprocess.CalledProcessError:
            print(
                f"Error: Existing marker at {mountpoint} could not be made writable.",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    if not sudo_available() and os.geteuid() != 0:
        print(
            f"Error: Target {mountpoint} is mounted but not writable by the current user.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        print(f"Making backup mount writable by uid {uid}, gid {gid}: {mountpoint}")
        run_sudo(["chown", f"{uid}:{gid}", mountpoint])
        ensure_marker_writable(mountpoint, uid, gid)
    except subprocess.CalledProcessError:
        print(
            f"Error: Target {mountpoint} is mounted but could not be made writable.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.access(mountpoint, os.W_OK):
        print(f"Error: Target {mountpoint} is still not writable.", file=sys.stderr)
        sys.exit(1)

def get_block_devices():
    """Returns a list of dictionaries with block device information using lsblk."""
    try:
        result = subprocess.run(
            ['lsblk', '-J', '-o', 'NAME,LABEL,MOUNTPOINTS,MOUNTPOINT,FSTYPE,SIZE,SERIAL,UUID,TYPE,RO'],
            capture_output=True, text=True, check=True
        )
        data = json.loads(result.stdout)
        return data.get('blockdevices', [])
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error querying block devices: {e}", file=sys.stderr)
        return []

def is_system_device(node):
    """Heuristic to reject obvious internal/system disks."""
    name = str(node.get("name", "")).lower()
    if name.startswith("loop") or name.startswith("ram") or "mapper" in name or "crypto" in name:
        return True
        
    mounts = []
    if "mountpoints" in node and node["mountpoints"]:
        mounts.extend(node["mountpoints"])
    if "mountpoint" in node and node["mountpoint"]:
        mounts.append(node["mountpoint"])
    
    for mp in mounts:
        if not mp:
            continue
        if mp in ['/', '/boot', '/boot/efi'] or str(mp).startswith('/var') or str(mp).startswith('/sys'):
            return True
            
    # Check children for system mounts
    for child in node.get("children", []):
        if is_system_device(child):
            return True
    return False

def scan_candidate_disks(label_contains: str):
    """Scan and yield candidate partitions matching the label."""
    devices = get_block_devices()
    candidates = []
    
    def process_node(node):
        if is_system_device(node):
            return # Skip entire disk or branch
            
        label = node.get("label") or ""
        if label_contains.lower() in label.lower():
            candidates.append(node)
            
        for child in node.get("children", []):
            process_node(child)
            
    for dev in devices:
        process_node(dev)
        
    return candidates

def get_verified_targets(config, store, allow_mount=True):
    """
    Returns a list of (disk_node, mountpoint, serial, uuid, label, mounted_by_bekusup).
    """
    candidates = scan_candidate_disks(config.destination.label_contains)
    if not candidates:
        print("Error: No eligible backup disks found.", file=sys.stderr)
        sys.exit(1)
        
    targets = []
    # Avoid cyclic imports if scanner.py is loaded before store.py
    from .store import verify_trust, get_disk_identity
    
    for disk in candidates:
        mp, mounted_by_bekusup = ensure_mounted(
            disk,
            config.destination.fallback_mount_root,
            allow_mount=allow_mount,
        )
        ok, msg = verify_trust(disk, mp, store)
        if not ok:
            print(f"Skipping /dev/{disk.get('name')}: {msg}", file=sys.stderr)
            continue
        serial, uuid, label = get_disk_identity(disk)
        targets.append((disk, mp, serial, uuid, label, mounted_by_bekusup))
        
    if not targets:
        print("Error: Candidate disks found but none were fully trusted/enrolled.", file=sys.stderr)
        sys.exit(1)
        
    return targets

def resolve_target_disk(config, target_device=None):
    """Return the single eligible candidate disk for enrollment.

    If target_device is provided, it must name one of the eligible candidates.
    Otherwise this refuses if zero or more than one candidate matches —
    enrollment is a one-disk-at-a-time operation, so ambiguity is rejected
    rather than guessed.
    """
    candidates = scan_candidate_disks(config.destination.label_contains)
    if not candidates:
        print(
            f"Error: No eligible disks found with label containing "
            f"'{config.destination.label_contains}'. Check that the disk is "
            f"plugged in and that its partition label matches.",
            file=sys.stderr,
        )
        sys.exit(1)
    if target_device:
        requested = os.path.basename(str(target_device).strip())
        for candidate in candidates:
            if candidate.get("name") == requested:
                return candidate
        names = ", ".join(f"/dev/{d.get('name')}" for d in candidates)
        print(
            f"Error: Requested target {target_device} is not an eligible backup disk. "
            f"Eligible disks: {names}",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(candidates) > 1:
        names = ", ".join(f"/dev/{d.get('name')}" for d in candidates)
        print(
            f"Error: Found {len(candidates)} eligible disks ({names}). "
            f"Run `bekusup enroll /dev/<name>` to choose one.",
            file=sys.stderr,
        )
        sys.exit(1)
    return candidates[0]


def ensure_mounted(disk, fallback_mount_root, allow_mount=True):
    """Ensures a disk is mounted.

    Returns (mountpoint, mounted_by_bekusup). The second value is True only
    when this call performed the mount and the caller may safely unmount it
    afterward.
    """
    mounts = []
    if "mountpoints" in disk and disk["mountpoints"]:
        mounts.extend([m for m in disk["mountpoints"] if m])
    if "mountpoint" in disk and disk["mountpoint"]:
        mounts.append(disk["mountpoint"])
        
    if mounts:
        # Ensure it is writable by current user
        mp = mounts[0]
        ensure_mountpoint_writable(mp)
        return mp, False
            
    if not allow_mount:
        print(
            f"Error: /dev/{disk.get('name')} is not mounted and mounting is disabled.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Attempt to mount
    dev_path = f"/dev/{disk.get('name')}"
    mount_target = os.path.join(fallback_mount_root, disk.get('label', disk.get('uuid', 'unknown')))
    
    try:
        try:
            os.makedirs(mount_target, exist_ok=True)
        except PermissionError:
            run_sudo(["mkdir", "-p", mount_target])
        print(f"Attempting to mount {dev_path} to {mount_target}...")
        run_sudo(["mount", dev_path, mount_target])
        ensure_mountpoint_writable(mount_target)
        return mount_target, True
    except subprocess.CalledProcessError:
        print(f"Error: Failed to safely mount {dev_path}. You may need to mount it manually.", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied creating fallback mount directory {mount_target}.", file=sys.stderr)
        sys.exit(1)


def unmount_if_mounted_by_bekusup(mountpoint):
    """Unmount a mountpoint that was created by bekusup during this run."""
    try:
        print(f"Unmounting temporary mount {mountpoint}...")
        subprocess.run(['sudo', '-n', 'umount', mountpoint], check=True)
    except subprocess.CalledProcessError:
        print(
            f"Warning: Failed to unmount temporary mount {mountpoint}. Please inspect it manually.",
            file=sys.stderr,
        )
