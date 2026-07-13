from __future__ import annotations

from urllib.error import URLError
from urllib.request import urlopen


def get_public_ip(url: str, timeout: int = 10) -> str | None:
    try:
        with urlopen(url, timeout=timeout) as response:
            value = response.read().decode("utf-8", errors="replace").strip()
    except (OSError, URLError):
        return None

    return value or None
