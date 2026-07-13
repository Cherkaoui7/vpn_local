from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .config_loader import Settings, choose_config, list_ovpn_configs
from .vpn_manager import VpnManager


class RotationScheduler:
    def __init__(self, settings: Settings, manager: VpnManager, logger: logging.Logger):
        self.settings = settings
        self.manager = manager
        self.logger = logger

    def run_forever(self) -> None:
        previous_config: str | None = None
        configs = list_ovpn_configs(self.settings.configs_dir)

        if not configs:
            raise FileNotFoundError(f"Aucun .ovpn dans {self.settings.configs_dir}")

        self.logger.info("%s serveur(s) OpenVPN trouve(s).", len(configs))
        self.logger.info("Rotation toutes les %s secondes.", self.settings.rotation_seconds)

        try:
            while True:
                config = choose_config(
                    configs,
                    previous_config,
                    self.settings.avoid_same_server,
                    self.settings.selection_mode,
                )
                previous_config = config.name
                next_rotation_at = datetime.now() + timedelta(seconds=self.settings.rotation_seconds)

                self.manager.disconnect()
                self.manager.connect(config, next_rotation_at=next_rotation_at)

                self.logger.info("Prochaine rotation dans %s secondes.", self.settings.rotation_seconds)
                time.sleep(self.settings.rotation_seconds)
        except KeyboardInterrupt:
            self.logger.info("Arret demande par Ctrl+C.")
            self.manager.disconnect()
