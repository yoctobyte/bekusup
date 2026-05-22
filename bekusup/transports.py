import subprocess
import sys
import os

class SyncProvider:
    def __init__(self, host_config):
        self.host = host_config

    def sync(self, source: str, dest: str, snapshot_base: str = None, dry_run: bool = False) -> bool:
        raise NotImplementedError

    def parse_uri(self):
        uri = self.host.uri or ""
        use_sshpass = False
        password = None
        user_host = uri.replace("ssh://", "", 1)
        
        if "@" in user_host and ":" in user_host.split("@")[0]:
            user_pass, host_part = user_host.split("@", 1)
            user, password = user_pass.split(":", 1)
            remote = f"{user}@{host_part}"
            use_sshpass = True
        else:
            remote = user_host
            
        return remote, use_sshpass, password

class RsyncProvider(SyncProvider):
    def sync(self, source: str, dest: str, snapshot_base: str = None, dry_run: bool = False) -> bool:
        remote, use_sshpass, password = self.parse_uri()
        cmd = ["rsync", "-aH", "--info=progress2", "--delete"]
        if dry_run:
            cmd.append("--dry-run")

        if self.host.transport == "ssh" and not use_sshpass:
            cmd.extend(["-e", "ssh -o BatchMode=yes"])
        
        if self.host.bandwidth_limit_kbps > 0:
            cmd.append(f"--bwlimit={self.host.bandwidth_limit_kbps}")
            
        if snapshot_base and os.path.exists(snapshot_base):
            compare_path = dest
            if not os.path.exists(compare_path):
                compare_path = os.path.dirname(compare_path) or "."
            if not dry_run:
                os.makedirs(dest, exist_ok=True)
                compare_path = dest
            if os.stat(snapshot_base).st_dev == os.stat(compare_path).st_dev:
                cmd.append(f"--link-dest={snapshot_base}")
            else:
                cmd.append(f"--copy-dest={snapshot_base}")
            
        remote_source = f"{remote}:{source}" if self.host.transport == "ssh" else source
        
        # Ensure destination dir exists
        if not dry_run:
            os.makedirs(dest, exist_ok=True)
        
        # rsync logic: we want to sync the *contents* or the folder into dest.
        # Adding a trailing slash to source means "copy contents of"
        if not remote_source.endswith('/'):
            remote_source += '/'
            
        dest_abs = os.path.abspath(dest)
        if not dest_abs.endswith('/'):
            dest_abs += '/'
            
        cmd.extend([remote_source, dest_abs])
        
        exec_cmd = cmd
        if use_sshpass and password:
            exec_cmd = ["sshpass", "-p", password] + cmd
            
        print_cmd = " ".join(["sshpass", "-p", "***"] + cmd if use_sshpass else cmd)
        print(f"[Rsync] Executing: {print_cmd}")
        
        try:
            subprocess.run(exec_cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            err_msg = str(e).replace(password, "***") if use_sshpass and password else str(e)
            print(f"Rsync failed for {self.host.name}: {err_msg}", file=sys.stderr)
            return False

class ScpProvider(SyncProvider):
    def sync(self, source: str, dest: str, snapshot_base: str = None, dry_run: bool = False) -> bool:
        if dry_run:
            print(f"[Scp] Dry-run requested for {self.host.name}; SCP fallback cannot preview safely.")
            return True
        remote, use_sshpass, password = self.parse_uri()
        cmd = ["scp", "-r"]
        if self.host.transport == "ssh" and not use_sshpass:
            cmd.extend(["-o", "BatchMode=yes"])
        
        if self.host.bandwidth_limit_kbps > 0:
            cmd.extend(["-l", str(self.host.bandwidth_limit_kbps * 8)])
            
        remote_source = f"{remote}:{source}" if self.host.transport == "ssh" else source
        
        dest_abs = os.path.abspath(dest)
        if not dest_abs.endswith('/'):
            dest_abs += '/'
        os.makedirs(dest_abs, exist_ok=True)
        cmd.extend([remote_source, dest_abs])
        
        exec_cmd = cmd
        if use_sshpass and password:
            exec_cmd = ["sshpass", "-p", password] + cmd
            
        print_cmd = " ".join(["sshpass", "-p", "***"] + cmd if use_sshpass else cmd)
        print(f"[Scp] Executing: {print_cmd}")
        
        try:
            subprocess.run(exec_cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            err_msg = str(e).replace(password, "***") if use_sshpass and password else str(e)
            print(f"SCP failed for {self.host.name}: {err_msg}", file=sys.stderr)
            return False

def get_provider(host_config):
    if host_config.transport == "local":
        return RsyncProvider(host_config)
    # Ideally we'd probe if rsync exists, but typically we try rsync and could fallback
    # For MVP we default to RsyncProvider for ssh unless explicitly "scp" transport
    if host_config.transport == "scp":
        return ScpProvider(host_config)
    return RsyncProvider(host_config)
