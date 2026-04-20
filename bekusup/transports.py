import subprocess
import sys
import os

class SyncProvider:
    def __init__(self, host_config):
        self.host = host_config

    def sync(self, source: str, dest: str, snapshot_base: str = None) -> bool:
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
    def sync(self, source: str, dest: str, snapshot_base: str = None) -> bool:
        remote, use_sshpass, password = self.parse_uri()
        cmd = ["rsync", "-aH", "--info=progress2", "--delete"]
        
        if self.host.bandwidth_limit_kbps > 0:
            cmd.append(f"--bwlimit={self.host.bandwidth_limit_kbps}")
            
        if snapshot_base and os.path.exists(snapshot_base):
            # Evaluate spatial storage capacity dynamically via block id
            os.makedirs(dest, exist_ok=True)
            if os.stat(snapshot_base).st_dev == os.stat(dest).st_dev:
                cmd.append(f"--link-dest={snapshot_base}")
            else:
                cmd.append(f"--copy-dest={snapshot_base}")
            
        remote_source = f"{remote}:{source}" if self.host.transport == "ssh" else source
        
        # Ensure destination dir exists
        os.makedirs(dest, exist_ok=True)
        
        # rsync logic: we want to sync the *contents* or the folder into dest.
        # Adding a trailing slash to source means "copy contents of"
        if not remote_source.endswith('/'):
            remote_source += '/'
            
        cmd.extend([remote_source, dest])
        
        exec_cmd = cmd
        if use_sshpass and password:
            exec_cmd = ["sshpass", "-p", password] + cmd
            
        print_cmd = " ".join(["sshpass", "-p", "***"] + cmd if use_sshpass else cmd)
        print(f"[Rsync] Executing: {print_cmd}")
        
        try:
            subprocess.run(exec_cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Rsync failed for {self.host.name}: {e}", file=sys.stderr)
            return False

class ScpProvider(SyncProvider):
    def sync(self, source: str, dest: str, snapshot_base: str = None) -> bool:
        remote, use_sshpass, password = self.parse_uri()
        # SCP doesn't do snapshot logic easily, we fallback to pure full copy
        # Also bandwidth limit options exist for scp: -l kpbs
        cmd = ["scp", "-r"]
        
        if self.host.bandwidth_limit_kbps > 0:
            # scp limit is in Kbit/s
            cmd.extend(["-l", str(self.host.bandwidth_limit_kbps * 8)])
            
        remote_source = f"{remote}:{source}" if self.host.transport == "ssh" else source
        
        os.makedirs(dest, exist_ok=True)
        cmd.extend([remote_source, dest])
        
        exec_cmd = cmd
        if use_sshpass and password:
            exec_cmd = ["sshpass", "-p", password] + cmd
            
        print_cmd = " ".join(["sshpass", "-p", "***"] + cmd if use_sshpass else cmd)
        print(f"[Scp] Executing: {print_cmd}")
        
        try:
            subprocess.run(exec_cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"SCP failed for {self.host.name}: {e}", file=sys.stderr)
            return False

def get_provider(host_config):
    if host_config.transport == "local":
        return RsyncProvider(host_config)
    # Ideally we'd probe if rsync exists, but typically we try rsync and could fallback
    # For MVP we default to RsyncProvider for ssh unless explicitly "scp" transport
    if host_config.transport == "scp":
        return ScpProvider(host_config)
    return RsyncProvider(host_config)
