"""Ablauf der Sicherung: Versuche, Download, Fortsetzen/Neubeginn, ZIP, Verifikation.

1. Versuch anlegen (Fortsetzen oder Neubeginn mit Grund)
2. Vorher-Status je Ordner (MESSAGES, UNSEEN, UIDNEXT, UIDVALIDITY)
3. Je Ordner fehlende UIDs laden -> ``arbeit/`` (.part, dann umbenennen),
   Hashes, ``os.utime`` = Eingangszeit, Commit je Mail
4. Nachher-Status je Ordner, Soll/Ist
5. Beim Abschluss des Case: ZIP packen (Zeitstempel + Extended Timestamp),
   neu öffnen und alle Hashes prüfen, ZIP-Hashes, ``.sha256``, RFC-3161
"""

from __future__ import annotations

from enum import StrEnum

from fed.core.cancel import CancelToken, ProgressCallback
from fed.core.case import Case


class Mode(StrEnum):
    RESUME = "resume"
    RESTART = "restart"


def run_attempt(
    case: Case,
    account_id: int,
    mode: Mode,
    password_or_token: str,
    cancel: CancelToken,
    progress: ProgressCallback | None = None,
    reason: str = "",
    delete_old: bool = False,
) -> int:
    """Führt einen Versuch für ein Konto aus und gibt die Versuchs-ID zurück."""
    raise NotImplementedError("folgt in Meilenstein 2")


def build_zip(case: Case, cancel: CancelToken, progress: ProgressCallback | None = None) -> None:
    """Packt alle Konten aus ``arbeit/`` in ein ZIP und verifiziert es."""
    raise NotImplementedError("folgt in Meilenstein 4")
