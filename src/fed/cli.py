"""Kommandozeilen-Modus. Nutzt denselben Kern wie die GUI.

Passwörter nie als Parameter: Abfrage per ``getpass`` oder Umgebungsvariable
``FED_PASSWORD``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fed import APP_NAME, __version__
from fed.core.msg import describe, set_language, t


def _choose_language(argv: list[str] | None) -> str:
    """Sprache der Kommandozeile: --lang, sonst FED_LANG, sonst ``sprache`` aus --config, sonst Deutsch."""
    import os

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    pre.add_argument("--config", type=Path)
    known, _ = pre.parse_known_args(argv)
    if known.lang:
        return known.lang
    if os.environ.get("FED_LANG"):
        return os.environ["FED_LANG"]
    if known.config and known.config.exists():
        try:
            from fed.core import config as cfgmod

            return cfgmod.load(known.config).programm.sprache
        except Exception:  # Fehler in der Config meldet später der eigentliche Befehl
            pass
    return "de"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fed", description=APP_NAME)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", type=Path, help=t("Pfad zur config.toml"))
    p.add_argument("--lang", choices=["de", "en"], help=t("Sprache der Ausgabe (Standard: FED_LANG oder config.toml)"))
    sub = p.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new-case", help=t("Case anlegen"))
    new.add_argument("--target", type=Path, required=True, help=t("Zielordner (Datenträger)"))
    new.add_argument("--case", required=True, help="Casenumber")
    new.add_argument("--examiner", help=t("Examiner (Standard aus Config)"))
    new.add_argument("--notes", default="")

    acq = sub.add_parser("acquire", help=t("Konto sichern"))
    acq.add_argument("--case-dir", type=Path, required=True)
    acq.add_argument("--account", required=True, help=t("E-Mail-Adresse"))
    acq.add_argument("--evidence", required=True, help=t("Evidence-/Asservatennummer"))
    acq.add_argument("--account-notes", default=None, help=t("Notiz zum Konto (erscheint im Bericht)"))
    acq.add_argument("--from", dest="date_from", help=t("Zeitraum ab (JJJJ-MM-TT)"))
    acq.add_argument("--to", dest="date_to", help=t("Zeitraum bis einschließlich (JJJJ-MM-TT)"))
    for opt, key, what in (("--from-contains", "from", t("Absender")),
                           ("--to-contains", "to", t("Empfänger (To/CC/BCC)")),
                           ("--subject-contains", "subject", t("Betreff")), ("--text-contains", "text", t("Text"))):
        acq.add_argument(opt, dest=f"flt_{key}", action="append", default=[], metavar=t("WERT"),
                         help=t("Teilsicherung: {what} enthält (mehrfach = oder)", what=what))
    mode = acq.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true", help=t("Fortsetzen (Standard)"))
    mode.add_argument("--force", action="store_true", help=t("Neubeginn"))
    acq.add_argument("--reason", default="", help=t("Grund für Neubeginn"))
    acq.add_argument("--delete-old", action="store_true", help=t("bei Neubeginn Altstand löschen statt verschieben"))
    acq.add_argument("--server", help=t("IMAP-Server (sonst Autoerkennung)"))
    acq.add_argument("--port", type=int, default=993)
    acq.add_argument("--security", choices=["ssl", "starttls"], default="ssl")
    acq.add_argument("--user", help=t("Benutzername (Standard: Adresse)"))
    acq.add_argument("--cafile", help=t("eigene CA-Datei (z. B. Behörden-CA oder Testserver)"))
    acq.add_argument("--exclude", action="append", default=[], metavar=t("ORDNER"), help=t("Ordner auslassen (mehrfach)"))
    acq.add_argument("--gmail-all-folders", action="store_true",
                     help=t("Gmail: alle Label-Ordner sichern (Standard: Alle Nachrichten + Spam + Papierkorb)"))
    acq.add_argument("--auth", choices=["auto", "password", "google-app", "google", "microsoft"], default="auto",
                     help=t("Anmeldeart (auto: Google per OAuth, ohne Client-ID per App-Passwort; Microsoft per "
                            "OAuth; sonst Passwort)"))
    acq.add_argument("--device-code", action="store_true", help=t("Microsoft: Anmeldung per Gerätecode"))
    acq.add_argument("--login-as", help=t("OAuth: Konto, mit dem angemeldet wird (z. B. bei geteiltem Postfach)"))

    fin = sub.add_parser("finish", help=t("Case abschließen: ZIP, Hashes, Berichte"))
    fin.add_argument("--case-dir", type=Path, required=True)
    wd = fin.add_mutually_exclusive_group()
    wd.add_argument("--delete-work", action="store_true", help=t("work/ nach verifiziertem ZIP löschen"))
    wd.add_argument("--keep-work", action="store_true", help=t("work/ behalten"))
    enc = fin.add_mutually_exclusive_group()
    enc.add_argument("--encrypt", action="store_true",
                     help=t("verschlüsselte Kopie (AES-256); Passwort aus FED_ZIP_PASSWORD oder Abfrage"))
    enc.add_argument("--encrypt-generate", action="store_true",
                     help=t("verschlüsselte Kopie mit erzeugtem Passwort (wird einmal angezeigt)"))
    fin.add_argument("--closing-note", metavar=t("TEXT"),
                     help=t("Abschlussvermerk für den Bericht (z. B. Übergabe, Besonderheiten)"))
    fin.add_argument("--zip", choices=["gesamt", "je-konto"], default=None,
                     help=t("mehrere Konten: ein ZIP für alle oder eines je Konto (Standard: Wahl im Case, sonst config)"))

    ver = sub.add_parser("verify", help=t("Hash-Kette und Dateien prüfen"))
    ver.add_argument("--case-dir", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    set_language(_choose_language(argv))
    args = build_parser().parse_args(argv)
    if args.command == "new-case":
        from fed.core import config as cfgmod
        from fed.core import syscheck
        from fed.core.case import Case, CaseInfo

        cfg = cfgmod.load(args.config) if args.config else cfgmod.Config()
        examiner = args.examiner or cfg.examiner_name
        case = Case.create(args.target, CaseInfo(args.case, examiner, args.notes),
                           replace_spaces=cfg.programm.leerzeichen_ersetzen)
        info = syscheck.collect(args.target, cfg.programm.ntp_server if cfg.programm.ntp else None, cfg.proxy_url)
        case.record_system_check(info.as_dict())
        case.store_config_snapshot(cfg.raw_text, cfg.logo_path())
        print(t("Case angelegt: {path}", path=case.root))
        print(t("System: {user} @ {host} · online={online} · extern={removable} · frei {free:.1f} GB · "
                "NTP-Abweichung {ntp}", user=info.user, host=info.hostname, online=info.online,
                removable=info.target_removable, free=info.free_bytes / 1e9, ntp=info.ntp_offset_s))
        case.close()
        return 0
    if args.command == "acquire":
        return _acquire(args)
    if args.command == "finish":
        from fed.core import config as cfgmod
        from fed.core.case import Case
        from fed.core.finalize import finish_case

        cfg = cfgmod.load(args.config) if args.config else cfgmod.Config()
        from fed.core.keepawake import KeepAwake

        case = Case.open(args.case_dir)
        try:
            delete = True if args.delete_work else False if args.keep_work else None
            if delete is None and cfg.programm.arbeitsordner_loeschen == "fragen":
                delete = sys.stdin.isatty() and input(
                    t("Arbeitsordner nach erfolgreicher ZIP-Verifikation löschen? [j/N] ")).strip().lower() in ("j", "ja", "y", "yes")
            if args.zip:
                case.set_zip_mode(args.zip.replace("-", "_"), source="cli")
            password = None
            if args.encrypt_generate:
                from fed.core.encrypt import generate_password

                password = generate_password()
            elif args.encrypt:
                import getpass
                import os

                password = os.environ.get("FED_ZIP_PASSWORD")
                if not password:
                    password = getpass.getpass(t("Passwort für die verschlüsselte Kopie: "))
                    if getpass.getpass(t("Wiederholen: ")) != password:
                        print(t("Passwörter stimmen nicht überein"), file=sys.stderr)
                        return 2
            with KeepAwake() if cfg.programm.wach_halten else _nullctx():
                res = finish_case(case, cfg, delete_work=delete, encrypt_password=password,
                                  closing_note=args.closing_note)
        finally:
            case.close()
        for z, (ts, err) in zip(res.zips, res.timestamps, strict=True):
            print(t("ZIP: {path}  ({files} Mails, verifiziert: {ok})", path=z.path, files=z.files, ok=z.verified))
            for k, v in z.hashes.items():
                print(f"  {k.upper():<7} {v}")
            if ts is not None:
                print(t("  Zeitstempel (RFC 3161): {time}  Seriennr. {serial}", time=ts.gen_time, serial=ts.serial))
            elif err:
                print(t("  WARNUNG Zeitstempel: {error}", error=err))
            for p in z.problems:
                print(t("  HINWEIS: {text}", text=describe(p)))
        if res.encrypted:
            print(t("Verschlüsselte Kopie: {file}  SHA-256 {sha}  (geprüft)", file=res.encrypted["file"], sha=res.encrypted["sha256"]))
            if args.encrypt_generate:
                print(t("  Passwort (wird nicht gespeichert, getrennt übermitteln): {pw}", pw=password))
        print(t("Berichte: {txt}, {pdf}", txt=res.report_txt.name, pdf=res.report_pdf.name)
              + (t(" + {n} Einzelberichte", n=len(res.account_reports)) if res.account_reports else ""))
        return 0
    if args.command == "verify":
        from fed.core.case import Case

        case = Case.open(args.case_dir)
        ok, bad = case.verify_log()
        case.close()
        print(t("Log-Hash-Kette: OK") if ok else t("Log-Hash-Kette GEBROCHEN ab Eintrag {n}", n=bad))
        return 0 if ok else 2
    print(t("Befehl '{cmd}' ist noch nicht umgesetzt.", cmd=args.command), file=sys.stderr)
    return 1


def _nullctx():
    import contextlib

    return contextlib.nullcontext()


def _acquire(args) -> int:
    import getpass
    import os
    import time
    from datetime import UTC, datetime

    from fed.core import acquisition as acqmod
    from fed.core import autoconfig, oauth
    from fed.core import config as cfgmod
    from fed.core.auth import (APP_PASSWORD_URL, OAuthSession, is_gmail, is_microsoft, log_oauth_login,
                               explain_login_error, make_connector, microsoft_password_hint,
                               normalize_app_password)
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
                server, port, security, source = args.server, args.port, args.security, "manual"
            else:
                found, log = autoconfig.discover(args.account, proxy=cfg.proxy_url)
                for line in log:
                    print("  " + line)
                if not found:
                    print(t("Keine IMAP-Einstellungen gefunden – bitte --server angeben."), file=sys.stderr)
                    return 1
                server, port, security, source = found.host, found.port, found.security, found.source
            user = args.user or args.account
        provider_hint = found.provider if found else None
        method = args.auth
        if method == "auto":
            if row:
                stored = case.conn.execute("SELECT auth_method FROM account WHERE id = ?", (acc_id,)).fetchone()[0]
                method = {"oauth_google": "google", "oauth_microsoft": "microsoft",
                          "app_password_google": "google-app"}.get(stored, "password")
            elif is_gmail(server) or str(provider_hint) == "google":
                method = "google" if cfg.oauth_google.client_id else "google-app"
            elif is_microsoft(server) or str(provider_hint) == "microsoft":
                method = "microsoft"
            else:
                method = "password"
        auth_method = {"google": "oauth_google", "microsoft": "oauth_microsoft",
                       "google-app": "app_password_google"}.get(method, "password")
        timeout = cfg.programm.socket_timeout_s
        if method in ("password", "google-app") and is_microsoft(server):
            print(t("  WARNUNG: {text}", text=microsoft_password_hint()), file=sys.stderr)
        if method in ("password", "google-app"):
            prompt = (t("Google-App-Passwort für {user} ({url}): ", user=user, url=APP_PASSWORD_URL)
                      if method == "google-app" else t("Passwort für {user}: ", user=user))
            password = os.environ.get("FED_PASSWORD") or getpass.getpass(prompt)
            if method == "google-app":
                password = normalize_app_password(password)
            connect = make_connector(server, port, security, user, password=password, timeout=timeout,
                                     cafile=args.cafile)
        else:
            if method == "google":
                prov, cid, secret = oauth.GOOGLE, cfg.oauth_google.client_id, cfg.oauth_google.client_secret
            else:
                tenant = oauth.microsoft_tenant(cfg.oauth_microsoft.tenant, args.login_as or args.account)
                prov, cid, secret = oauth.microsoft(tenant), cfg.oauth_microsoft.client_id, ""
            sess = OAuthSession(prov, cid, secret, cfg.proxy_url)
            print(t("Anmeldung bei {provider} – ", provider=method.title())
                  + (t("Gerätecode folgt …") if args.device_code else t("Browser wird geöffnet …")))
            try:
                token = sess.login_interactive(
                    login_hint=args.login_as or args.account, device_code=args.device_code,
                    show_device_code=lambda dc: print(dc.message or t("Öffne {url} und gib {code} ein", url=dc.verification_uri, code=dc.user_code)))
            except oauth.OAuthError as e:
                case.log("ERROR", "oauth_failed", str(e))
                print(t("Anmeldung fehlgeschlagen: {error}", error=e), file=sys.stderr)
                hint = oauth.explain_error(prov.name, str(e))
                if hint:
                    print("  " + hint, file=sys.stderr)
                return 1
            warning = log_oauth_login(case, token, args.account)
            print(t("Angemeldet als {login}", login=token.account or "?"))
            if warning:
                print(t("  WARNUNG: {text}", text=warning))
            connect = make_connector(server, port, security, user, oauth_session=sess, timeout=timeout,
                                     cafile=args.cafile)

        if not row:
            spec = acqmod.AccountSpec(
                args.account, args.evidence, server, port, security, user, auth_method=auth_method,
                autoconfig_source=source,
                date_from=datetime.fromisoformat(args.date_from).replace(tzinfo=UTC) if args.date_from else None,
                date_to=datetime.fromisoformat(args.date_to).replace(tzinfo=UTC) if args.date_to else None,
                notes=args.account_notes or "",
            )
            try:
                imap = connect()
            except Exception as e:
                if method in ("password", "google-app"):
                    explained = explain_login_error(server, str(e))
                    if explained != str(e):
                        print(t("Anmeldung fehlgeschlagen: {error}", error=explained), file=sys.stderr)
                raise
            acc_id = acqmod.register_account(case, spec, imap)
            folders = acqmod.scan(case, acc_id, imap)
            imap.logout()
            for f in folders:
                print(t("  {name:<40} {n:>7} Mails {mb:>9.1f} MB", name=f["name"], n=f["messages"], mb=f["size"] / 1e6))
        elif args.account_notes is not None:
            acqmod.set_account_notes(case, acc_id, args.account_notes)
        flt = {k: getattr(args, f"flt_{k}") for k in ("from", "to", "subject", "text")}
        if any(flt.values()):
            acqmod.set_content_filter(case, acc_id, flt)
        if args.gmail_all_folders and acqmod.is_gmail_account(case, acc_id):
            acqmod.set_selection(case, acc_id, {r[0] for r in case.conn.execute(
                "SELECT display_name FROM folder WHERE account_id = ? AND (flags IS NULL OR flags NOT LIKE '%Noselect%')",
                (acc_id,))})
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
                print("\r" + t("  {done}/{total} Mails · {mb_done:.1f}/{mb_total:.1f} MB · {mbs:.1f} MB/s · "
                                "{mpm:.0f} Mails/min · Rest {eta} s · {folder:<30}", done=p.mails_done,
                                total=p.mails_total, mb_done=p.bytes_done / 1e6, mb_total=p.bytes_total / 1e6,
                                mbs=bps / 1e6, mpm=mpm, eta=int(eta) if eta else "?", folder=p.folder[:30]),
                      end="", flush=True)

        mode = acqmod.Mode.RESTART if args.force else acqmod.Mode.RESUME
        from fed.core.keepawake import KeepAwake

        awake = KeepAwake()
        if cfg.programm.wach_halten:
            method = awake.acquire()
            case.log("INFO" if method else "WARNING", "keep_awake_on",
                     {"for": "acquisition", "method": method or "unavailable"})
        try:
            res = acqmod.run_attempt(case, acc_id, connect, mode, token, show, args.reason, args.delete_old, cfg)
        except KeyboardInterrupt:
            token.cancel()
            raise
        finally:
            awake.release()
        print()
        print(t("Versuch {nr}: {status} · gesichert {saved} · bereits vorhanden {skipped} · erwartet {expected}",
                nr=res.nr, status=res.status, saved=res.saved, skipped=res.skipped_existing, expected=res.expected))
        for w in res.warnings:
            print(t("  WARNUNG: {text}", text=w))
        return 0 if res.status == "done" else 3
    finally:
        case.close()


if __name__ == "__main__":
    raise SystemExit(main())
