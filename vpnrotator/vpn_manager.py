from __future__ import annotations

import json
import logging
import subprocess
import time
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config_loader import Settings
from .ip_check import get_public_ip
from .credentials import fetch_vpnbook_credentials, update_auth_file
from .notification import send_desktop_notification


@dataclass
class VpnState:
    pid: int
    config: str
    started_at: str
    next_rotation_at: str | None = None
    public_ip: str | None = None


class VpnManager:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger

    def validate(self) -> None:
        if not self.settings.openvpn_path.exists():
            raise FileNotFoundError(
                f"OpenVPN introuvable: {self.settings.openvpn_path}. "
                "Installe OpenVPN ou modifie openvpn_path dans settings.json."
            )
        if self.settings.auth_file and not self.settings.auth_file.exists():
            raise FileNotFoundError(f"Fichier auth introuvable: {self.settings.auth_file}")

    def connect(
        self,
        config_path: Path,
        next_rotation_at: datetime | None = None,
        retry_on_auth_fail: bool = True,
        use_udp: bool = True,
    ) -> VpnState:
        self.validate()
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)

        # Get pre-VPN IP
        pre_ip = None
        if self.settings.public_ip_check:
            self.logger.info("Recuperation de l'IP publique pre-VPN...")
            pre_ip = get_public_ip(self.settings.public_ip_url)
            self.logger.info("IP pre-VPN: %s", pre_ip or "Inconnue")

        # Check if we should override protocol to UDP
        attempt_udp = self.settings.force_udp and use_udp

        effective_config_path = self._build_udp_config(config_path) if attempt_udp else config_path

        log_path = self.settings.logs_dir / f"openvpn-{config_path.stem}.log"
        log_path.write_text("", encoding="utf-8")
        log_file = log_path.open("ab")

        command = [
            str(self.settings.openvpn_path),
            "--config",
            str(effective_config_path),
        ]

        if self.settings.auth_file:
            command.extend(["--auth-user-pass", str(self.settings.auth_file)])

        command.extend(self.settings.openvpn_extra_args)

        self.logger.info("Connexion VPN (%s): %s", "UDP" if attempt_udp else "TCP", config_path.name)
        process = subprocess.Popen(
            command,
            cwd=str(self.settings.project_dir),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0,
        )

        state = VpnState(
            pid=process.pid,
            config=config_path.name,
            started_at=datetime.now().isoformat(timespec="seconds"),
            next_rotation_at=next_rotation_at.isoformat(timespec="seconds") if next_rotation_at else None,
        )
        self._write_state(state)

        # Monitor connection progress
        start_time = time.time()
        timeout = self.settings.udp_connect_timeout_seconds if attempt_udp else self.settings.connect_timeout_seconds
        connected = False
        auth_failed = False

        self.logger.info("Attente de l'initialisation du VPN...")
        while time.time() - start_time < timeout:
            log_content = self._log_contains_content(log_path)
            if "Initialization Sequence Completed" in log_content:
                connected = True
                break
            if "AUTH_FAILED" in log_content:
                auth_failed = True
                break
            if process.poll() is not None:
                break

            time.sleep(0.5)

        # Final safety check for logs in case process exited just now
        if not connected and not auth_failed:
            log_content = self._log_contains_content(log_path)
            if "AUTH_FAILED" in log_content:
                auth_failed = True

        try:
            log_file.close()
        except Exception:
            pass

        # Handle failure cases
        if not connected or process.poll() is not None or auth_failed:
            # Kill process group
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                pass
            self._clear_state()

            # If UDP connection failed (and it's not a credential auth failure), try TCP fallback
            if attempt_udp and not auth_failed:
                self.logger.warning("Echec de connexion en UDP. Repli automatique sur TCP...")
                return self.connect(
                    config_path,
                    next_rotation_at,
                    retry_on_auth_fail=retry_on_auth_fail,
                    use_udp=False,
                )

            if auth_failed:
                if retry_on_auth_fail and self.settings.auth_file:
                    self.logger.warning("Authentification refusee. Tentative de scraping des identifiants...")
                    creds = fetch_vpnbook_credentials(self.logger)
                    if creds:
                        username, password = creds
                        old_creds = ""
                        if self.settings.auth_file.exists():
                            old_creds = self.settings.auth_file.read_text(encoding="utf-8")

                        if f"{username}\n{password}" not in old_creds:
                            if update_auth_file(self.settings.auth_file, username, password, self.logger):
                                self.logger.info("Identifiants mis a jour. Nouvelle tentative de connexion...")
                                return self.connect(config_path, next_rotation_at, retry_on_auth_fail=False)
                        else:
                            self.logger.warning("Les identifiants scraped sont identiques aux actuels. Echec reel.")
                    else:
                        self.logger.error("Echec du scraping des identifiants.")

                send_desktop_notification(
                    "VPN Rotator - Erreur Authentification",
                    f"Authentification refusee pour : {config_path.name}",
                    self.logger
                )
                raise RuntimeError(
                    f"Authentification refusee par OpenVPN ({config_path.name}). "
                    "Mets a jour auth.txt avec le nouveau mot de passe VPNBook."
                )

            send_desktop_notification(
                "VPN Rotator - Erreur Connexion",
                f"Echec de connexion pour : {config_path.name}",
                self.logger
            )
            raise RuntimeError(
                f"OpenVPN s'est arrete pendant la connexion ou a expire ({config_path.name}). "
                f"Consulte {log_path}."
            )

        # Get post-VPN IP and verify routing
        post_ip = None
        if self.settings.public_ip_check:
            for attempt in range(3):
                post_ip = get_public_ip(self.settings.public_ip_url)
                if post_ip:
                    break
                time.sleep(1.0)

            if post_ip:
                state.public_ip = post_ip
                self._write_state(state)
                self.logger.info("IP publique detectee: %s", post_ip)

                if pre_ip and post_ip == pre_ip:
                    # Routing error: kill the process and clean state
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    except Exception:
                        pass
                    self._clear_state()
                    send_desktop_notification(
                        "VPN Rotator - Erreur Routage",
                        f"L'IP publique n'a pas change ({pre_ip}). Connexion annulee.",
                        self.logger
                    )
                    raise RuntimeError(
                        f"Erreur de routage VPN : l'IP publique n'a pas change ({pre_ip}). "
                        "Le trafic n'est pas redirige via le tunnel VPN."
                    )
            else:
                self.logger.warning("Impossible de recuperer l'IP publique apres connexion.")

        self.logger.info("Processus OpenVPN actif: PID %s", process.pid)
        
        # Success Notification
        msg = f"Serveur : {config_path.name}"
        if state.public_ip:
            msg += f"\nNouvelle IP : {state.public_ip}"
        send_desktop_notification("VPN Rotator - Connecte", msg, self.logger)
        
        return state

    def disconnect(self) -> None:
        state = self.read_state()
        if not state:
            self.logger.info("Aucune connexion OpenVPN enregistree.")
            return

        self.logger.info("Deconnexion VPN: PID %s (%s)", state.pid, state.config)
        result = subprocess.run(
            ["taskkill", "/PID", str(state.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.logger.warning("taskkill a retourne %s: %s", result.returncode, result.stderr.strip())
        
        send_desktop_notification(
            "VPN Rotator - Deconnecte",
            f"Deconnexion du serveur : {state.config}",
            self.logger
        )
        self._clear_state()

    def read_state(self) -> VpnState | None:
        if not self.settings.state_file.exists():
            return None
        try:
            data = json.loads(self.settings.state_file.read_text(encoding="utf-8"))
            return VpnState(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def _write_state(self, state: VpnState) -> None:
        self.settings.state_file.write_text(
            json.dumps(asdict(state), indent=2),
            encoding="utf-8",
        )

    def _clear_state(self) -> None:
        try:
            self.settings.state_file.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _build_udp_config(config_path: Path) -> Path:
        content = config_path.read_text(encoding="utf-8", errors="replace")
        updated_lines: list[str] = []
        saw_proto = False

        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("proto "):
                updated_lines.append("proto udp")
                saw_proto = True
                continue
            if stripped.startswith("remote "):
                parts = stripped.split()
                if len(parts) >= 2:
                    updated_lines.append(f"remote {parts[1]} 25000")
                    continue
            updated_lines.append(line)

        if not saw_proto:
            updated_lines.insert(0, "proto udp")

        temp_dir = Path(tempfile.gettempdir()) / "vpnrotator"
        temp_dir.mkdir(parents=True, exist_ok=True)
        udp_config = temp_dir / f"{config_path.stem}-udp.ovpn"
        udp_config.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
        return udp_config

    @staticmethod
    def _log_contains(path: Path, needle: str) -> bool:
        try:
            return needle in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    @staticmethod
    def _log_contains_content(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
