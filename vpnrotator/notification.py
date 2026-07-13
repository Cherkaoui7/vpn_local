from __future__ import annotations

import logging
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def send_desktop_notification(title: str, message: str, logger: logging.Logger | None = None) -> None:
    if sys.platform != "win32":
        return

    # Escape double quotes for PowerShell string
    safe_title = title.replace('"', '`"')
    safe_message = message.replace('"', '`"')

    code = f"""
[void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
$notification = New-Object System.Windows.Forms.NotifyIcon
$notification.Icon = [System.Drawing.SystemIcons]::Information
$notification.BalloonTipTitle = "{safe_title}"
$notification.BalloonTipText = "{safe_message}"
$notification.Visible = $True
$notification.ShowBalloonTip(5000)
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", code],
            capture_output=True,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        if logger:
            logger.warning("Impossible d'envoyer la notification de bureau: %s", exc)
