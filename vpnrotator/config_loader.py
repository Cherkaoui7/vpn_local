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
        # 1. Check next to the running executable (if frozen via PyInstaller)
        if getattr(sys, 'frozen', False):
            candidate = Path(sys.executable).parent / settings_path
            if candidate.exists():
                settings_path = candidate
            else:
                # 2. Check in the default project folder
                fallback = Path(r"C:\Users\USER\Documents\vpn\vpn_local") / settings_path
                if fallback.exists():
                    settings_path = fallback
                else:
                    settings_path = settings_path.resolve()
        else:
            # 3. Running as script - check in the script's folder
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

    return Settings(
        project_dir=project_dir,
        openvpn_path=_resolve(project_dir, raw.get("openvpn_path")) or Path("openvpn.exe"),
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
