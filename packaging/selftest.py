"""Startet ein gebautes FED-Paket im Selbsttest-Modus und prüft das Ergebnis.

    python packaging/selftest.py dist/FED-1.0.0-windows-x64.exe
    python packaging/selftest.py dist/FED-1.0.0-macos-arm64.zip
    python packaging/selftest.py dist/FED-1.0.0-linux-x64.tar.gz

Geprüft wird das Paket, das Nutzer herunterladen (entpackt in einen leeren Ordner):
Programm startet, findet seinen Ordner (dort liegen config.toml/FED.ini), der Programm-Hash
entspricht der Programmdatei, Übersetzung, Qt, PDF mit Schriften und Case-Datei funktionieren.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def unpack(archive: Path, dest: Path) -> tuple[Path, Path]:
    """Rückgabe: (Programmdatei, erwarteter Programmordner)."""
    name = archive.name
    if name.endswith(".exe"):
        exe = dest / "FED.exe"
        exe.write_bytes(archive.read_bytes())
        return exe, dest
    if name.endswith(".tar.gz"):
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="tar")
        return dest / "FED" / "FED", dest / "FED"
    if name.endswith(".zip"):  # macOS: ditto erhält Symlinks und Rechte des Bundles
        subprocess.run(["ditto", "-x", "-k", str(archive), str(dest)], check=True)
        app = dest / "FED.app"
        return next((app / "Contents" / "MacOS").glob("FED*")), dest
    raise SystemExit(f"unbekanntes Paket: {archive}")


def main(argv: list[str]) -> int:
    archive = Path(argv[0]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "paket"
        dest.mkdir()
        exe, expected_dir = unpack(archive, dest)
        out = Path(tmp) / "selftest.json"
        env = {**os.environ, "FED_SELFTEST": str(out), "QT_QPA_PLATFORM": "offscreen"}
        r = subprocess.run([str(exe)], env=env, cwd=tmp, capture_output=True, text=True, timeout=300)
        if not out.exists():
            print(r.stdout, r.stderr, sep="\n")
            print(f"FEHLER: kein Ergebnis (Rückgabewert {r.returncode})")
            return 1
        res = json.loads(out.read_text(encoding="utf-8"))
        print(json.dumps(res, indent=2))
        checks = {
            "Selbsttest ok": res.get("ok") is True,
            "Programmordner = Ordner der Programmdatei": Path(res.get("program_dir", "")).resolve()
            == expected_dir.resolve(),
            "Programm-Hash = SHA-256 der Programmdatei": res.get("tool_sha256") == sha256(exe),
            "Englische Übersetzung": res.get("translation_en") == "Settings",
        }
        for k, v in checks.items():
            print(f"{'OK  ' if v else 'FEHLER'}  {k}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
