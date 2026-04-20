import os
import json
import time
from pathlib import Path
import threading

class IndexStore:
    def __init__(self, index_file="~/.local/share/bekusup/index.json"):
        self.index_file = Path(os.path.expanduser(index_file))
        self.data = {"drives": {}}
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r') as f:
                    self.data = json.load(f)
            except json.JSONDecodeError:
                pass

    def _save(self):
        self.index_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_file, 'w') as f:
            json.dump(self.data, f, indent=2)

    def enroll_drive(self, serial: str, uuid: str, label: str):
        if not serial and not uuid:
            raise ValueError("Refusing enrollment: Hardware returned no Serial or UUID.")
            
        with self.lock:
            drive_id = serial if serial else f"{uuid}-{label}"
            if drive_id not in self.data["drives"]:
                self.data["drives"][drive_id] = {
                    "serial": serial,
                    "uuid": uuid,
                    "label": label,
                    "first_seen": time.time(),
                    "last_seen": time.time(),
                    "sessions": {}
                }
            else:
                self.data["drives"][drive_id]["last_seen"] = time.time()
            self._save()
            return drive_id

    def get_drive(self, drive_id: str):
        with self.lock:
            return self.data["drives"].get(drive_id)
            
    def get_last_success_for_host(self, host_name: str):
        best_drive = None
        best_session = None
        best_time = 0

        with self.lock:
            for drive_id, drive_data in self.data["drives"].items():
                for session_id, session_data in drive_data.get("sessions", {}).items():
                    host_info = session_data.get("hosts", {}).get(host_name)
                    if host_info and host_info.get("status") in ("succeeded", "partial"):
                        ts = session_data.get("timestamp", 0)
                        if ts > best_time:
                            best_time = ts
                            best_drive = drive_id
                            best_session = session_id

        return best_drive, best_session, best_time

    def drives_with_successful_host(self, host_name: str):
        result = []
        with self.lock:
            for drive_id, drive_data in self.data["drives"].items():
                for session_data in drive_data.get("sessions", {}).values():
                    host_info = session_data.get("hosts", {}).get(host_name)
                    if host_info and host_info.get("status") in ("succeeded", "partial"):
                        result.append(drive_id)
                        break
        return result

    def most_recent_drive_for_host(self, host_name: str):
        drive_id, _, _ = self.get_last_success_for_host(host_name)
        return drive_id

    def log_session(self, drive_id: str, session_id: str, outcome: str, hosts_data: dict):
        with self.lock:
            if drive_id in self.data["drives"]:
                self.data["drives"][drive_id]["sessions"][session_id] = {
                    "timestamp": time.time(),
                    "outcome": outcome,
                    "hosts": hosts_data
                }
                self._save()

def get_disk_identity(disk_node):
    serial = disk_node.get("serial", "")
    uuid = disk_node.get("uuid", "")
    label = disk_node.get("label", "")
    return serial, uuid, label
    
def write_marker_file(mountpoint, serial, uuid, label):
    marker_path = os.path.join(mountpoint, ".bekusup-volume.json")
    data = {
        "enrollment_time": time.time(),
        "serial": serial,
        "uuid": uuid,
        "label": label
    }
    with open(marker_path, 'w') as f:
        json.dump(data, f, indent=2)

def read_marker_file(mountpoint):
    marker_path = os.path.join(mountpoint, ".bekusup-volume.json")
    if os.path.exists(marker_path):
        with open(marker_path, 'r') as f:
            return json.load(f)
    return None

def verify_trust(disk_node, mountpoint, index: IndexStore):
    marker = read_marker_file(mountpoint)
    serial, uuid, label = get_disk_identity(disk_node)
    drive_id = serial if serial else f"{uuid}-{label}"
    
    with index.lock:
        index_known = drive_id in index.data["drives"]
    
    if not marker:
        if index_known:
             return False, "Drive is in local index but missing marker file! Was formatting wiped? Re-enroll."
        return False, "No marker file found. Disk must be enrolled via 'bekusup enroll'."
        
    if not serial and not uuid:
        return False, "HARD REJECT: Hardware presents no Serial or UUID, collision hazard."
        
    if marker:
        if not index_known:
             return False, "Drive has marker file but is unrecognized by this machine's local index. Re-enroll to establish trust."
             
    if marker.get("serial") and serial and marker.get("serial") != serial:
        return False, f"HARD REJECT: Marker serial '{marker.get('serial')}' conflicts with actual serial '{serial}'."
        
    if marker.get("uuid") and uuid and marker.get("uuid") != uuid:
        return False, f"HARD REJECT: Marker UUID '{marker.get('uuid')}' conflicts with actual UUID '{uuid}'."
        
    with index.lock:
        index.data["drives"][drive_id]["last_seen"] = time.time()
        index._save()
        
    return True, ""
