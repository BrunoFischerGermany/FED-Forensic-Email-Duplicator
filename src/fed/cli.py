"""Kommandozeilen-Modus. Nutzt denselben Kern wie die GUI.

Passwörter nie als Parameter: Abfrage per ``getpass`` oder Umgebungsvariable
``FED_PASSWORD``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fed import APP_NAME, __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fed", description=APP_NAME)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", type=Path, help="Pfad zur config.toml")
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-case", help="Case anlegen")
    new.add_argument("--target", type=Path, required=True, help="Zielordner (Datenträger)")
    new.add_argument("--case", required=True, help="Casenumber")
    new.add_argument("--examiner", help="Examiner (Standard aus Config)")
    new.add_argument("--notes", default="")

    acq = sub.add_parser("acquire", help="Konto sichern")
    acq.add_argument("--case-dir", type=Path, required=True)
    acq.add_argument("--account", required=True, help="E-Mail-Adresse")
    acq.add_argument("--evidence", required=True, help="Evidence-/Asservatennummer")
    acq.add_argument("--from", dest="date_from", help="Zeitraum ab (JJJJ-MM-TT)")
    acq.add_argument("--to", dest="date_to", help="Zeitraum bis einschließlich (JJJJ-MM-TT)")
    mode = acq.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help="Fortsetzen (Standard)")
    mode.add_argument("--force", action="store_true", help="Neubeginn")
    acq.add_argument("--reason", default="", help="Grund für Neubeginn")

    fin = sub.add_parser("finish", help="Case abschließen: ZIP, Hashes, Berichte")
    fin.add_argument("--case-dir", type=Path, required=True)

    ver = sub.add_parser("verify", help="Hash-Kette und Dateien prüfen")
    ver.add_argument("--case-dir", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "new-case":
        from fed.core import config as cfgmod
        from fed.core.case import Case, CaseInfo

        cfg = cfgmod.load(args.config) if args.config else cfgmod.Config()
        examiner = args.examiner or cfg.examiner_name
        case = Case.create(args.target, CaseInfo(args.case, examiner, args.notes))
        print(f"Case angelegt: {case.root}")
        case.close()
        return 0
    if args.command == "verify":
        from fed.core.case import Case

        case = Case.open(args.case_dir)
        ok, bad = case.verify_log()
        case.close()
        print("Log-Hash-Kette: OK" if ok else f"Log-Hash-Kette GEBROCHEN ab Eintrag {bad}")
        return 0 if ok else 2
    print(f"Befehl '{args.command}' ist noch nicht umgesetzt.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
