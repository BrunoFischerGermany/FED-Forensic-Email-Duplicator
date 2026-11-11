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
    except Exception:
        user = "unknown"
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
    """Liegt ``path`` auf einem externen/Wechseldatenträger? ``None`` = unbekannt."""
    try:
        if sys.platform == "win32":
            return _removable_windows(path)
        if sys.platform == "darwin":
            return _removable_macos(path)
        return _removable_linux(path)
    except Exception:
        return None


def _removable_windows(path: Path) -> bool | None:
    import ctypes

    drive = Path(path).resolve().anchor  # z. B. "E:\\"
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive))
    # 2 = DRIVE_REMOVABLE (USB-Stick), 3 = DRIVE_FIXED (auch USB-Festplatten!), 4 = Netzlaufwerk
    if drive_type == 2:
        return True
    if drive_type == 3:
        return _windows_bus_is_usb(drive)
    return False


def no_console() -> dict:
    """Unter Windows kein Konsolenfenster für Hilfsprogramme (FED selbst hat keine Konsole,
    ohne diese Kennung öffnet Windows für PowerShell & Co. ein eigenes Fenster)."""
    import subprocess

    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _windows_bus_is_usb(drive: str) -> bool | None:
    """Fragt per PowerShell den Bustyp ab (USB-Festplatten melden sich als „fixed“)."""
    import subprocess

    letter = drive[0]
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
           f"(Get-Partition -DriveLetter {letter} | Get-Disk).BusType"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False,
                             **no_console()).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.upper() == "USB" if out else None


def _removable_macos(path: Path) -> bool | None:
    import plistlib
    import subprocess

    out = subprocess.run(["diskutil", "info", "-plist", str(path)], capture_output=True, timeout=10, check=False).stdout
    info = plistlib.loads(out)
    if info.get("RemovableMedia") or info.get("Ejectable"):
        return True
    if "Internal" in info:
        return not info["Internal"]
    return None


def _removable_linux(path: Path) -> bool | None:
    import os

    target = os.path.realpath(path)
    best = ("", "")
    with open("/proc/mounts", encoding="utf-8") as f:
        for line in f:
            dev, mnt = line.split()[:2]
            mnt = mnt.replace("\\040", " ")
            if (target == mnt or target.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best[1]):
                best = (dev, mnt)
    dev = best[0]
    if not dev.startswith("/dev/"):
        return None
    name = os.path.basename(os.path.realpath(dev))
    sys_path = os.path.realpath(f"/sys/class/block/{name}")
    if os.path.exists(os.path.join(sys_path, "partition")):
        sys_path = os.path.dirname(sys_path)
    if "/usb" in sys_path:
        return True
    rem = os.path.join(sys_path, "removable")
    if os.path.exists(rem):
        with open(rem) as f:
            return f.read().strip() == "1"
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
