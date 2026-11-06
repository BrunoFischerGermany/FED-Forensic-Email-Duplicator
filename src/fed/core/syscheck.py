"""Systemprüfung: Benutzer, Rechner, Zeit, Internet, Datenträger, Speicher."""

from __future__ import annotations

import getpass
import platform
import shutil
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class SystemInfo:
    ts_utc: str
    user: str
    hostname: str
    os: str
    local_time: str
    utc_time: str
    tz: str
    ntp_offset_s: float | None
    online: bool
    proxy: str
    target_path: str
    target_removable: bool | None
    free_bytes: int

    def as_dict(self) -> dict:
        return asdict(self)


def current_user() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - z. B. ohne Login-Terminal
        user = "unbekannt"
    if sys.platform == "win32":
        import os

        domain = os.environ.get("USERDOMAIN")
        if domain:
            return f"{domain}\\{user}"
    return user


def os_description() -> str:
    return f"{platform.system()} {platform.release()} ({platform.version()}, {platform.machine()})"


def is_online(host: str = "1.1.1.1", port: int = 443, timeout: float = 3.0) -> bool:
    """Einfacher TCP-Test; wird alle 10 s für die Statusleiste aufgerufen."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ntp_offset(server: str, timeout: float = 3.0) -> float | None:
    """Abweichung der Rechnerzeit zur NTP-Zeit in Sekunden (SNTP, RFC 4330)."""
    import struct
    import time

    packet = b"\x1b" + 47 * b"\0"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(packet, (server, 123))
            data, _ = s.recvfrom(48)
            t3 = time.time()
    except OSError:
        return None
    secs, frac = struct.unpack("!II", data[40:48])
    server_time = secs - 2208988800 + frac / 2**32
    return server_time - (t0 + t3) / 2


def is_removable(path: Path) -> bool | None:
    """Liegt ``path`` auf einem Wechseldatenträger? ``None`` = unbekannt.

    TODO: Windows ``GetDriveTypeW``, Linux ``/sys/block/*/removable``,
    macOS ``diskutil info``.
    """
    return None


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def collect(target: Path, ntp_server: str | None, proxy: str = "") -> SystemInfo:
    now = datetime.now(UTC)
    local = now.astimezone()
    return SystemInfo(
        ts_utc=now.isoformat(timespec="milliseconds"),
        user=current_user(),
        hostname=socket.gethostname(),
        os=os_description(),
        local_time=local.isoformat(timespec="seconds"),
        utc_time=now.isoformat(timespec="seconds"),
        tz=str(local.tzinfo),
        ntp_offset_s=ntp_offset(ntp_server) if ntp_server else None,
        online=is_online(),
        proxy=proxy,
        target_path=str(target.resolve()),
        target_removable=is_removable(target),
        free_bytes=free_bytes(target),
    )
