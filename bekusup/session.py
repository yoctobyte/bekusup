import os
import json
import re
import threading
from datetime import datetime

class SessionManager:
    def __init__(self, target_mount, config, store, drive_id, dry_run=False):
        self.target_mount = target_mount
        self.config = config
        self.store = store
        self.drive_id = drive_id
        self.dry_run = dry_run
        self.lock = threading.Lock()
        self.sessions_dir = target_mount
        
        self.timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.session_name = self.timestamp
        self.session_dir = target_mount
        
        self.manifest = {
            "timestamp": self.timestamp,
            "drive_id": self.drive_id,
            "hosts": {},
            "outcome": "incomplete"
        }
        self.snapshot_base = None
        self.host_dirs = {}
        self.final_host_dirs = {}

    def _sanitize_path_part(self, value):
        cleaned = re.sub(r"[^A-Za-z0-9_.@+-]+", "_", value.strip())
        return cleaned.strip("._") or "host"

    def _host_identity(self, host):
        if getattr(host, "transport", None) == "ssh" and getattr(host, "uri", None):
            raw = host.uri.replace("ssh://", "", 1)
            user = None
            host_part = raw
            if "@" in raw:
                user_part, host_part = raw.rsplit("@", 1)
                user = user_part.split(":", 1)[0] if user_part else None
            if host_part.startswith("[") and "]" in host_part:
                host_name = host_part[1:host_part.index("]")]
            else:
                host_name = host_part.rsplit(":", 1)[0] if ":" in host_part else host_part
            return user or "unknown", host_name or getattr(host, "name", "host")

        return os.environ.get("USER") or "local", getattr(host, "name", "localhost")

    def host_folder_prefix(self, host):
        user, host_name = self._host_identity(host)
        return f"{self._sanitize_path_part(user)}@{self._sanitize_path_part(host_name)}"

    def _incomplete_host_dir(self, host):
        return os.path.join(
            self.target_mount,
            f"{self.host_folder_prefix(host)}T{self.timestamp}{self.config.run_policy.incomplete_suffix}",
        )

    def _final_host_dir(self, host):
        return os.path.join(
            self.target_mount,
            f"{self.host_folder_prefix(host)}T{self.timestamp}",
        )
        
    def find_snapshot_base(self, host):
        if not os.path.isdir(self.target_mount):
            return None
        complete_marker = self.config.run_policy.complete_marker
        prefix = f"{self.host_folder_prefix(host)}T"
        candidates = []
        for d in os.listdir(self.target_mount):
            if not d.startswith(prefix) or d.endswith(self.config.run_policy.incomplete_suffix):
                continue
            dpath = os.path.join(self.target_mount, d)
            if os.path.isdir(dpath) and os.path.exists(os.path.join(dpath, complete_marker)):
                candidates.append(d)
        if candidates:
            candidates.sort() # Sort lexicographically by timestamp
            return os.path.join(self.target_mount, candidates[-1])
        return None

    def get_snapshot_base_for_host(self, host):
        return self.find_snapshot_base(host)

    def begin_session(self):
        st = os.statvfs(self.target_mount)
        free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        if free_gb < self.config.run_policy.min_free_space_gb:
            print(f"Error: Target drive has only {free_gb:.2f}GB free. Minimum required is {self.config.run_policy.min_free_space_gb}GB.")
            return False

        if self.dry_run:
            print(f"Dry-run target root would be: {self.target_mount}")
            return True
        
        return True

    def get_host_dest_dir(self, host):
        host_name = getattr(host, "name", str(host))
        incomplete_dir = self._incomplete_host_dir(host)
        final_dir = self._final_host_dir(host)
        self.host_dirs[host_name] = incomplete_dir
        self.final_host_dirs[host_name] = final_dir
        if self.dry_run:
            return incomplete_dir
        os.makedirs(incomplete_dir, exist_ok=True)
        return incomplete_dir
        
    def record_host_status(self, host_name, status, details=None):
        with self.lock:
            self.manifest["hosts"][host_name] = {"status": status, "details": details}

    def finalize(self):
        with self.lock:
            all_hosts_success = True
            for host, status_obj in self.manifest["hosts"].items():
                if status_obj["status"] != "succeeded":
                    all_hosts_success = False

            self.manifest["outcome"] = "complete" if all_hosts_success else "complete_with_warnings"

            if self.dry_run:
                print(f"Dry-run session outcome: {self.manifest['outcome']}")
                return

            for host_name, status_obj in self.manifest["hosts"].items():
                host_dir = self.host_dirs.get(host_name)
                final_dir = self.final_host_dirs.get(host_name)
                if not host_dir or not final_dir:
                    continue

                manifest_path = os.path.join(host_dir, "manifest.json")
                with open(manifest_path, 'w') as f:
                    json.dump(
                        {
                            **self.manifest,
                            "host": host_name,
                            "host_status": status_obj,
                        },
                        f,
                        indent=2,
                    )

                if status_obj["status"] in ("succeeded", "partial"):
                    marker_path = os.path.join(host_dir, self.config.run_policy.complete_marker)
                    with open(marker_path, 'w') as f:
                        f.write(self.timestamp)

                os.rename(host_dir, final_dir)
            
            self.store.log_session(self.drive_id, self.timestamp, self.manifest["outcome"], self.manifest["hosts"])
            print(f"Session {self.timestamp} finalized with status: {self.manifest['outcome']}")
