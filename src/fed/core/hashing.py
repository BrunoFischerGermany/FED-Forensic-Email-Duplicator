"""Hash-Berechnung: SHA-256 als Haupt-Hash, optional MD5/SHA-1/SHA-512."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

PRIMARY = "sha256"
_CHUNK = 1024 * 1024


class MultiHasher:
    """Berechnet mehrere Hashes in einem Durchlauf."""

    def __init__(self, extra: Iterable[str] = ()) -> None:
        names = [PRIMARY, *[e for e in extra if e != PRIMARY]]
        self._h = {n: hashlib.new(n) for n in names}

    def update(self, data: bytes) -> None:
        for h in self._h.values():
            h.update(data)

    def hexdigests(self) -> dict[str, str]:
        return {n: h.hexdigest() for n, h in self._h.items()}


def hash_bytes(data: bytes, extra: Iterable[str] = ()) -> dict[str, str]:
    mh = MultiHasher(extra)
    mh.update(data)
    return mh.hexdigests()


def hash_file(path: Path, extra: Iterable[str] = ()) -> dict[str, str]:
    mh = MultiHasher(extra)
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            mh.update(chunk)
    return mh.hexdigests()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool_fingerprint() -> str:
    """SHA-256, der die laufende Programmversion kennzeichnet.

    Gebautes Programm (Nuitka): Hash der Programmdatei. Aus dem Quelltext: Hash über alle
    Dateien des Pakets ``fed`` (relativer Pfad + Inhalt, sortiert), ohne ``__pycache__``.
    """
    import sys

    import fed

    if "__compiled__" in vars(fed) or getattr(sys, "frozen", False):
        return hash_file(Path(sys.argv[0]).resolve())["sha256"]
    root = Path(fed.__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}:
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()
