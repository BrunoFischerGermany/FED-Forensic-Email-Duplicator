"""Passwort aus dem macOS-Schlüsselbund lesen (nur auf dem laufenden Mac).

Apple Mail legt das IMAP-Passwort als „Internet-Passwort“ im Anmeldeschlüsselbund ab. Auf dem
angemeldeten Rechner lässt es sich über das Programm ``security`` lesen – macOS fragt dabei die
Person um Erlaubnis („FED möchte auf den Schlüsselbund zugreifen“). Das Passwort wird nur in den
Arbeitsspeicher geladen und nie gespeichert oder protokolliert (nur die Tatsache der Nutzung).

Ein Abzug/Image mit der Schlüsselbund-Datei plus Anmeldepasswort ist ein eigener, noch offener
Weg (siehe Roadmap); dieses Modul deckt nur den laufenden Mac ab.
"""

from __future__ import annotations

import subprocess
import sys


def available() -> bool:
    """Schlüsselbund-Zugriff nur auf macOS (mit dem Programm ``security``)."""
    return sys.platform == "darwin"


def internet_password(host: str, account: str, port: int | None = None, timeout: float = 20.0) -> str | None:
    """Internet-Passwort für ``host``/``account`` aus dem Anmeldeschlüsselbund (oder ``None``).

    Löst auf dem Mac den Zustimmungsdialog aus. ``None`` = nichts gefunden, Zugriff verweigert, oder
    nicht auf macOS. Wirft nicht; Fehler werden zu ``None``.
    """
    host = host.strip()
    account = account.strip()
    if not available() or not host or not account:
        return None
    cmd = ["security", "find-internet-password", "-s", host, "-a", account, "-w"]
    if port:
        cmd += ["-P", str(port)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        # Ohne Port erneut versuchen (Apple Mail speichert nicht immer den Port mit)
        if port:
            return internet_password(host, account, None, timeout)
        return None
    pw = res.stdout.rstrip("\n")
    return pw or None
