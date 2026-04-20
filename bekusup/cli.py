import argparse
import os
import subprocess
import sys
from .config import load_config


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
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def cmd_run(args, config):
    from .lock import RunLock
    from .scanner import get_verified_targets, ensure_mounted
    from .store import IndexStore, get_disk_identity
    from .session import SessionManager
    from .transports import get_provider
    import concurrent.futures

    with RunLock():
        print(f"Running bekusup with {len(config.hosts)} hosts configured.")
        store = IndexStore()
        
        targets = get_verified_targets(config, store)
        
        # Sort targets by freshness (newest completed session comes first)
        def freshness(target):
            disk, mp, serial, uuid, label = target
            drive_id = serial if serial else f"{uuid}-{label}"
            info = store.get_drive(drive_id)
            if not info or not info.get("sessions"):
                return 0
            
            # Fetch all completion times
            completed_times = []
            for s in info["sessions"].values():
                if s.get("outcome") == "complete":
                    completed_times.append(s.get("timestamp", 0))
                    
            return max(completed_times) if completed_times else 0
            
        targets = sorted(targets, key=freshness, reverse=True)
        host_cache_bases = {}
        
        # Sequentially loop the drives
        for disk, mp, serial, uuid, label in targets:
            drive_id = serial if serial else f"{uuid}-{label}"
            print(f"\n>>> Executing Run on Target: /dev/{disk.get('name')} [label: {label}] (Drive: {drive_id})")
            
            session = SessionManager(mp, config, store, drive_id)
            if not session.begin_session():
                continue

            def sync_host(host):
                print(f"Starting backup for host: {host.name}")
                if not is_host_online(host.uri):
                    status = "unreachable"
                    session.record_host_status(host.name, status, "Failed 2-second reachability probe.")
                    print(f"Skipping host {host.name} [{status}]")
                    return status

                foreign_snapshot_base = host_cache_bases.get(host.name)
                if not session.snapshot_base and foreign_snapshot_base:
                    print(f"Using Cross-Drive Cache (Copy-Dest) for {host.name} from {os.path.basename(foreign_snapshot_base)}")

                provider = get_provider(host)
                host_dest = session.get_host_dest_dir(host.name)
                os.makedirs(host_dest, exist_ok=True)
                
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
                            
                    res = provider.sync(path_cfg.source, dest_path, snapshot_base=host_snapshot_base)
                    if not res:
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
                futures = {executor.submit(sync_host, h): h for h in config.hosts}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        host = futures[future]
                        print(f"Exception backing up {host.name}: {e}")
                        session.record_host_status(host.name, "failed", str(e))
                        
            session.finalize()
            if session.manifest["outcome"] in ("complete", "complete_with_warnings"):
                final_dir = os.path.join(session.sessions_dir, session.timestamp)
                for h_name, h_stat in session.manifest["hosts"].items():
                    if h_stat["status"] in ("succeeded", "partial"):
                        host_cache_bases[h_name] = final_dir

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
    
    for c in candidates:
        print("\n-----------------------------")
        print(f"💽 Target: /dev/{c.get('name')}")
        print(f"   Label: {c.get('label')}")
        print(f"   Size:  {c.get('size')}")
        print(f"   Mount: {c.get('mountpoints') or c.get('mountpoint')}")
        
        serial, uuid, label = get_disk_identity(c)
        drive_id = serial if serial else f"{uuid}-{label}"
        info = store.get_drive(drive_id)
        
        if info:
            print(f"   Trust: ENROLLED ✅")
            sessions = info.get('sessions', {})
            print(f"   Session History: {len(sessions)} records")
            
            if sessions:
                last_session = sorted(sessions.keys())[-1]
                s_data = sessions[last_session]
                print(f"   Latest Run: {last_session} [{s_data.get('outcome')}]")
                host_statuses = s_data.get('hosts', {})
                success_count = sum(1 for h in host_statuses.values() if h['status'] == 'succeeded')
                print(f"   Coverage: {success_count}/{len(host_statuses)} Hosts Available")
        else:
            print("   Trust: UNENROLLED ❌ (Requires `bekusup enroll` to reconcile database)")
    print("\n-----------------------------")
    print(" 📡 GLOBAL HOST COVERAGE")
    print("-----------------------------")
    for host in config.hosts:
        d_id, sid, stime = store.get_last_success_for_host(host.name)
        if d_id:
            import datetime
            time_str = datetime.datetime.fromtimestamp(stime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"   [+] {host.name}: Drive '{d_id}'  ({time_str})")
        else:
            print(f"   [-] {host.name}: No successful backups found locally.")
    print("================================")

def cmd_enroll(args, config):
    from .scanner import resolve_target_disk, ensure_mounted
    from .store import write_marker_file, IndexStore, get_disk_identity
    print("Enrolling new backup disk...")
    disk = resolve_target_disk(config)
    print(f"Target selected: /dev/{disk.get('name')} [label: {disk.get('label')}]")
    mp = ensure_mounted(disk, config.destination.fallback_mount_root)

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

    write_marker_file(mp, serial, uuid, label)
    store = IndexStore()
    try:
        drive_id = store.enroll_drive(serial, uuid, label)
    except ValueError as e:
        print(f"Enrollment refused: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Enrolled successfully! Drive ID recorded locally as: {drive_id}")

def main():
    parser = argparse.ArgumentParser(description="Bekusup - Tape-Drive Style Rotating Backup")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Run command
    parser_run = subparsers.add_parser("run", help="Perform one backup cycle against the detected eligible disk")
    
    # Scan command
    parser_scan = subparsers.add_parser("scan", help="Report eligible disks, mount state, free space, and the most recent session")
    
    # Enroll command
    parser_enroll = subparsers.add_parser("enroll", help="Perform one-time approval of a new backup disk")

    args = parser.parse_args()
    
    config = load_config(args.config)
    
    if args.command == "run":
        cmd_run(args, config)
    elif args.command == "scan":
        cmd_scan(args, config)
    elif args.command == "enroll":
        cmd_enroll(args, config)
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
