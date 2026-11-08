"""Verschlüsselte Kopie für die Weitergabe (AES-256, WinZip-AES-Format).

Das geprüfte Original-ZIP bleibt unverändert – sein Hash ist der Beweiswert. Die Kopie
``<Casenumber>_encrypted.zip`` enthält Original-ZIP(s), deren ``.sha256``, ggf. Zeitstempel
(``.tsr``/``.tsq``), ``log*.csv`` und die Berichte. Der Empfänger entpackt (7-Zip, WinRAR, Keka,
The Unarchiver …) und prüft den Hash des Original-ZIPs.

Das Passwort wird nirgends gespeichert oder protokolliert; protokolliert werden nur Verfahren,
Dateiname, enthaltene Dateien und der SHA-256 der Kopie.
"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

METHOD = "AES-256 (WinZip AES, AE-2)"
MIN_LENGTH = 12
# ohne leicht verwechselbare Zeichen (0/O, 1/l/I)
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_password(groups: int = 5, size: int = 5) -> str:
    """Zufälliges Passwort, gut abzulesen/abzutippen, z. B. ``Kd7mP-x3QwZ-…`` (≈ 140 Bit)."""
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(size)) for _ in range(groups))


def check_password(password: str) -> str:
    """Leerer Text = in Ordnung, sonst Begründung."""
    if len(password) < MIN_LENGTH:
        return f"mindestens {MIN_LENGTH} Zeichen"
    if len(set(password)) < 6:
        return "zu wenig verschiedene Zeichen"
    return ""


def files_for_copy(case_root: Path, zip_paths: list[Path]) -> list[Path]:
    """Original-ZIPs mit Begleitdateien, CSV-Listen und Berichte."""
    files: list[Path] = []
    for z in zip_paths:
        files.append(z)
        for suffix in (".sha256", ".tsr", ".tsq"):
            p = z.with_name(z.name + suffix)
            if p.exists():
                files.append(p)
    for pattern in ("log*.csv", "report*.pdf", "report*.txt"):
        files += sorted(p for p in case_root.glob(pattern) if p not in files)
    return files


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def create_copy(target: Path, files: list[Path], password: str) -> dict:
    """Schreibt die verschlüsselte Kopie und prüft sie (entschlüsseln und jede Datei vergleichen).

    Rückgabe: ``{"file", "sha256", "size", "files": [...], "verified": bool, "method"}``."""
    import pyzipper

    problem = check_password(password)
    if problem:
        raise ValueError(f"Passwort ungeeignet: {problem}")
    part = target.with_name(target.name + ".part")
    expected = {p.name: _sha256(p) for p in files}
    with pyzipper.AESZipFile(part, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode("utf-8"))
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        for p in files:
            zf.write(p, p.name)
    part.replace(target)
    verified = True
    with pyzipper.AESZipFile(target) as zf:
        zf.setpassword(password.encode("utf-8"))
        names = zf.namelist()
        if sorted(names) != sorted(expected):
            verified = False
        for name in names:
            h = hashlib.sha256()
            with zf.open(name) as f:
                while chunk := f.read(1 << 20):
                    h.update(chunk)
            if h.hexdigest() != expected.get(name):
                verified = False
    return {"file": target.name, "sha256": _sha256(target), "size": target.stat().st_size, "files": names,
            "verified": verified, "method": METHOD}
