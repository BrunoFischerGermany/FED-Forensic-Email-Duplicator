"""Abbruch-Signal und Fortschrittsmeldungen.

Jede lange Operation bekommt ein ``CancelToken`` und prüft es nach jedem
Arbeitsblock (z. B. nach jeder Mail). Netzwerkzugriffe haben zusätzlich ein
Socket-Timeout, damit nichts unbegrenzt hängt.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class Cancelled(Exception):
    """Wird geworfen, wenn der Benutzer abgebrochen hat."""


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.requested_at: float | None = None

    def cancel(self) -> None:
        if not self._event.is_set():
            self.requested_at = time.time()
            self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled()


@dataclass
class Progress:
    phase: str = ""
    mails_done: int = 0
    mails_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    folder: str = ""
    folder_done: int = 0
    folder_total: int = 0
    current_uid: int | None = None
    warnings: int = 0
    errors: int = 0
    started: float = field(default_factory=time.monotonic)
    _window: list[tuple[float, int, int]] = field(default_factory=list, repr=False)

    def sample(self, window_s: float = 30.0) -> None:
        """Merkt den aktuellen Stand für den gleitenden Mittelwert."""
        now = time.monotonic()
        self._window.append((now, self.bytes_done, self.mails_done))
        while self._window and now - self._window[0][0] > window_s:
            self._window.pop(0)

    def speed(self) -> tuple[float, float]:
        """(Bytes/s, Mails/min) über das Zeitfenster."""
        if len(self._window) < 2:
            return 0.0, 0.0
        (t0, b0, m0), (t1, b1, m1) = self._window[0], self._window[-1]
        dt = max(t1 - t0, 1e-6)
        return (b1 - b0) / dt, (m1 - m0) / dt * 60

    def eta_seconds(self) -> float | None:
        bps, _ = self.speed()
        if bps <= 0:
            return None
        return max(self.bytes_total - self.bytes_done, 0) / bps


ProgressCallback = Callable[[Progress], None]
