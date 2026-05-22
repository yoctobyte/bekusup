import hashlib
import os
import stat
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from fcntl import ioctl


SKIP_FILENAMES = {
    ".bekusup-volume.json",
    "SESSION_COMPLETE",
    "manifest.json",
}

FICLONE = 0x40049409
REFLINK_FILESYSTEMS = {"btrfs", "xfs"}


@dataclass
class FixResult:
    changed: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_shared: int = 0


def format_bytes(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def hash_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iter_regular_files(root):
    for current_root, dirs, files in os.walk(root):
        dirs.sort()
        files.sort()
        dirs[:] = [d for d in dirs if not d.endswith(".incomplete")]
        for name in files:
            if name in SKIP_FILENAMES:
                continue
            path = os.path.join(current_root, name)
            try:
                st = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            yield path, st


def find_hardlink_candidate_groups(root):
    by_size = defaultdict(list)
    for path, st in iter_regular_files(root):
        if st.st_size == 0:
            continue
        by_size[(st.st_dev, st.st_size)].append((path, st))

    groups = []
    for (device, size), entries in by_size.items():
        if len(entries) < 2:
            continue

        by_hash = defaultdict(list)
        for path, st in entries:
            try:
                by_hash[hash_file(path)].append((path, st))
            except OSError:
                continue

        for digest, hashed_entries in by_hash.items():
            if len(hashed_entries) < 2:
                continue

            by_inode = defaultdict(list)
            for path, st in hashed_entries:
                by_inode[(st.st_dev, st.st_ino)].append(path)

            if len(by_inode) < 2:
                continue

            inode_groups = [sorted(inode_paths) for inode_paths in by_inode.values()]
            inode_groups.sort(key=lambda inode_paths: inode_paths[0])
            paths = [path for inode_paths in inode_groups for path in inode_paths]
            groups.append(
                {
                    "device": device,
                    "size": size,
                    "hash": digest,
                    "inode_count": len(by_inode),
                    "path_count": len(paths),
                    "potential_savings": size * (len(by_inode) - 1),
                    "inode_groups": inode_groups,
                    "paths": sorted(paths),
                }
            )

    groups.sort(key=lambda group: group["potential_savings"], reverse=True)
    return groups


def summarize_groups(groups):
    return {
        "groups": len(groups),
        "paths": sum(group["path_count"] for group in groups),
        "potential_savings": sum(group["potential_savings"] for group in groups),
    }


def detect_filesystem_type(path):
    try:
        result = subprocess.run(
            ["findmnt", "-no", "FSTYPE", "-T", path],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else None


def resolve_link_mode(path, requested_mode):
    if requested_mode != "auto":
        return requested_mode
    fs_type = detect_filesystem_type(path)
    if fs_type in REFLINK_FILESYSTEMS:
        return "reflink"
    return "hardlink"


def _can_hardlink_replace(canonical_stat, target_stat):
    return (
        stat.S_IMODE(canonical_stat.st_mode) == stat.S_IMODE(target_stat.st_mode)
        and canonical_stat.st_uid == target_stat.st_uid
        and canonical_stat.st_gid == target_stat.st_gid
    )


def _verify_candidate(path, expected_size, expected_hash):
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size != expected_size:
        return None
    if hash_file(path) != expected_hash:
        return None
    return st


def _replace_with_hardlink(canonical_path, target_path):
    directory = os.path.dirname(target_path)
    fd, temp_path = tempfile.mkstemp(prefix=".bekusup-link.", dir=directory)
    os.close(fd)
    os.unlink(temp_path)
    try:
        os.link(canonical_path, temp_path)
        os.replace(temp_path, target_path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _copy_target_metadata(source_path, target_path):
    st = os.stat(source_path, follow_symlinks=False)
    os.chmod(target_path, stat.S_IMODE(st.st_mode), follow_symlinks=False)
    try:
        os.chown(target_path, st.st_uid, st.st_gid, follow_symlinks=False)
    except PermissionError:
        pass
    os.utime(target_path, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)


def _replace_with_reflink(canonical_path, target_path):
    directory = os.path.dirname(target_path)
    fd, temp_path = tempfile.mkstemp(prefix=".bekusup-reflink.", dir=directory)
    os.close(fd)
    try:
        with open(canonical_path, "rb") as source, open(temp_path, "r+b") as target:
            ioctl(target.fileno(), FICLONE, source.fileno())
        _copy_target_metadata(target_path, temp_path)
        os.replace(temp_path, target_path)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def fix_candidate_group(group, mode):
    result = FixResult()
    inode_groups = group.get("inode_groups") or [[path] for path in group["paths"]]
    canonical_path = inode_groups[0][0]
    expected_size = group["size"]
    expected_hash = group["hash"]

    try:
        canonical_stat = _verify_candidate(canonical_path, expected_size, expected_hash)
    except OSError:
        canonical_stat = None
    if canonical_stat is None:
        result.errors += sum(len(paths) for paths in inode_groups[1:])
        return result

    canonical_inode = (canonical_stat.st_dev, canonical_stat.st_ino)
    for paths in inode_groups:
        for target_path in paths:
            try:
                target_stat = _verify_candidate(target_path, expected_size, expected_hash)
                if target_stat is None:
                    result.skipped += 1
                    continue

                target_inode = (target_stat.st_dev, target_stat.st_ino)
                if target_inode == canonical_inode:
                    continue

                if mode == "hardlink":
                    if not _can_hardlink_replace(canonical_stat, target_stat):
                        result.skipped += 1
                        continue
                    _replace_with_hardlink(canonical_path, target_path)
                elif mode == "reflink":
                    _replace_with_reflink(canonical_path, target_path)
                else:
                    raise ValueError(f"Unsupported link mode: {mode}")

                result.changed += 1
                result.bytes_shared += expected_size
            except OSError:
                result.errors += 1

    return result


def fix_candidate_groups(groups, mode):
    total = FixResult()
    for group in groups:
        result = fix_candidate_group(group, mode)
        total.changed += result.changed
        total.skipped += result.skipped
        total.errors += result.errors
        total.bytes_shared += result.bytes_shared
    return total
