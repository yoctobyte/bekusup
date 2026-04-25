import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .config import load_config
from .scanner import ensure_mounted, scan_candidate_disks, unmount_if_mounted_by_bekusup
from .store import IndexStore, get_disk_identity, read_marker_file


@dataclass
class CheckResult:
    ok: bool
    label: str
    detail: str


def format_bytes(size):
    if size is None:
        return "unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def parse_ssh_uri(uri):
    if not uri or not uri.startswith("ssh://"):
        return None

    raw = uri.replace("ssh://", "", 1)
    user = None
    password = None
    if "@" in raw:
        user_part, host_part = raw.split("@", 1)
        if ":" in user_part:
            user, password = user_part.split(":", 1)
        else:
            user = user_part
    else:
        host_part = raw

    port = "22"
    if ":" in host_part:
        host, port = host_part.rsplit(":", 1)
    else:
        host = host_part

    remote = f"{user}@{host}" if user else host
    return {
        "host": host,
        "port": port,
        "remote": remote,
        "password": password,
    }


def run_timed(cmd, timeout=8):
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return None, 0, f"missing tool: {exc.filename}"
    except subprocess.TimeoutExpired:
        return None, timeout, f"timed out after {timeout}s"

    return result, time.monotonic() - start, None


def local_path_size(path):
    if not os.path.exists(path):
        return None, "missing"
    if os.path.isfile(path):
        return os.path.getsize(path), None

    if shutil.which("du"):
        result, _elapsed, error = run_timed(["du", "-sb", path], timeout=30)
        if error:
            return None, error
        if result and result.returncode == 0:
            try:
                return int(result.stdout.split()[0]), None
            except (IndexError, ValueError):
                pass

    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total, None


def ssh_base_cmd(info):
    cmd = ["ssh", "-p", info["port"], "-o", "BatchMode=yes", info["remote"]]
    return cmd


def check_ssh_host(host):
    info = parse_ssh_uri(host.uri)
    if not info:
        return [CheckResult(True, "reachability", "local host")], {}

    results = []
    nc_cmd = ["nc", "-z", "-w", "2", info["host"], info["port"]]
    nc_result, elapsed, error = run_timed(nc_cmd, timeout=4)
    if error:
        results.append(CheckResult(False, "tcp", error))
    elif nc_result.returncode == 0:
        results.append(CheckResult(True, "tcp", f"port {info['port']} reachable in {elapsed:.2f}s"))
    else:
        results.append(CheckResult(False, "tcp", f"port {info['port']} unreachable"))

    if info["password"]:
        results.append(
            CheckResult(
                False,
                "ssh-auth",
                "password URI configured; skipping ssh command probe to avoid exposing password in process list",
            )
        )
        return results, info

    ssh_result, ssh_elapsed, ssh_error = run_timed(ssh_base_cmd(info) + ["true"], timeout=8)
    if ssh_error:
        results.append(CheckResult(False, "ssh-auth", ssh_error))
    elif ssh_result.returncode == 0:
        results.append(CheckResult(True, "ssh-auth", f"non-interactive ssh OK in {ssh_elapsed:.2f}s"))
    else:
        detail = (ssh_result.stderr or ssh_result.stdout or "ssh returned non-zero").strip()
        results.append(CheckResult(False, "ssh-auth", detail))

    return results, info


def remote_path_size(host, info, path):
    if not info or info.get("password"):
        return None, "skipped"
    quoted = shlex.quote(path)
    result, _elapsed, error = run_timed(ssh_base_cmd(info) + [f"du -sb -- {quoted}"], timeout=30)
    if error:
        return None, error
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or "remote du failed").strip()
    try:
        return int(result.stdout.split()[0]), None
    except (IndexError, ValueError):
        return None, "could not parse remote du output"


def disk_identity_id(serial, uuid, label):
    return serial if serial else f"{uuid}-{label}"


def check_disks(config):
    print("== Disk Flyover ==")
    store = IndexStore()
    candidates = scan_candidate_disks(config.destination.label_contains)
    if not candidates:
        print(f"[FAIL] no candidate disks with label containing '{config.destination.label_contains}'")
        return False

    all_ok = True
    for disk in candidates:
        name = disk.get("name")
        label = disk.get("label") or ""
        print(f"\n/dev/{name} label={label or '-'} size={disk.get('size') or '-'}")
        mounted_by_bekusup = False
        mountpoint = None
        try:
            mountpoint, mounted_by_bekusup = ensure_mounted(
                disk,
                config.destination.fallback_mount_root,
                allow_mount=True,
            )
            print(f"[OK] mount: {mountpoint}" + (" (temporary)" if mounted_by_bekusup else ""))
            serial, uuid, current_label = get_disk_identity(disk)
            if not serial and not uuid:
                print("[FAIL] identity: disk exposes neither serial nor filesystem UUID")
                all_ok = False
                continue

            marker = read_marker_file(mountpoint)
            drive_id = disk_identity_id(serial, uuid, current_label)
            indexed = store.get_drive(drive_id) is not None
            if not marker:
                print("[WARN] trust: no .bekusup-volume.json marker; run enroll before real backup")
                all_ok = False
            elif not indexed:
                print("[WARN] trust: marker exists, but local index does not know this drive")
                all_ok = False
            elif marker.get("serial") and serial and marker.get("serial") != serial:
                print("[FAIL] trust: marker serial conflicts with current serial")
                all_ok = False
            elif marker.get("uuid") and uuid and marker.get("uuid") != uuid:
                print("[FAIL] trust: marker UUID conflicts with current UUID")
                all_ok = False
            else:
                print(f"[OK] trust: enrolled as {drive_id}")
        except SystemExit:
            print("[FAIL] mount/trust check aborted for this disk")
            all_ok = False
        finally:
            if mounted_by_bekusup and mountpoint:
                unmount_if_mounted_by_bekusup(mountpoint)

    return all_ok


def check_hosts(config):
    print("\n== Host Flyover ==")
    if not config.hosts:
        print("[FAIL] no hosts configured")
        return False

    all_ok = True
    for host in config.hosts:
        print(f"\nHost: {host.name} ({host.transport})")
        reachability, ssh_info = check_ssh_host(host)
        host_ok = True
        for result in reachability:
            marker = "[OK]" if result.ok else "[WARN]" if result.label == "ssh-auth" else "[FAIL]"
            print(f"{marker} {result.label}: {result.detail}")
            if not result.ok and result.label != "ssh-auth":
                host_ok = False

        total_size = 0
        unknown_size = False
        for path_cfg in host.paths:
            if host.transport == "local":
                size, error = local_path_size(path_cfg.source)
            elif host.transport == "ssh":
                size, error = remote_path_size(host, ssh_info, path_cfg.source)
            else:
                size, error = None, f"size probe not implemented for transport '{host.transport}'"

            if error:
                print(f"[WARN] size {path_cfg.source}: {error}")
                unknown_size = True
            else:
                total_size += size or 0
                print(f"[OK] size {path_cfg.source}: {format_bytes(size)}")

        if not unknown_size:
            print(f"[OK] total configured source size: {format_bytes(total_size)}")
        if getattr(host, "bandwidth_limit_kbps", 0):
            print(f"[INFO] configured bandwidth limit: {host.bandwidth_limit_kbps} KiB/s")
        else:
            print("[INFO] configured bandwidth limit: none")

        all_ok = all_ok and host_ok

    return all_ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bekusup flyover: preflight configuration and environment checks")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    print("Bekusup Flyover")
    print("This checks configuration, mountability, trust, reachability, and source sizes.")
    print("It does not create backup sessions.")

    disks_ok = check_disks(config)
    hosts_ok = check_hosts(config)
    if disks_ok and hosts_ok:
        print("\nFlyover result: OK")
        return 0
    print("\nFlyover result: WARNINGS/FAILURES")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
