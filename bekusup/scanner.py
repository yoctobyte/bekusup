import subprocess
import json
import sys
import os

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

def get_verified_targets(config, store):
    """
    Returns a list of (disk_node, mountpoint, serial, uuid, label).
    """
    candidates = scan_candidate_disks(config.destination.label_contains)
    if not candidates:
        print("Error: No eligible backup disks found.", file=sys.stderr)
        sys.exit(1)
        
    targets = []
    # Avoid cyclic imports if scanner.py is loaded before store.py
    from .store import verify_trust, get_disk_identity
    
    for disk in candidates:
        mp = ensure_mounted(disk, config.destination.fallback_mount_root)
        ok, msg = verify_trust(disk, mp, store)
        if not ok:
            print(f"Skipping /dev/{disk.get('name')}: {msg}", file=sys.stderr)
            continue
        serial, uuid, label = get_disk_identity(disk)
        targets.append((disk, mp, serial, uuid, label))
        
    if not targets:
        print("Error: Candidate disks found but none were fully trusted/enrolled.", file=sys.stderr)
        sys.exit(1)
        
    return targets

def resolve_target_disk(config):
    """Return the single eligible candidate disk for enrollment.

    Refuses if zero or more than one candidate matches — enrollment is a
    one-disk-at-a-time operation, so ambiguity is rejected rather than
    guessed.
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
    if len(candidates) > 1:
        names = ", ".join(f"/dev/{d.get('name')}" for d in candidates)
        print(
            f"Error: Found {len(candidates)} eligible disks ({names}). "
            f"Enroll one disk at a time — disconnect the others first.",
            file=sys.stderr,
        )
        sys.exit(1)
    return candidates[0]


def ensure_mounted(disk, fallback_mount_root):
    """Ensures a disk is mounted and returns the active mountpoint."""
    mounts = []
    if "mountpoints" in disk and disk["mountpoints"]:
        mounts.extend([m for m in disk["mountpoints"] if m])
    if "mountpoint" in disk and disk["mountpoint"]:
        mounts.append(disk["mountpoint"])
        
    if mounts:
        # Ensure it is writable by current user
        mp = mounts[0]
        if os.access(mp, os.W_OK):
            return mp
        else:
            print(f"Error: Target {mp} is mounted but not writable by the current user.", file=sys.stderr)
            sys.exit(1)
            
    # Attempt to mount
    dev_path = f"/dev/{disk.get('name')}"
    mount_target = os.path.join(fallback_mount_root, disk.get('label', disk.get('uuid', 'unknown')))
    
    try:
        os.makedirs(mount_target, exist_ok=True)
        print(f"Attempting to mount {dev_path} to {mount_target}...")
        # Note: Depending on system config, mount may require sudo.
        subprocess.run(['sudo', '-n', 'mount', dev_path, mount_target], check=True)
        return mount_target
    except subprocess.CalledProcessError:
        print(f"Error: Failed to safely mount {dev_path}. You may need to mount it manually.", file=sys.stderr)
        sys.exit(1)
    except PermissionError:
        print(f"Error: Permission denied creating fallback mount directory {mount_target}.", file=sys.stderr)
        sys.exit(1)
