from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.request import urlopen, Request


def fetch_vpnbook_credentials(logger: logging.Logger) -> tuple[str, str] | None:
    url = "https://www.vpnbook.com/freevpn/openvpn"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        logger.info("Scraping des identifiants VPNBook sur %s...", url)
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Echec du telechargement des identifiants VPNBook: %s", exc)
        return None

    username_match = re.search(
        r'<label[^>]*>Username</label>\s*<div[^>]*>\s*<code[^>]*>([^<]+)</code>',
        html,
        re.IGNORECASE
    )
    password_match = re.search(
        r'<label[^>]*>Password</label>\s*<div[^>]*>\s*<code[^>]*>([^<]+)</code>',
        html,
        re.IGNORECASE
    )

    if username_match and password_match:
        username = username_match.group(1).strip()
        password = password_match.group(1).strip()
        logger.info("Identifiants scraped: Username='%s', Password=<masked>", username)
        return username, password

    logger.warning("Regex non concordante pour le username/password dans le HTML de VPNBook.")
    return None


def update_auth_file(auth_path: Path, username: str, password: str, logger: logging.Logger) -> bool:
    try:
        logger.info("Mise a jour du fichier auth: %s", auth_path)
        # Ensure parent directories exist
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_text(f"{username}\n{password}\n", encoding="utf-8")
        return True
    except OSError as exc:
        logger.error("Impossible d'ecrire dans le fichier auth %s: %s", auth_path, exc)
        return False
