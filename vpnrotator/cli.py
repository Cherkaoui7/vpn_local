from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .config_loader import list_ovpn_configs, load_settings
from .logging_setup import setup_logging
from .scheduler import RotationScheduler
from .vpn_manager import VpnManager


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
    subparsers.add_parser("start", help="Demarre la rotation automatique.")
    subparsers.add_parser("stop", help="Arrete le processus OpenVPN actif.")
    subparsers.add_parser("status", help="Affiche l'etat enregistre.")
    subparsers.add_parser("list", help="Liste les fichiers .ovpn disponibles.")
    subparsers.add_parser("once", help="Connecte un serveur aleatoire sans rotation.")
    subparsers.add_parser("rotate", help="Force immediatement un changement de serveur.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = load_settings(args.settings)
    logger = setup_logging(settings.logs_dir)
    manager = VpnManager(settings, logger)

    try:
        if args.command == "start":
            RotationScheduler(settings, manager, logger).run_forever()
            return 0

        if args.command == "stop":
            manager.disconnect()
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
