import curses
import os
import json
import subprocess
import socket
import re
import sys
from pathlib import Path
from .config import load_config, write_yaml_atomic, HostConfig, PathConfig

class BekusupTUI:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config_data = self._load_data()
        self.stdscr = None
        self.colors = {}
        self.selected_index = 0
        self.scroll_offset = 0
        self.status_msg = ""
        self.status_color = 0

    def _load_data(self):
        if os.path.exists(self.config_path):
            try:
                import yaml
                with open(self.config_path, 'r') as f:
                    data = yaml.safe_load(f)
                if not data: return self._default_config()
                # Ensure structure
                if "hosts" not in data: data["hosts"] = []
                if "destination" not in data: data["destination"] = self._default_config()["destination"]
                if "run_policy" not in data: data["run_policy"] = self._default_config()["run_policy"]
                return data
            except Exception:
                return self._default_config()
        return self._default_config()

    def _default_config(self):
        return {
            "destination": {
                "label_contains": "backup",
                "fallback_mount_root": "/mnt/bekusup",
                "auto_unmount": False
            },
            "run_policy": {
                "min_free_space_gb": 20,
                "max_parallel_hosts": 2,
                "run_without_command": False
            },
            "hosts": []
        }

    def run(self):
        # Check for TERM and isatty
        if not sys.stdout.isatty():
            print("Not a TTY. TUI cannot run.")
            return
        
        try:
            curses.wrapper(self._main)
        except KeyboardInterrupt:
            pass

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)   # Header
        curses.init_pair(2, curses.COLOR_GREEN, -1)  # Success / Active
        curses.init_pair(3, curses.COLOR_YELLOW, -1) # Warning / Selection
        curses.init_pair(4, curses.COLOR_RED, -1)    # Error
        curses.init_pair(5, curses.COLOR_MAGENTA, -1)# Tailscale / Fancy
        curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE) # Selection Bar
        
        self.colors = {
            "header": curses.color_pair(1) | curses.A_BOLD,
            "active": curses.color_pair(2),
            "select": curses.color_pair(6),
            "warn": curses.color_pair(3),
            "error": curses.color_pair(4),
            "fancy": curses.color_pair(5)
        }

    def _draw_box(self, y, x, h, w, title=""):
        self.stdscr.attron(self.colors["header"])
        self.stdscr.hline(y, x, curses.ACS_HLINE, w)
        self.stdscr.hline(y + h - 1, x, curses.ACS_HLINE, w)
        self.stdscr.vline(y, x, curses.ACS_VLINE, h)
        self.stdscr.vline(y, x + w - 1, curses.ACS_VLINE, h)
        self.stdscr.addch(y, x, curses.ACS_ULCORNER)
        self.stdscr.addch(y, x + w - 1, curses.ACS_URCORNER)
        self.stdscr.addch(y + h - 1, x, curses.ACS_LLCORNER)
        self.stdscr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER)
        if title:
            self.stdscr.addstr(y, x + 2, f" {title} ")
        self.stdscr.attroff(self.colors["header"])

    def _draw_header(self):
        h, w = self.stdscr.getmaxyx()
        logo = " BEKUSUP FLEET MANAGER "
        self.stdscr.addstr(1, (w - len(logo)) // 2, logo, self.colors["fancy"] | curses.A_REVERSE)
        
        info = f" Config: {self.config_path} "
        self.stdscr.addstr(2, (w - len(info)) // 2, info, curses.A_DIM)

    def _draw_host_list(self):
        h, w = self.stdscr.getmaxyx()
        box_y, box_x = 4, 2
        box_h, box_w = h - 8, w - 4
        self._draw_box(box_y, box_x, box_h, box_w, "Hosts")
        
        hosts = self.config_data.get("hosts", [])
        if not hosts:
            msg = " No hosts configured. Press 'a' to add or 't' to discover. "
            self.stdscr.addstr(box_y + box_h // 2, (w - len(msg)) // 2, msg, self.colors["warn"])
            return

        list_h = box_h - 2
        list_w = box_w - 2
        
        for i in range(list_h):
            idx = i + self.scroll_offset
            if idx >= len(hosts):
                break
            
            host = hosts[idx]
            is_selected = (idx == self.selected_index)
            y = box_y + 1 + i
            x = box_x + 1
            
            style = self.colors["select"] if is_selected else curses.A_NORMAL
            line = f" {host.get('name', '???'):<15} | {host.get('transport', '???'):<6} | {host.get('uri', 'local'):<40} "
            self.stdscr.addstr(y, x, line[:list_w].ljust(list_w), style)

    def _draw_status(self):
        h, w = self.stdscr.getmaxyx()
        if self.status_msg:
            self.stdscr.addstr(h - 4, 2, f" Status: {self.status_msg} ", self.status_color)

    def _draw_footer(self):
        h, w = self.stdscr.getmaxyx()
        footer = " [a]Add  [e]Edit  [d]Del  [t]Tailscale  [v]Verify  [s]Save  [q]Quit "
        self.stdscr.addstr(h - 2, (w - len(footer)) // 2, footer, curses.A_REVERSE)

    def _set_status(self, msg, color_key="fancy"):
        self.status_msg = msg
        self.status_color = self.colors.get(color_key, 0)

    def _main(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        self._init_colors()
        
        while True:
            h, w = self.stdscr.getmaxyx()
            if h < 20 or w < 60:
                self.stdscr.erase()
                self.stdscr.addstr(0, 0, "Terminal too small!")
                self.stdscr.refresh()
                self.stdscr.getch()
                break

            self.stdscr.erase()
            self._draw_header()
            self._draw_host_list()
            self._draw_status()
            self._draw_footer()
            self.stdscr.refresh()
            
            key = self.stdscr.getch()
            if key == ord('q'):
                break
            elif key == curses.KEY_UP:
                self.selected_index = max(0, self.selected_index - 1)
                if self.selected_index < self.scroll_offset:
                    self.scroll_offset = self.selected_index
            elif key == curses.KEY_DOWN:
                hosts_count = len(self.config_data.get("hosts", []))
                self.selected_index = min(hosts_count - 1, self.selected_index + 1)
                list_h = h - 8 - 2
                if self.selected_index >= self.scroll_offset + list_h:
                    self.scroll_offset = self.selected_index - list_h + 1
            elif key == ord('a'):
                self._add_host_flow()
            elif key == ord('e'):
                self._edit_host_flow()
            elif key == ord('d'):
                self._delete_host_flow()
            elif key == ord('t'):
                self._tailscale_scan_flow()
            elif key == ord('v'):
                self._verify_host_flow()
            elif key == ord('s'):
                self._save_and_exit()
                self._set_status("Config saved!", "active")

    def _get_input(self, prompt, default=""):
        h, w = self.stdscr.getmaxyx()
        input_y = h - 4
        self.stdscr.move(input_y, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addstr(input_y, 2, f"{prompt} [{default}]: ", curses.A_BOLD)
        curses.echo()
        curses.curs_set(1)
        inp = self.stdscr.getstr().decode('utf-8').strip()
        curses.noecho()
        curses.curs_set(0)
        return inp if inp else default

    def _smart_guess_name(self):
        hosts = self.config_data.get("hosts", [])
        if not hosts:
            return socket.gethostname() or "host1"
        
        last_name = hosts[-1]["name"]
        match = re.search(r'(\d+)$', last_name)
        if match:
            num = int(match.group(1))
            prefix = last_name[:match.start()]
            return f"{prefix}{num + 1}"
        return f"{last_name}-2"

    def _smart_guess_uri(self, name):
        hosts = self.config_data.get("hosts", [])
        if not hosts:
            return f"ssh://root@{name}"
        
        # Try to find a pattern in previous URIs
        users = []
        for h in hosts:
            uri = h.get("uri")
            if uri and uri.startswith("ssh://"):
                raw = uri.replace("ssh://", "", 1)
                if "@" in raw:
                    user_pass, host_part = raw.split("@", 1)
                    if ":" in user_pass:
                        u, _password = user_pass.split(":", 1)
                        users.append(u)
                    else:
                        users.append(user_pass)
        
        user = "root"
        if users:
            user = max(set(users), key=users.count)
        
        return f"ssh://{user}@{name}"

    def _smart_guess_ssh_target(self):
        return self._smart_guess_uri(self._smart_guess_name()).replace("ssh://", "", 1)

    def _split_ssh_target(self, target):
        raw = target.replace("ssh://", "", 1).strip()
        user = None
        host_part = raw
        if "@" in raw:
            user_part, host_part = raw.rsplit("@", 1)
            user = user_part.split(":", 1)[0] if user_part else None

        if host_part.startswith("[") and "]" in host_part:
            name = host_part[1:host_part.index("]")]
        else:
            name = host_part.rsplit(":", 1)[0] if ":" in host_part else host_part

        return user, name or "host1", raw

    def _remote_from_ssh_target(self, raw_target):
        host_part = raw_target
        user_part = None
        password = None
        if "@" in raw_target:
            user_part, host_part = raw_target.rsplit("@", 1)
            if ":" in user_part:
                user, password = user_part.split(":", 1)
                user_part = user

        remote = f"{user_part}@{host_part}" if user_part else host_part
        return remote, password

    def _verify_ssh_target(self, raw_target):
        remote, password = self._remote_from_ssh_target(raw_target)
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", remote, "true"]
        if password:
            cmd = ["sshpass", "-p", password] + ["ssh", "-o", "ConnectTimeout=4", remote, "true"]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=8)
            return True
        except Exception:
            return False

    def _ssh_copy_id_command(self, raw_target):
        remote, _password = self._remote_from_ssh_target(raw_target)
        return ["ssh-copy-id", remote]

    def _install_ssh_key_interactive(self, raw_target):
        cmd = self._ssh_copy_id_command(raw_target)
        if self.stdscr is not None:
            curses.def_prog_mode()
            curses.endwin()
        try:
            print(f"Installing SSH key with: {' '.join(cmd)}")
            result = subprocess.run(cmd, check=False)
            input("Press Enter to return to bekusup...")
            return result.returncode == 0
        finally:
            if self.stdscr is not None:
                curses.reset_prog_mode()
                curses.curs_set(0)

    def _add_host_flow(self):
        target = self._get_input("SSH target", self._smart_guess_ssh_target())
        if not target: return

        user, name, raw_target = self._split_ssh_target(target)
        source_user = user or os.environ.get('USER', 'user')
        source = "/root" if source_user == "root" else f"/home/{source_user}"
        verified = self._verify_ssh_target(raw_target)
        if not verified:
            install = self._get_input("SSH key failed. Install key? (y/N)", "y")
            if install.lower() in ("y", "yes"):
                if self._install_ssh_key_interactive(raw_target):
                    verified = self._verify_ssh_target(raw_target)
        
        new_host = {
            "name": name,
            "transport": "ssh",
            "uri": f"ssh://{raw_target}",
            "paths": [{"source": source, "dest_subdir": "."}]
        }
        self.config_data["hosts"].append(new_host)
        self.selected_index = len(self.config_data["hosts"]) - 1
        if verified:
            self._set_status(f"Added {name}; SSH verified", "active")
        else:
            self._set_status(f"Added {name}; SSH not verified", "warn")

    def _edit_host_flow(self):
        hosts = self.config_data.get("hosts", [])
        if not hosts: return
        host = hosts[self.selected_index]
        
        host["name"] = self._get_input("Name", host["name"])
        host["transport"] = self._get_input("Transport", host["transport"])
        if host["transport"] == "ssh":
            host["uri"] = self._get_input("URI", host.get("uri", ""))
        else:
            host["uri"] = None
            
        if host["paths"]:
            p = host["paths"][0]
            p["source"] = self._get_input("Source", p["source"])
            p["dest_subdir"] = self._get_input("Dest Subdir", p["dest_subdir"])
        self._set_status(f"Updated {host['name']}", "active")

    def _delete_host_flow(self):
        hosts = self.config_data.get("hosts", [])
        if not hosts: return
        name = hosts[self.selected_index]["name"]
        confirm = self._get_input(f"Delete {name}? (y/N)", "n")
        if confirm.lower() in ("y", "yes"):
            hosts.pop(self.selected_index)
            self.selected_index = max(0, self.selected_index - 1)
            self._set_status(f"Deleted {name}", "warn")

    def _tailscale_scan_flow(self):
        self._set_status("Discovering Tailscale fleet...", "fancy")
        self.stdscr.refresh()
        
        try:
            res = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
            data = json.loads(res.stdout)
            peers = data.get("Peer", {})
            
            existing_names = {h["name"] for h in self.config_data["hosts"]}
            discovered = []
            for node_id, node in peers.items():
                name = node.get("HostName")
                if name and name not in existing_names:
                    ip = node.get("TailscaleIPs", [None])[0]
                    if ip:
                        discovered.append((name, ip))
            
            if not discovered:
                self._set_status("No new nodes found.", "warn")
                return

            ans = self._get_input(f"Found {len(discovered)} nodes. Add all? (Y/n)", "y")
            if ans.lower() in ("y", "yes"):
                for name, ip in discovered:
                    uri = self._smart_guess_uri(name)
                    self.config_data["hosts"].append({
                        "name": name,
                        "transport": "ssh",
                        "uri": uri,
                        "paths": [{"source": "/home/user", "dest_subdir": "."}]
                    })
                self._set_status(f"Added {len(discovered)} nodes.", "active")
            else:
                self._set_status("Discovery cancelled.", "warn")
                
        except Exception as e:
            self._set_status(f"Tailscale failed: {str(e)[:40]}", "error")

    def _verify_host_flow(self):
        hosts = self.config_data.get("hosts", [])
        if not hosts: return
        host = hosts[self.selected_index]
        
        self._set_status(f"Verifying {host['name']} credentials...", "fancy")
        self.stdscr.refresh()
        
        if host["transport"] == "local":
            if os.path.exists(host["paths"][0]["source"]):
                self._set_status("OK: Local path found.", "active")
            else:
                self._set_status("FAIL: Local path missing.", "error")
        else:
            # SSH Verify
            from .transports import get_provider
            paths = [PathConfig(source=p["source"], dest_subdir=p["dest_subdir"]) for p in host["paths"]]
            h_cfg = HostConfig(name=host["name"], transport=host["transport"], uri=host.get("uri"), paths=paths)
            provider = get_provider(h_cfg)
            remote, use_sshpass, password = provider.parse_uri()
            
            cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", remote, "true"]
            if use_sshpass and password:
                cmd = ["sshpass", "-p", password] + ["ssh", "-o", "ConnectTimeout=4", remote, "true"]
            
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=6)
                self._set_status("OK: SSH Access Verified.", "active")
            except Exception as e:
                self._set_status(f"FAIL: {str(e)[:40]}", "error")

    def _save_and_exit(self):
        write_yaml_atomic(self.config_path, self.config_data)

def start_tui(config_path):
    import sys
    app = BekusupTUI(config_path)
    app.run()

if __name__ == "__main__":
    import sys
    start_tui(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
