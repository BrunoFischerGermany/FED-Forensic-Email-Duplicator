"""Rechner während einer Sicherung wach halten: kein Ruhezustand, keine Bildschirmsperre durch Leerlauf.

- Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)``
- macOS: ``caffeinate -dimsu -w <pid>`` (endet spätestens mit FED)
- Linux: ``gnome-session-inhibit`` bzw. ``systemd-inhibit`` (Leerlauf, Ruhezustand)

Grenzen: Eine per Gruppenrichtlinie erzwungene Sperre oder das Zuklappen eines
Laptops lässt sich je nach System nicht verhindern; FED meldet das im Log.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class KeepAwake:
    """Verschachtelbar: erst das letzte ``release()`` gibt den Rechner wieder frei."""

    def __init__(self, reason: str = "FED: forensische Sicherung läuft") -> None:
        self.reason = reason
        self.method: str | None = None
        self._proc: subprocess.Popen | None = None
        self._count = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------ öffentlich
    def acquire(self) -> str | None:
        """Aktiviert das Wachhalten und gibt das verwendete Verfahren zurück (``None`` = nicht möglich)."""
        with self._lock:
            self._count += 1
            if self._count == 1:
                try:
                    self.method = self._start()
                except Exception:
                    self.method = None
            return self.method

    def release(self) -> None:
        with self._lock:
            if self._count == 0:
                return
            self._count -= 1
            if self._count == 0:
                self._stop()
                self.method = None

    @property
    def active(self) -> bool:
        return self._count > 0

    def __enter__(self) -> KeepAwake:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    # ------------------------------------------------------------ intern
    def _start(self) -> str | None:
        if sys.platform == "win32":
            import ctypes

            r = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            return "SetThreadExecutionState" if r else None
        if sys.platform == "darwin":
            if shutil.which("caffeinate"):
                self._proc = subprocess.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "caffeinate"
            return None
        for cmd, name in (
            (["gnome-session-inhibit", "--inhibit", "idle:suspend", "--reason", self.reason,
              "sleep", "infinity"], "gnome-session-inhibit"),
            (["systemd-inhibit", "--what=idle:sleep:handle-lid-switch", "--who=FED", f"--why={self.reason}",
              "--mode=block", "sleep", "infinity"], "systemd-inhibit"),
        ):
            if shutil.which(cmd[0]):
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    proc.wait(0.5)  # sofort beendet = nicht erlaubt/kein Dienst
                except subprocess.TimeoutExpired:
                    self._proc = proc
                    return name
        return None

    def _stop(self) -> None:
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


_shared = KeepAwake()


def shared() -> KeepAwake:
    """Gemeinsame Instanz für das ganze Programm."""
    return _shared
