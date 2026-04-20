import os
import json
from datetime import datetime

class SessionManager:
    def __init__(self, target_mount, config, store, drive_id):
        self.target_mount = target_mount
        self.config = config
        self.store = store
        self.drive_id = drive_id
        
        self.sessions_dir = os.path.join(target_mount, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        
        self.timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.session_name = f"{self.timestamp}{self.config.run_policy.incomplete_suffix}"
        self.session_dir = os.path.join(self.sessions_dir, self.session_name)
        
        self.manifest = {
            "timestamp": self.timestamp,
            "drive_id": self.drive_id,
            "hosts": {},
            "outcome": "incomplete"
        }
        self.snapshot_base = None
        
    def find_snapshot_base(self):
        complete_marker = self.config.run_policy.complete_marker
        candidates = []
        for d in os.listdir(self.sessions_dir):
            if d.endswith(self.config.run_policy.incomplete_suffix):
                continue
            dpath = os.path.join(self.sessions_dir, d)
            if os.path.isdir(dpath) and os.path.exists(os.path.join(dpath, complete_marker)):
                candidates.append(d)
        if candidates:
            candidates.sort() # Sort lexicographically by timestamp
            return os.path.join(self.sessions_dir, candidates[-1])
        return None

    def begin_session(self):
        # Check free space
        st = os.statvfs(self.target_mount)
        free_gb = (st.f_bavail * st.f_frsize) / (1024**3)
        if free_gb < self.config.run_policy.min_free_space_gb:
            print(f"Error: Target drive has only {free_gb:.2f}GB free. Minimum required is {self.config.run_policy.min_free_space_gb}GB.")
            return False
            
        os.makedirs(self.session_dir, exist_ok=True)
        self.snapshot_base = self.find_snapshot_base()
        if self.snapshot_base:
            print(f"Using previous session as snapshot base: {os.path.basename(self.snapshot_base)}")
        
        return True

    def get_host_dest_dir(self, host_name):
        return os.path.join(self.session_dir, host_name)
        
    def record_host_status(self, host_name, status, details=None):
        self.manifest["hosts"][host_name] = {"status": status, "details": details}

    def finalize(self):
        # Assume if we reached finalize, the run wasn't critically interrupted.
        all_hosts_success = True
        for host, status in self.manifest["hosts"].items():
            if status["status"] != "succeeded":
                all_hosts_success = False

        self.manifest["outcome"] = "complete" if all_hosts_success else "complete_with_warnings"
        
        manifest_path = os.path.join(self.session_dir, "manifest.json")
        with open(manifest_path, 'w') as f:
            json.dump(self.manifest, f, indent=2)
            
        # Write complete marker
        marker_path = os.path.join(self.session_dir, self.config.run_policy.complete_marker)
        with open(marker_path, 'w') as f:
            f.write(self.timestamp)
            
        final_dir = os.path.join(self.sessions_dir, self.timestamp)
        os.rename(self.session_dir, final_dir)
        
        self.store.log_session(self.drive_id, self.timestamp, self.manifest["outcome"])
        print(f"Session {self.timestamp} finalized with status: {self.manifest['outcome']}")
