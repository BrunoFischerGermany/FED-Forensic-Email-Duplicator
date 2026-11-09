"""Anmeldedaten der laufenden Sitzung – nur im Arbeitsspeicher, nie auf der Festplatte, nie im Log.

Manchmal gibt die betroffene Person das Passwort nur einmal ein. Nach einem erfolgreichen
Verbindungstest merkt sich FED Passwort bzw. OAuth-Anmeldung je Konto, bis das Programm beendet
wird; wird das Konto erneut gewählt (Fortsetzen, weiteres Konto, neuer Case), ist es vorbelegt.
„Vergessen“ löscht einen Eintrag sofort, Beenden alle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Entry:
    method: str  # password | google_app | google | microsoft
    password: str | None
    oauth: object | None  # OAuthSession
    when: str  # Uhrzeit des letzten erfolgreichen Tests (lokal)

    def __repr__(self) -> str:  # nie das Passwort ausgeben (Tracebacks, Debugger)
        return f"Entry(method={self.method!r}, password={'***' if self.password else None}, when={self.when!r})"


class SessionVault:
    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}

    def put(self, email: str, method: str, password: str | None, oauth=None) -> None:
        if not email or (not password and oauth is None):
            return
        self._entries[email] = Entry(method, password, oauth, datetime.now().strftime("%H:%M"))

    def get(self, email: str) -> Entry | None:
        return self._entries.get(email)

    def forget(self, email: str) -> None:
        self._entries.pop(email, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, email: str) -> bool:
        return email in self._entries
