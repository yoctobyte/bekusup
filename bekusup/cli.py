import argparse
import os
import socket
import subprocess
import sys
import tempfile

import yaml

from .config import Config, load_config


DEFAULT_DRY_RUN = True


def is_host_online(uri):
    if not uri or not uri.startswith("ssh://"):
        return True
    host_part = uri.replace("ssh://", "", 1)
    port = "22"
    if "@" in host_part:
        host_part = host_part.split("@")[-1]
    if ":" in host_part:
        host_part, port = host_part.split(":", 1)
    try:
        subprocess.run(
            ["nc", "-z", "-w", "2", host_part, port],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def resolve_dry_run(args):
    if getattr(args, "dry_run", False):
        return True
    if getattr(args, "no_dry_run", False):
        return False
    return DEFAULT_DRY_RUN


def print_dry_run_banner(enabled):
    if enabled:
        print("=== DRY RUN MODE: no durable writes will be made ===")


def build_minimal_local_config():
    username = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
    hostname = socket.gethostname() or "localhost"
    return {
        "destination": {
            "label_contains": "backup",
            "fallback_mount_root": "/mnt/bekusup",
            "auto_unmount": False,
        },
        "run_policy": {
            "min_free_space_gb": 20,
            "incomplete_suffix": ".incomplete",
            "complete_marker": "SESSION_COMPLETE",
            "max_parallel_hosts": 2,
        },
        "hosts": [
            {
                "name": hostname,
                "transport": "local",
                "paths": [
                    {
                        "source": os.path.join("/home", username),
                        "dest_subdir": os.path.join("home", username),
                    }
                ],
            }
        ],
    }


def write_yaml_atomic(path, data):
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".bekusup.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def run_init_wizard(config_path):
    target = os.path.abspath(config_path)
    if os.path.exists(target):
        print(f"Config already exists at {target}. Refusing to overwrite it automatically.")
        return 1

    print(f"Creating a starter config at {target}. Press Ctrl-C to abort.")
    try:
        default_data = build_minimal_local_config()
        hostname = default_data["hosts"][0]["name"]
        source = default_data["hosts"][0]["paths"][0]["source"]
        host_name = input(f"Host name [{hostname}]: ").strip() or hostname
        source_path = input(f"Source path [{source}]: ").strip() or source

        default_data["hosts"][0]["name"] = host_name
        default_data["hosts"][0]["paths"][0]["source"] = source_path
        write_yaml_atomic(target, default_data)
    except KeyboardInterrupt:
        print("\nInitialization cancelled. No config was written.", file=sys.stderr)
        return 1

    print(f"Wrote starter config to {target}")
    print("Next steps:")
    print("  1. Review the generated config.yaml")
    print("  2. Run `./bekusup-cli.sh` for a dry-run")
    print("  3. Run `./bekusup-cli.sh enroll` when a new backup disk is inserted")
    return 0


def interactive_no_config_menu(config_path):
    print(f"No config file found at {os.path.abspath(config_path)}.")
    print("Choose an action:")
    print("  1. Create a starter localhost config")
    print("  2. Scan for candidate backup disks")
    print("  3. Enroll a backup disk")
    print("  q. Quit")
    choice = input("> ").strip().lower()

    if choice == "1":
        return "init"
    if choice == "2":
        return "scan"
    if choice == "3":
        return "enroll"
    return None


def ensure_config_exists_or_route(args):
    config_exists = os.path.exists(args.config)
    if args.command is not None:
        return args.command

    if config_exists:
        print(
            f"No command specified. Defaulting to `run` with config {os.path.abspath(args.config)}."
        )
        return "run"

    selection = interactive_no_config_menu(args.config)
    if selection is None:
        print("Nothing selected. Exiting.")
        return None
    return selection


def cmd_run(args, config):
    from .lock import RunLock
    from .scanner import get_verified_targets
    from .store import IndexStore
    from .session import SessionManager
    from .transports import get_provider
    import concurrent.futures

    dry_run = resolve_dry_run(args)

    with RunLock():
        print_dry_run_banner(dry_run)
        print(f"Running bekusup with {len(config.hosts)} hosts configured.")
        store = IndexStore()

        targets = get_verified_targets(
            config,
            store,
            allow_mount=not dry_run,
        )

        def freshness(target):
            disk, mp, serial, uuid, label = target
            drive_id = serial if serial else f"{uuid}-{label}"
            info = store.get_drive(drive_id)
            if not info or not info.get("sessions"):
                return 0

            completed_times = []
            for session_info in info["sessions"].values():
                if session_info.get("outcome") in ("complete", "complete_with_warnings"):
                    completed_times.append(session_info.get("timestamp", 0))

            return max(completed_times) if completed_times else 0

        targets = sorted(targets, key=freshness, reverse=True)
        host_cache_bases = {}

        for disk, mp, serial, uuid, label in targets:
            drive_id = serial if serial else f"{uuid}-{label}"
            print(
                f"\n>>> Executing Run on Target: /dev/{disk.get('name')} "
                f"[label: {label}] (Drive: {drive_id})"
            )

            session = SessionManager(mp, config, store, drive_id, dry_run=dry_run)
            if not session.begin_session():
                continue

            def sync_host(host):
                print(f"Starting backup for host: {host.name}")
                if not is_host_online(host.uri):
                    status = "unreachable"
                    session.record_host_status(
                        host.name,
                        status,
                        "Failed 2-second reachability probe.",
                    )
                    print(f"Skipping host {host.name} [{status}]")
                    return status

                foreign_snapshot_base = host_cache_bases.get(host.name)
                if not session.snapshot_base and foreign_snapshot_base:
                    print(
                        "Using Cross-Drive Cache (Copy-Dest) "
                        f"for {host.name} from {os.path.basename(foreign_snapshot_base)}"
                    )

                provider = get_provider(host)
                host_dest = session.get_host_dest_dir(host.name)

                all_paths_ok = True
                any_paths_ok = False
                for path_cfg in host.paths:
                    dest_path = os.path.join(host_dest, path_cfg.dest_subdir)

                    host_snapshot_base = None
                    active_base = session.snapshot_base or foreign_snapshot_base
                    if active_base:
                        base_path = os.path.join(active_base, host.name, path_cfg.dest_subdir)
                        if os.path.exists(base_path):
                            host_snapshot_base = base_path

                    result = provider.sync(
                        path_cfg.source,
                        dest_path,
                        snapshot_base=host_snapshot_base,
                        dry_run=dry_run,
                    )
                    if not result:
                        all_paths_ok = False
                    else:
                        any_paths_ok = True

                if all_paths_ok:
                    status = "succeeded"
                elif any_paths_ok:
                    status = "partial"
                else:
                    status = "failed"

                session.record_host_status(host.name, status)
                print(f"Finished backup for host: {host.name} [{status}]")
                return status

            max_parallel = config.run_policy.max_parallel_hosts
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
                futures = {executor.submit(sync_host, host): host for host in config.hosts}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        host = futures[future]
                        print(f"Exception backing up {host.name}: {exc}")
                        session.record_host_status(host.name, "failed", str(exc))

            session.finalize()
            if session.manifest["outcome"] in ("complete", "complete_with_warnings"):
                final_dir = os.path.join(session.sessions_dir, session.timestamp)
                for host_name, host_status in session.manifest["hosts"].items():
                    if host_status["status"] in ("succeeded", "partial"):
                        host_cache_bases[host_name] = final_dir

        if dry_run:
            print("=== DRY RUN COMPLETE: no durable writes were made ===")


def cmd_scan(args, config):
    from .scanner import scan_candidate_disks
    from .store import IndexStore, get_disk_identity

    print("================================")
    print(" BEKUSUP - OPERATOR DASHBOARD")
    print("================================")
    print("Scanning for eligible backup disks...")
    candidates = scan_candidate_disks(config.destination.label_contains)
    if not candidates:
        print("No eligible candidate disks found. Validate your mount labels.")
        return

    store = IndexStore()

    for candidate in candidates:
        print("\n-----------------------------")
        print(f"💽 Target: /dev/{candidate.get('name')}")
        print(f"   Label: {candidate.get('label')}")
        print(f"   Size:  {candidate.get('size')}")
        print(f"   Mount: {candidate.get('mountpoints') or candidate.get('mountpoint')}")

        serial, uuid, label = get_disk_identity(candidate)
        drive_id = serial if serial else f"{uuid}-{label}"
        info = store.get_drive(drive_id)

        if info:
            print("   Trust: ENROLLED ✅")
            sessions = info.get("sessions", {})
            print(f"   Session History: {len(sessions)} records")

            if sessions:
                last_session = sorted(sessions.keys())[-1]
                session_info = sessions[last_session]
                print(f"   Latest Run: {last_session} [{session_info.get('outcome')}]")
                host_statuses = session_info.get("hosts", {})
                success_count = sum(
                    1 for host in host_statuses.values() if host["status"] == "succeeded"
                )
                print(f"   Coverage: {success_count}/{len(host_statuses)} Hosts Available")
        else:
            print("   Trust: UNENROLLED ❌ (Requires `bekusup enroll` to reconcile database)")

    print("\n-----------------------------")
    print(" 📡 GLOBAL HOST COVERAGE")
    print("-----------------------------")
    for host in config.hosts:
        drive_id, session_id, stamp = store.get_last_success_for_host(host.name)
        if drive_id:
            from datetime import datetime

            time_str = datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
            print(f"   [+] {host.name}: Drive '{drive_id}'  ({time_str})")
        else:
            print(f"   [-] {host.name}: No successful backups found locally.")
    print("================================")


def cmd_enroll(args, config):
    from .scanner import ensure_mounted, resolve_target_disk
    from .store import IndexStore, get_disk_identity, write_marker_file

    print("Enrolling new backup disk...")
    disk = resolve_target_disk(config)
    print(f"Target selected: /dev/{disk.get('name')} [label: {disk.get('label')}]")
    mountpoint = ensure_mounted(disk, config.destination.fallback_mount_root, allow_mount=True)

    serial, uuid, label = get_disk_identity(disk)
    print(f"Identity -> Serial: {serial}, UUID: {uuid}")

    if not serial and not uuid:
        print(
            "Enrollment refused: disk exposes neither a Serial nor a filesystem UUID.\n"
            "  Run `lsblk -o NAME,SERIAL,UUID` to confirm; a disk with no stable identity\n"
            "  cannot be tracked safely across reboots or dock swaps.",
            file=sys.stderr,
        )
        sys.exit(1)

    write_marker_file(mountpoint, serial, uuid, label)
    store = IndexStore()
    try:
        drive_id = store.enroll_drive(serial, uuid, label)
    except ValueError as exc:
        print(f"Enrollment refused: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Enrolled successfully! Drive ID recorded locally as: {drive_id}")


def cmd_init(args, _config):
    raise SystemExit(run_init_wizard(args.config))


def build_parser():
    parser = argparse.ArgumentParser(description="Bekusup - Tape-Drive Style Rotating Backup")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    dry_group = parser.add_mutually_exclusive_group()
    dry_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without durable writes",
    )
    dry_group.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Allow durable writes even while the default dry-run training wheels are enabled",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="Perform one backup cycle against the detected eligible disk")
    subparsers.add_parser("scan", help="Report eligible disks, mount state, free space, and recent sessions")
    subparsers.add_parser("enroll", help="Perform one-time approval of a new backup disk")
    subparsers.add_parser("init", help="Interactively create a starter config")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    command = ensure_config_exists_or_route(args)
    if command is None:
        return 0

    args.command = command

    if command == "init":
        return run_init_wizard(args.config)

    if command in ("scan", "enroll") and not os.path.exists(args.config):
        config = Config()
    else:
        if not os.path.exists(args.config):
            print(f"Error: Config file {args.config} does not exist.", file=sys.stderr)
            return 1
        config = load_config(args.config)

    if command == "run":
        cmd_run(args, config)
    elif command == "scan":
        cmd_scan(args, config)
    elif command == "enroll":
        cmd_enroll(args, config)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
