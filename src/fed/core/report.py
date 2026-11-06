"""TXT- und PDF-Bericht aus der Case-Datei (PDF mit fpdf2, Logo im Kopf)."""

from __future__ import annotations

from pathlib import Path

from fed.core.case import Case


def write_txt(case: Case, path: Path, language: str = "de") -> None:
    raise NotImplementedError("folgt in Meilenstein 4")


def write_pdf(case: Case, path: Path, logo: Path | None = None, language: str = "de") -> None:
    raise NotImplementedError("folgt in Meilenstein 4")


def write_log_csv(case: Case, path: Path) -> None:
    """Liste aller gesicherten Mails (Ordner, UID, Eingangszeit, Absender, Betreff, Hashes)."""
    raise NotImplementedError("folgt in Meilenstein 4")
