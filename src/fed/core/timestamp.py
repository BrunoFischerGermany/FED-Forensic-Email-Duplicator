"""Qualifizierter Zeitstempel nach RFC 3161 für den ZIP-Hash (Antwort als .tsr)."""

from __future__ import annotations

from pathlib import Path


def request_timestamp(sha256_hex: str, tsa_url: str, out: Path, proxy: str = "") -> None:
    raise NotImplementedError("folgt in Meilenstein 5")
