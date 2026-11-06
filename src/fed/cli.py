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
    acq.add_argument("--delete-old", action="store_true", help="bei Neubeginn Altstand löschen statt verschieben")
    acq.add_argument("--server", help="IMAP-Server (sonst Autoerkennung)")
    acq.add_argument("--port", type=int, default=993)
    acq.add_argument("--security", choices=["ssl", "starttls"], default="ssl")
    acq.add_argument("--user", help="Benutzername (Standard: Adresse)")
    acq.add_argument("--cafile", help="eigene CA-Datei (z. B. Behörden-CA oder Testserver)")
    acq.add_argument("--exclude", action="append", default=[], metavar="ORDNER", help="Ordner auslassen (mehrfach)")
    acq.add_argument("--auth", choices=["auto", "password", "google", "microsoft"], default="auto",
                     help="Anmeldeart (auto: Google/Microsoft per OAuth, sonst Passwort)")
    acq.add_argument("--device-code", action="store_true", help="Microsoft: Anmeldung per Gerätecode")
    acq.add_argument("--login-as", help="OAuth: Konto, mit dem angemeldet wird (z. B. bei geteiltem Postfach)")

    fin = sub.add_parser("finish", help="Case abschließen: ZIP, Hashes, Berichte")
    fin.add_argument("--case-dir", type=Path, required=True)

    ver = sub.add_parser("verify", help="Hash-Kette und Dateien prüfen")
    ver.add_argument("--case-dir", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "new-case":
        from fed.core import config as cfgmod
        from fed.core import syscheck
        from fed.core.case import Case, CaseInfo

        cfg = cfgmod.load(args.config) if args.config else cfgmod.Config()
        examiner = args.examiner or cfg.examiner_name
        case = Case.create(args.target, CaseInfo(args.case, examiner, args.notes))
        info = syscheck.collect(args.target, cfg.programm.ntp_server if cfg.programm.ntp else None, cfg.proxy_url)
        case.record_system_check(info.as_dict())
        print(f"Case angelegt: {case.root}")
        print(f"System: {info.user} @ {info.hostname} · online={info.online} · extern={info.target_removable} "
              f"· frei {info.free_bytes / 1e9:.1f} GB · NTP-Abweichung {info.ntp_offset_s}")
        case.close()
        return 0
    if args.command == "acquire":
        return _acquire(args)
    if args.command == "verify":
        from fed.core.case import Case

        case = Case.open(args.case_dir)
        ok, bad = case.verify_log()
        case.close()
        print("Log-Hash-Kette: OK" if ok else f"Log-Hash-Kette GEBROCHEN ab Eintrag {bad}")
        return 0 if ok else 2
    print(f"Befehl '{args.command}' ist noch nicht umgesetzt.", file=sys.stderr)
    return 1


def _acquire(args) -> int:
    import getpass
    import os
    import time
    from datetime import UTC, datetime

    from fed.core import acquisition as acqmod
    from fed.core import autoconfig, oauth
    from fed.core import config as cfgmod
    from fed.core.auth import OAuthSession, log_oauth_login, make_connector
    from fed.core.cancel import CancelToken
    from fed.core.case import Case

    cfg = cfgmod.load(args.config) if args.config else cfgmod.Config()
    case = Case.open(args.case_dir)
    try:
        row = case.conn.execute("SELECT id, server, port, security, username FROM account WHERE email = ?",
                                (args.account,)).fetchone()
        found = None
        if row:
            acc_id, server, port, security, user = row
        else:
            if args.server:
                server, port, security, source = args.server, args.port, args.security, "manuell"
            else:
                found, log = autoconfig.discover(args.account, proxy=cfg.proxy_url)
                for line in log:
                    print("  " + line)
                if not found:
                    print("Keine IMAP-Einstellungen gefunden – bitte --server angeben.", file=sys.stderr)
                    return 1
                server, port, security, source = found.host, found.port, found.security, found.source
            user = args.user or args.account
        provider_hint = found.provider if found else None
        method = args.auth
        if method == "auto":
            if row:
                stored = case.conn.execute("SELECT auth_method FROM account WHERE id = ?", (acc_id,)).fetchone()[0]
                method = {"oauth_google": "google", "oauth_microsoft": "microsoft"}.get(stored, "password")
            elif server.lower() == "imap.gmail.com" or str(provider_hint) == "google":
                method = "google"
            elif server.lower() == "outlook.office365.com" or str(provider_hint) == "microsoft":
                method = "microsoft"
            else:
                method = "password"
        auth_method = {"google": "oauth_google", "microsoft": "oauth_microsoft"}.get(method, "password")
        timeout = cfg.programm.socket_timeout_s
        if method == "password":
            password = os.environ.get("FED_PASSWORD") or getpass.getpass(f"Passwort für {user}: ")
            connect = make_connector(server, port, security, user, password=password, timeout=timeout,
                                     cafile=args.cafile)
        else:
            if method == "google":
                prov, cid, secret = oauth.GOOGLE, cfg.oauth_google.client_id, cfg.oauth_google.client_secret
            else:
                prov, cid, secret = oauth.microsoft(cfg.oauth_microsoft.tenant), cfg.oauth_microsoft.client_id, ""
            sess = OAuthSession(prov, cid, secret, cfg.proxy_url)
            print(f"Anmeldung bei {method.title()} – " + ("Gerätecode folgt …" if args.device_code
                                                          else "Browser wird geöffnet …"))
            try:
                token = sess.login_interactive(
                    login_hint=args.login_as or args.account, device_code=args.device_code,
                    show_device_code=lambda dc: print(dc.message or f"Öffne {dc.verification_uri} und gib {dc.user_code} ein"))
            except oauth.OAuthError as e:
                case.log("ERROR", "oauth_failed", str(e))
                print(f"Anmeldung fehlgeschlagen: {e}", file=sys.stderr)
                return 1
            warning = log_oauth_login(case, token, args.account)
            print(f"Angemeldet als {token.account or '?'}")
            if warning:
                print(f"  WARNUNG: {warning}")
            connect = make_connector(server, port, security, user, oauth_session=sess, timeout=timeout,
                                     cafile=args.cafile)

        if not row:
            spec = acqmod.AccountSpec(
                args.account, args.evidence, server, port, security, user, auth_method=auth_method,
                autoconfig_source=source,
                date_from=datetime.fromisoformat(args.date_from).replace(tzinfo=UTC) if args.date_from else None,
                date_to=datetime.fromisoformat(args.date_to).replace(tzinfo=UTC) if args.date_to else None,
            )
            imap = connect()
            acc_id = acqmod.register_account(case, spec, imap)
            folders = acqmod.scan(case, acc_id, imap)
            imap.logout()
            for f in folders:
                print(f"  {f['name']:<40} {f['messages']:>7} Mails {f['size'] / 1e6:>9.1f} MB")
        if args.exclude:
            names = {r[0] for r in case.conn.execute(
                "SELECT display_name FROM folder WHERE account_id = ? AND selected = 1", (acc_id,))}
            acqmod.set_selection(case, acc_id, names - set(args.exclude))

        token = CancelToken()
        last = [0.0]

        def show(p) -> None:
            if time.monotonic() - last[0] >= 1 or p.mails_done == p.mails_total:
                last[0] = time.monotonic()
                bps, mpm = p.speed()
                eta = p.eta_seconds()
                print(f"\r  {p.mails_done}/{p.mails_total} Mails · {p.bytes_done / 1e6:.1f}/{p.bytes_total / 1e6:.1f} MB "
                      f"· {bps / 1e6:.1f} MB/s · {mpm:.0f} Mails/min · Rest {int(eta) if eta else '?'} s · {p.folder[:30]:<30}",
                      end="", flush=True)

        mode = acqmod.Mode.RESTART if args.force else acqmod.Mode.RESUME
        try:
            res = acqmod.run_attempt(case, acc_id, connect, mode, token, show, args.reason, args.delete_old, cfg)
        except KeyboardInterrupt:
            token.cancel()
            raise
        print()
        print(f"Versuch {res.nr}: {res.status} · gesichert {res.saved} · bereits vorhanden {res.skipped_existing} "
              f"· erwartet {res.expected}")
        for w in res.warnings:
            print(f"  WARNUNG: {w}")
        return 0 if res.status == "done" else 3
    finally:
        case.close()


if __name__ == "__main__":
    raise SystemExit(main())
