import yaml
import sys
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class DestinationConfig:
    label_contains: str = "backup"
    fallback_mount_root: str = "/mnt/bekusup"
    auto_unmount: bool = False

@dataclass
class RunPolicyConfig:
    min_free_space_gb: int = 20
    incomplete_suffix: str = ".incomplete"
    complete_marker: str = "SESSION_COMPLETE"
    max_parallel_hosts: int = 2

@dataclass
class PathConfig:
    source: str
    dest_subdir: str

@dataclass
class HostConfig:
    name: str
    transport: str
    paths: List[PathConfig]
    uri: Optional[str] = None
    bandwidth_limit_kbps: int = 0

@dataclass
class Config:
    destination: DestinationConfig = field(default_factory=DestinationConfig)
    run_policy: RunPolicyConfig = field(default_factory=RunPolicyConfig)
    hosts: List[HostConfig] = field(default_factory=list)

def load_config(path: str) -> Config:
    try:
        with open(path, 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error loading config {path}: {e}", file=sys.stderr)
        sys.exit(1)

    if not data:
        data = {}

    dest_data = data.get("destination", {})
    dest = DestinationConfig(
        label_contains=dest_data.get("label_contains", "backup"),
        fallback_mount_root=dest_data.get("fallback_mount_root", "/mnt/bekusup"),
        auto_unmount=dest_data.get("auto_unmount", False)
    )

    policy_data = data.get("run_policy", {})
    policy = RunPolicyConfig(
        min_free_space_gb=policy_data.get("min_free_space_gb", 20),
        incomplete_suffix=policy_data.get("incomplete_suffix", ".incomplete"),
        complete_marker=policy_data.get("complete_marker", "SESSION_COMPLETE"),
        max_parallel_hosts=policy_data.get("max_parallel_hosts", 2)
    )

    hosts = []
    for h in data.get("hosts", []):
        paths = []
        for p in h.get("paths", []):
            paths.append(PathConfig(source=p["source"], dest_subdir=p["dest_subdir"]))
        
        host = HostConfig(
            name=h["name"],
            transport=h["transport"],
            paths=paths,
            uri=h.get("uri"),
            bandwidth_limit_kbps=h.get("bandwidth_limit_kbps", 0)
        )
        hosts.append(host)

    return Config(destination=dest, run_policy=policy, hosts=hosts)
