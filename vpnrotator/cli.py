from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config_loader import list_ovpn_configs, load_settings
from .logging_setup import setup_logging
from .scheduler import RotationScheduler
from .vpn_manager import VpnManager


DETACHED_PROCESS = 0x00000008 if sys.platform == "win32" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if sys.platform == "win32" else 0
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="VPN Rotator local pour OpenVPN sur Windows.",
    )
    parser.add_argument(
        "--settings",
        default="settings.json",
        help="Chemin du fichier settings.json.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("start", help="Demarre la rotation automatique en arriere-plan.")
    subparsers.add_parser("stop", help="Arrete le rotator et le processus OpenVPN actif.")
    subparsers.add_parser("status", help="Affiche l'etat enregistre.")
    subparsers.add_parser("list", help="Liste les fichiers .ovpn disponibles.")
    subparsers.add_parser("once", help="Connecte un serveur aleatoire sans rotation.")
    subparsers.add_parser("rotate", help="Force immediatement un changement de serveur.")
    subparsers.add_parser("gui", help="Lance l'interface graphique VpnPrivate.")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "run" in argv:
        return _run_background_worker(argv)

    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    logger = setup_logging(settings.logs_dir)
    manager = VpnManager(settings, logger)

    try:
        if args.command == "start":
            return _start_background(settings)

        if args.command == "stop":
            _stop_background(settings)
            manager.disconnect()
            return 0

        if args.command == "gui":
            from .gui import run_gui
            run_gui(Path(args.settings))
            return 0

        if args.command == "status":
            state = manager.read_state()
            if not state:
                print("Aucun VPN actif enregistre.")
            else:
                print("VPN connecte")
                print(f"Serveur : {state.config}")
                print(f"PID : {state.pid}")
                print(f"Depuis : {state.started_at}")
                if state.public_ip:
                    print(f"IP : {state.public_ip}")
                remaining = _format_remaining(state.next_rotation_at)
                if remaining:
                    print(f"Temps restant : {remaining}")
            rotator = _read_rotator_state(settings)
            rotator_pid = rotator.get("pid") if rotator else None
            if rotator_pid and _is_pid_running(int(rotator_pid)):
                print(f"Rotator : actif | PID {rotator_pid}")
            else:
                print("Rotator : arrete")
            return 0

        if args.command == "list":
            configs = list_ovpn_configs(settings.configs_dir)
            if not configs:
                print(f"Aucun fichier .ovpn dans {settings.configs_dir}")
            for config in configs:
                print(config.name)
            return 0

        if args.command == "once":
            from .config_loader import choose_config

            configs = list_ovpn_configs(settings.configs_dir)
            config = choose_config(configs, None, False, settings.selection_mode)
            manager.disconnect()
            manager.connect(config)
            return 0

        if args.command == "rotate":
            from .config_loader import choose_config

            current = manager.read_state()
            configs = list_ovpn_configs(settings.configs_dir)
            previous = current.config if current else None
            config = choose_config(configs, previous, settings.avoid_same_server, settings.selection_mode)
            manager.disconnect()
            manager.connect(config)
            print(f"Rotation forcee vers: {config.name}")
            return 0

    except Exception as exc:
        logger.error("%s", exc)
        return 1

    parser.print_help()
    return 2


def _format_remaining(next_rotation_at: str | None) -> str | None:
    if not next_rotation_at:
        return None
    try:
        target = datetime.fromisoformat(next_rotation_at)
    except ValueError:
        return None

    seconds = max(0, int((target - datetime.now()).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {seconds:02d} s"
    return f"{seconds} s"


def _rotator_state_path(settings) -> Path:
    return settings.project_dir / "rotator_state.json"


def _read_rotator_state(settings) -> dict:
    path = _rotator_state_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_rotator_state(settings, pid: int) -> None:
    path = _rotator_state_path(settings)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_rotator_state(settings) -> None:
    try:
        _rotator_state_path(settings).unlink()
    except FileNotFoundError:
        pass


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_background(settings) -> int:
    existing = _read_rotator_state(settings)
    existing_pid = int(existing.get("pid", 0) or 0)
    if existing_pid and _is_pid_running(existing_pid):
        print(f"Rotator deja actif | PID {existing_pid}")
        return 0

    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = settings.logs_dir / "rotator_background.log"
    log_file = log_path.open("ab")
    main_path = settings.project_dir / "main.py"
    settings_path = Path("settings.json").resolve()

    process = subprocess.Popen(
        [
            sys.executable,
            str(main_path),
            "--settings",
            str(settings_path),
            "run",
        ],
        cwd=str(settings.project_dir),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    log_file.close()
    _write_rotator_state(settings, process.pid)
    print(f"Rotator demarre en arriere-plan | PID {process.pid}")
    print("Tu peux fermer ce terminal. Utilise `python main.py stop` pour arreter le VPN.")
    return 0


def _stop_background(settings) -> None:
    state = _read_rotator_state(settings)
    pid = int(state.get("pid", 0) or 0)
    if not pid:
        _clear_rotator_state(settings)
        return

    if _is_pid_running(pid):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    _clear_rotator_state(settings)


def _run_background_worker(argv: list[str]) -> int:
    worker_args = [arg for arg in argv if arg != "run"]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--settings", default="settings.json")
    args, _ = parser.parse_known_args(worker_args)

    settings = load_settings(args.settings)
    logger = setup_logging(settings.logs_dir)
    manager = VpnManager(settings, logger)

    _write_rotator_state(settings, os.getpid())
    try:
        RotationScheduler(settings, manager, logger).run_forever()
        return 0
    except Exception as exc:
        logger.error("%s", exc)
        return 1
    finally:
        _clear_rotator_state(settings)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
