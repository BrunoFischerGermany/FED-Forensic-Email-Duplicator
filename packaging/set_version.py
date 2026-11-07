"""Setzt die Versionsnummer für einen Build (nur im CI-Checkout, wird nicht eingecheckt).

    python packaging/set_version.py v1.2.0          # -> __version__ = "1.2.0"
    python packaging/set_version.py ""              # unverändert lassen

So tragen Programm, Berichte und Validierungsprotokoll eines Releases die Version des Tags.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INIT = Path(__file__).resolve().parent.parent / "src" / "fed" / "__init__.py"


def main(argv: list[str]) -> int:
    v = (argv[0] if argv else "").strip().removeprefix("v")
    if not v:
        print("Version unverändert")
        return 0
    if not re.fullmatch(r"[0-9][0-9A-Za-z.\-]*", v):
        print(f"ungültige Version: {v!r}")
        return 1
    text = INIT.read_text(encoding="utf-8")
    new, n = re.subn(r'^__version__ = "[^"]*"', f'__version__ = "{v}"', text, flags=re.M)
    if n != 1:
        print("__version__ nicht gefunden")
        return 1
    INIT.write_text(new, encoding="utf-8")
    print(f"__version__ = {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
