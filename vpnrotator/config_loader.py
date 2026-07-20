from __future__ import annotations

import json
import random
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_dir: Path
    openvpn_path: Path
    configs_dir: Path
    auth_file: Path | None
    logs_dir: Path
    state_file: Path
    rotation_seconds: int
    selection_mode: str
    avoid_same_server: bool
    connect_timeout_seconds: int
    udp_connect_timeout_seconds: int
    public_ip_check: bool
    public_ip_url: str
    openvpn_extra_args: list[str]
    force_udp: bool


def _resolve(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = project_dir / path
    return path


def load_settings(path: str | Path = "settings.json") -> Settings:
    settings_path = Path(path)
    if not settings_path.is_absolute():
        if getattr(sys, 'frozen', False):
            # Standalone Executable mode: store all settings and configs in a persistent folder in the user's home directory
            app_dir = Path.home() / ".vpnprivate"
            app_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy settings.json from bundle if missing
            settings_dest = app_dir / "settings.json"
            if not settings_dest.exists():
                src = Path(sys._MEIPASS) / "settings.json"
                if src.exists():
                    import shutil
                    shutil.copy2(src, settings_dest)
                    
            # Sync configs folder from bundle
            configs_dest = app_dir / "configs"
            configs_dest.mkdir(parents=True, exist_ok=True)
            src_configs = Path(sys._MEIPASS) / "configs"
            if src_configs.exists():
                import shutil
                for item in src_configs.glob("*.ovpn"):
                    dest_file = configs_dest / item.name
                    # Copy if missing or if the size changed (updates from the bundle)
                    if not dest_file.exists() or dest_file.stat().st_size != item.stat().st_size:
                        shutil.copy2(item, dest_file)

            # Sync openvpn_bin folder from bundle
            openvpn_bin_dest = app_dir / "openvpn_bin"
            openvpn_bin_dest.mkdir(parents=True, exist_ok=True)
            src_openvpn_bin = Path(sys._MEIPASS) / "openvpn_bin"
            if src_openvpn_bin.exists():
                import shutil
                for item in src_openvpn_bin.glob("*"):
                    if item.is_file():
                        dest_file = openvpn_bin_dest / item.name
                        # Copy if missing or if the size changed
                        if not dest_file.exists() or dest_file.stat().st_size != item.stat().st_size:
                            shutil.copy2(item, dest_file)
            
            settings_path = settings_dest
        else:
            # Script / Dev mode: check in local workspace
            script_candidate = Path(__file__).resolve().parent.parent / settings_path
            if script_candidate.exists():
                settings_path = script_candidate
            else:
                settings_path = settings_path.resolve()
    else:
        settings_path = settings_path.resolve()

    project_dir = settings_path.parent

    with settings_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    if getattr(sys, 'frozen', False):
        default_openvpn = project_dir / "openvpn_bin" / "openvpn.exe"
    else:
        default_openvpn = Path(r"C:\Program Files\OpenVPN\bin\openvpn.exe")

    configured_path = _resolve(project_dir, raw.get("openvpn_path"))
    if not configured_path or not configured_path.exists() or "C:\\Program Files\\OpenVPN" in str(configured_path):
        openvpn_path = default_openvpn
    else:
        openvpn_path = configured_path

    return Settings(
        project_dir=project_dir,
        openvpn_path=openvpn_path,
        configs_dir=_resolve(project_dir, raw.get("configs_dir")) or project_dir / "configs",
        auth_file=_resolve(project_dir, raw.get("auth_file")),
        logs_dir=_resolve(project_dir, raw.get("logs_dir")) or project_dir / "logs",
        state_file=_resolve(project_dir, raw.get("state_file")) or project_dir / "vpn_state.json",
        rotation_seconds=int(raw.get("rotation_seconds", 1800)),
        selection_mode=str(raw.get("selection_mode", "random")).lower(),
        avoid_same_server=bool(raw.get("avoid_same_server", True)),
        connect_timeout_seconds=int(raw.get("connect_timeout_seconds", 25)),
        udp_connect_timeout_seconds=int(raw.get("udp_connect_timeout_seconds", 8)),
        public_ip_check=bool(raw.get("public_ip_check", True)),
        public_ip_url=str(raw.get("public_ip_url", "https://api.ipify.org")),
        openvpn_extra_args=list(raw.get("openvpn_extra_args", [])),
        force_udp=bool(raw.get("force_udp", True)),
    )


def list_ovpn_configs(configs_dir: Path) -> list[Path]:
    if not configs_dir.exists():
        return []
    return sorted(configs_dir.glob("*.ovpn"))


def extract_remote_host(ovpn_path: Path) -> tuple[str, int] | None:
    try:
        content = ovpn_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("remote "):
                parts = line.split()
                if len(parts) >= 3:
                    return parts[1], int(parts[2])
    except Exception:
        pass
    return None


def measure_latency(host: str, port: int, timeout: float = 0.5) -> float:
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return time.perf_counter() - start
    except Exception:
        return 999.0


def choose_config(
    configs: list[Path],
    previous: str | None,
    avoid_same: bool,
    selection_mode: str = "random",
) -> Path:
    if not configs:
        raise FileNotFoundError("Aucun fichier .ovpn trouve dans le dossier configs.")

    candidates = configs
    if avoid_same and previous and len(configs) > 1:
        candidates = [config for config in configs if config.name != previous] or configs

    if selection_mode == "latency":
        latencies = []
        for config in candidates:
            remote = extract_remote_host(config)
            if remote:
                host, port = remote
                lat = measure_latency(host, port)
                latencies.append((config, lat))
            else:
                latencies.append((config, 999.0))

        # Sort configs by measured latency and choose the fastest available server.
        latencies.sort(key=lambda x: x[1])
        return latencies[0][0]

    return random.choice(candidates)
