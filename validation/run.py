"""Validierung von FED gegen das feste Testpostfach – erzeugt ein Validierungsprotokoll.

    python -m validation.run                    # Bericht nach ./validation-report/
    python -m validation.run --out DIR --lang de,en

Voraussetzungen: ``dovecot`` und ``openssl`` im PATH (Linux/macOS; unter Windows
z. B. in WSL). Der Zeitstempel-Test (V11) braucht ``openssl ts``.

Ablauf: ein lokaler Dovecot stellt das Testpostfach aus ``testmailbox.py`` bereit
(12 Mails in 10 Ordnern, erwartete Hashes in ``expected_hashes.csv``). FED sichert es
wie im echten Einsatz; jede Prüfung vergleicht das Ergebnis mit dem erwarteten Wert.
Ergebnis je Prüfung: BESTANDEN / NICHT BESTANDEN / NICHT GEPRÜFT. Rückgabewert 1, wenn
eine Prüfung nicht bestanden ist.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dovecot_server  # noqa: E402
import fake_tsa  # noqa: E402
import testmailbox  # noqa: E402

from fed import APP_NAME, __version__  # noqa: E402
from fed.core.acquisition import AccountSpec, register_account, run_attempt, scan  # noqa: E402
from fed.core.cancel import CancelToken  # noqa: E402
from fed.core.case import CASE_DB, Case, CaseInfo  # noqa: E402
from fed.core.config import Config  # noqa: E402
from fed.core.finalize import finish_case, verify_zip  # noqa: E402
from fed.core.hashing import hash_file, tool_fingerprint  # noqa: E402
from fed.core.imap_reader import ForbiddenCommand, ReadOnlyImap  # noqa: E402

PASS, FAIL, SKIP = "pass", "fail", "skip"

# id: {sprache: (Titel, erwartetes Ergebnis)}
CHECKS: dict[str, dict[str, tuple[str, str]]] = {
    "V01": {"de": ("Nur lesender Zugriff",
                   "Postfach auf dem Server nach der Sicherung unverändert: alle Maildir-Dateien (Inhalt, "
                   "Name inkl. Flags, Zeit) identisch, STATUS vorher = nachher in jedem Ordner."),
            "en": ("Read-only access",
                   "Mailbox on the server unchanged after acquisition: all Maildir files (content, name incl. "
                   "flags, time) identical, STATUS before = after in every folder.")},
    "V02": {"de": ("Sperre schreibender IMAP-Befehle",
                   "STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, RENAME, CREATE, SELECT werden vor dem Senden "
                   "abgewiesen."),
            "en": ("Blocking of writing IMAP commands",
                   "STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, RENAME, CREATE, SELECT are refused before "
                   "being sent.")},
    "V03": {"de": ("Vollständigkeit", "Alle Ordner (auch leere) erkannt; Anzahl Mails je Ordner wie erwartet."),
            "en": ("Completeness", "All folders (including empty ones) found; number of messages per folder "
                                   "as expected.")},
    "V04": {"de": ("Bitgenaue Sicherung",
                   "SHA-256, MD5 und Größe jeder .eml stimmen mit expected_hashes.csv überein (u. a. 8-bit-"
                   "Latin-1, ohne Date-Header, 4-MB-Mail in Teilstücken)."),
            "en": ("Bit-exact acquisition",
                   "SHA-256, MD5 and size of every .eml match expected_hashes.csv (incl. 8-bit Latin-1, no "
                   "Date header, 4 MB message fetched in chunks).")},
    "V05": {"de": ("Eingangszeit als Dateizeit",
                   "Dateizeit und Dateiname jeder .eml entsprechen der Eingangszeit (INTERNALDATE)."),
            "en": ("Receipt time as file time",
                   "File time and file name of every .eml correspond to the receipt time (INTERNALDATE).")},
    "V06": {"de": ("Abbrechen und Fortsetzen",
                   "Abgebrochener Versuch hinterlässt keine Teildateien; Fortsetzen lädt nur Fehlendes, "
                   "Endergebnis bitgenau."),
            "en": ("Cancel and resume",
                   "A cancelled attempt leaves no partial files; resuming loads only what is missing, final "
                   "result bit-exact.")},
    "V07": {"de": ("Erkennung veränderter Arbeitsdateien",
                   "Eine nachträglich veränderte .eml wird beim Fortsetzen erkannt, protokolliert und neu geladen."),
            "en": ("Detection of modified working files",
                   "A .eml modified afterwards is detected on resume, logged and downloaded again.")},
    "V08": {"de": ("ZIP-Archiv",
                   "ZIP verifiziert; Inhalt bitgenau; Ordnerstruktur inkl. Umlaut- und leerer Ordner; "
                   ".sha256-Datei korrekt."),
            "en": ("ZIP archive",
                   "ZIP verified; content bit-exact; folder structure incl. umlaut and empty folders; .sha256 "
                   "file correct.")},
    "V09": {"de": ("Erkennung veränderter ZIP-Inhalte",
                   "Eine im ZIP veränderte Mail wird bei der Verifikation erkannt."),
            "en": ("Detection of modified ZIP content", "A message modified inside the ZIP is detected by "
                                                        "verification.")},
    "V10": {"de": ("Manipulationsschutz des Logs",
                   "Log nur anhängend (UPDATE/DELETE abgewiesen); Hash-Kette intakt; Änderung eines Eintrags "
                   "wird erkannt."),
            "en": ("Tamper protection of the log",
                   "Log append-only (UPDATE/DELETE refused); hash chain intact; modifying an entry is "
                   "detected.")},
    "V11": {"de": ("Zeitstempel nach RFC 3161",
                   "Zeitstempel für den ZIP-Hash erhalten; Hash und Nonce passen; Signatur mit openssl ts "
                   "-verify gültig."),
            "en": ("RFC 3161 timestamp",
                   "Timestamp for the ZIP hash received; hash and nonce match; signature valid with openssl "
                   "ts -verify.")},
    "V12": {"de": ("Berichte und Ausgabe-Hashes",
                   "report.txt/report.pdf und log.csv erzeugt; alle in der Case-Datei vermerkten Ausgabe-"
                   "Hashes stimmen mit den Dateien überein."),
            "en": ("Reports and output hashes",
                   "report.txt/report.pdf and log.csv created; all output hashes recorded in the case file "
                   "match the files.")},
    "V13": {"de": ("Umlaute in Adresse, Domain und Passwort",
                   "Konto bärbel.böhmer@ärzte.de (IDN-Domain, Umlaute im Namen und im Passwort) wird angemeldet "
                   "und bitgenau gesichert; Ordner im ZIP trägt die Adresse in Unicode (UTF-8-Kennzeichen)."),
            "en": ("Umlauts in address, domain and password",
                   "Account bärbel.böhmer@ärzte.de (IDN domain, umlauts in name and password) signs in and is "
                   "acquired bit-exactly; the ZIP folder carries the address in Unicode (UTF-8 flag).")},
}

T = {
    "de": {"title": "Validierungsprotokoll", "subtitle": "Prüfung gegen ein Testpostfach mit bekannten Hashes",
           "env": "Umgebung", "date": "Datum (UTC / lokal)", "tool": "Werkzeug", "fp": "SHA-256 Programm",
           "commit": "Quelltext-Stand (Git)", "python": "Python", "os": "Betriebssystem", "server": "IMAP-Server",
           "openssl": "OpenSSL", "summary": "Ergebnis", "checks": "Prüfungen", "expected": "Erwartet",
           "result": "Ergebnis", PASS: "BESTANDEN", FAIL: "NICHT BESTANDEN", SKIP: "NICHT GEPRÜFT",
           "mailbox": "Testpostfach", "folder": "Ordner", "received": "Eingang (UTC)", "size": "Größe",
           "note": "Merkmal", "overall_ok": "Alle durchgeführten Prüfungen bestanden.",
           "overall_fail": "Mindestens eine Prüfung NICHT bestanden.", "page": "Seite", "duration": "Dauer"},
    "en": {"title": "Validation Report", "subtitle": "Test against a test mailbox with known hashes",
           "env": "Environment", "date": "Date (UTC / local)", "tool": "Tool", "fp": "SHA-256 of program",
           "commit": "Source revision (Git)", "python": "Python", "os": "Operating system", "server": "IMAP server",
           "openssl": "OpenSSL", "summary": "Result", "checks": "Checks", "expected": "Expected",
           "result": "Result", PASS: "PASSED", FAIL: "FAILED", SKIP: "NOT TESTED",
           "mailbox": "Test mailbox", "folder": "Folder", "received": "Received (UTC)", "size": "Size",
           "note": "Feature", "overall_ok": "All checks performed passed.",
           "overall_fail": "At least one check FAILED.", "page": "Page", "duration": "Duration"},
}


@dataclass
class CheckResult:
    id: str
    status: str
    details: list[str | dict] = field(default_factory=list)  # Text oder {"de": …, "en": …}


def _d(de: str, en: str) -> dict:
    return {"de": de, "en": en}


def _yn(v: bool, lang: str) -> str:
    return {"de": ("ja", "nein"), "en": ("yes", "no")}[lang][0 if v else 1]


def _txt(detail: str | dict, lang: str) -> str:
    return detail.get(lang, detail.get("de", "")) if isinstance(detail, dict) else detail


@dataclass
class Report:
    started_utc: str
    finished_utc: str = ""
    environment: dict = field(default_factory=dict)
    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status != FAIL for r in self.results)


# ---------------------------------------------------------------- Hilfen
def _expected() -> list[dict]:
    with (HERE / "expected_hashes.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _maildir_snapshot(maildir: Path) -> dict[str, tuple[str, int]]:
    """Mail-Dateien (cur/new) mit Inhalt-Hash und Zeit; Dovecot-Indexdateien zählen nicht zum Postfach."""
    snap = {}
    for p in maildir.rglob("*"):
        if p.is_file() and p.parent.name in {"cur", "new"}:
            snap[p.relative_to(maildir).as_posix()] = (hashlib.sha256(p.read_bytes()).hexdigest(),
                                                       int(p.stat().st_mtime))
    return snap


def _version(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        return "–"


def _git_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    return _version(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"])


class Ctx:
    """Gemeinsamer Zustand der Prüfungen."""

    def __init__(self, srv, work: Path) -> None:
        self.srv = srv
        self.work = work
        self.maildir = srv.root / "home" / testmailbox.USER / "Maildir"
        self.expected = _expected()
        self.cfg = Config()
        self.cfg.programm.chunk_mb = 1  # 4-MB-Mail wird stückweise geladen
        self.cfg.programm.verbindungen = 3
        self.case: Case | None = None
        self.finish = None
        self.snapshot_before: dict = {}

    def connect(self) -> ReadOnlyImap:
        c = ReadOnlyImap(self.srv.host, self.srv.port, cafile=str(self.srv.cafile))
        c.connect()
        c.login_password(self.srv.user, self.srv.password)
        return c

    def new_case(self, number: str) -> tuple[Case, int]:
        case = Case.create(self.work, CaseInfo(number, "Validierung", "automatische Validierung"))
        imap = self.connect()
        acc = register_account(case, AccountSpec(self.srv.user, "VAL-01", self.srv.host, self.srv.port), imap)
        scan(case, acc, imap)
        imap.logout()
        return case, acc


# ---------------------------------------------------------------- Prüfungen
def v01_read_only(ctx: Ctx) -> CheckResult:
    r = CheckResult("V01", PASS)
    after = _maildir_snapshot(ctx.maildir)
    if after != ctx.snapshot_before:
        r.status = FAIL
        for k in sorted(set(after) ^ set(ctx.snapshot_before)):
            r.details.append(_d(f"Datei geändert/verschoben: {k}", f"file changed/moved: {k}"))
        for k in sorted(set(after) & set(ctx.snapshot_before)):
            if after[k] != ctx.snapshot_before[k]:
                r.details.append(_d(f"Inhalt/Zeit geändert: {k}", f"content/time changed: {k}"))
    changed = 0
    rows = ctx.case.conn.execute("SELECT display_name, status_before, status_after FROM folder WHERE selected = 1")
    for name, b, a in rows:
        if json.loads(b) != json.loads(a):
            changed += 1
            r.status = FAIL
            r.details.append(_d(f"STATUS geändert in {name}: {b} -> {a}", f"STATUS changed in {name}: {b} -> {a}"))
    r.details.insert(0, _d(f"{len(after)} Maildir-Dateien verglichen; {changed} Ordner mit STATUS-Abweichung",
                           f"{len(after)} Maildir files compared; {changed} folders with STATUS difference"))
    return r


def v02_guard(ctx: Ctx) -> CheckResult:
    r = CheckResult("V02", PASS)
    imap = ctx.connect()
    inbox = next(f for f in imap.list_folders() if f.name == "INBOX")
    imap.examine(inbox)
    attempts: dict[str, Callable] = {
        "STORE": lambda: imap.imap.uid("STORE", "1", "+FLAGS", "(\\Seen)"),
        "COPY": lambda: imap.imap.uid("COPY", "1", "Archiv"),
        "MOVE": lambda: imap.imap.uid("MOVE", "1", "Archiv"),
        "EXPUNGE": lambda: imap.imap.expunge(),
        "APPEND": lambda: imap.imap.append("INBOX", None, None, b"Subject: x\r\n\r\nx"),
        "DELETE": lambda: imap.imap.delete("Leer"),
        "RENAME": lambda: imap.imap.rename("Leer", "Leer2"),
        "CREATE": lambda: imap.imap.create("Neu"),
        "SELECT": lambda: imap.imap.select("INBOX"),
    }
    for verb, fn in attempts.items():
        try:
            fn()
        except ForbiddenCommand:
            r.details.append(_d(f"{verb}: abgewiesen", f"{verb}: refused"))
            continue
        except Exception as e:
            r.status = FAIL
            r.details.append(_d(f"{verb}: nicht durch FED abgewiesen ({type(e).__name__}: {e})",
                                f"{verb}: not refused by FED ({type(e).__name__}: {e})"))
            continue
        r.status = FAIL
        r.details.append(_d(f"{verb}: AUSGEFÜHRT", f"{verb}: EXECUTED"))
    imap.logout()
    return r


def v03_completeness(ctx: Ctx) -> CheckResult:
    r = CheckResult("V03", PASS)
    want = Counter(e["folder"] for e in ctx.expected)
    folders = {name: (sel, cnt) for name, sel, cnt in ctx.case.conn.execute(
        "SELECT display_name, selected, count_saved FROM folder")}
    expected_folders = testmailbox.folders()
    for f in sorted(expected_folders):
        if f not in folders:
            r.status = FAIL
            r.details.append(_d(f"Ordner fehlt: {f}", f"folder missing: {f}"))
            continue
        got = folders[f][1] or 0
        ok = got == want.get(f, 0)
        r.details.append(_d(f"{f}: {got} / {want.get(f, 0)}" + ("" if ok else "  ← ABWEICHUNG"),
                            f"{f}: {got} / {want.get(f, 0)}" + ("" if ok else "  ← MISMATCH")))
        if not ok:
            r.status = FAIL
    return r


def v04_bit_exact(ctx: Ctx) -> CheckResult:
    r = CheckResult("V04", PASS)
    rows = ctx.case.conn.execute("SELECT f.display_name, m.zip_path, m.size FROM message m "
                                 "JOIN folder f ON f.id = m.folder_id WHERE m.status = 'valid'").fetchall()
    got = Counter()
    for folder, zip_path, _size in rows:
        p = ctx.case.work_dir / zip_path
        if not p.exists():  # Arbeitsordner gelöscht -> aus dem ZIP lesen
            with zipfile.ZipFile(ctx.finish.zip.path) as zf:
                data = zf.read(zip_path)
        else:
            data = p.read_bytes()
        got[(folder, hashlib.sha256(data).hexdigest(), hashlib.md5(data).hexdigest(), len(data))] += 1
    want = Counter((e["folder"], e["sha256"], e["md5"], int(e["size"])) for e in ctx.expected)
    for k in sorted(want - got):
        r.status = FAIL
        r.details.append(_d(f"erwartet, nicht gesichert: {k[0]} SHA-256 {k[1]}",
                            f"expected, not acquired: {k[0]} SHA-256 {k[1]}"))
    for k in sorted(got - want):
        r.status = FAIL
        r.details.append(_d(f"gesichert, nicht erwartet: {k[0]} SHA-256 {k[1]}",
                            f"acquired, not expected: {k[0]} SHA-256 {k[1]}"))
    n_ok = sum((want & got).values())
    r.details.insert(0, _d(f"{n_ok} von {len(ctx.expected)} Mails bitgenau",
                           f"{n_ok} of {len(ctx.expected)} messages bit-exact"))
    return r


def v05_times(ctx: Ctx) -> CheckResult:
    r = CheckResult("V05", PASS)
    by_hash = {e["sha256"]: e for e in ctx.expected}
    n = 0
    for zip_path, sha in ctx.case.conn.execute("SELECT zip_path, sha256 FROM message WHERE status = 'valid'"):
        exp = datetime.fromisoformat(by_hash[sha]["received_utc"].replace("Z", "+00:00"))
        p = ctx.case.work_dir / zip_path
        mtime = int(p.stat().st_mtime) if p.exists() else None
        name_ok = Path(zip_path).name.startswith(exp.strftime("%Y%m%dT%H%M%SZ_UID"))
        time_ok = mtime is None or mtime == int(exp.timestamp())
        if not (name_ok and time_ok):
            r.status = FAIL
            ft = datetime.fromtimestamp(mtime, UTC).isoformat() if mtime else "–"
            r.details.append(_d(f"{zip_path}: erwartet {exp.isoformat()}, Dateizeit {ft}",
                                f"{zip_path}: expected {exp.isoformat()}, file time {ft}"))
        n += 1
    r.details.insert(0, _d(f"{n} Dateien geprüft", f"{n} files checked"))
    return r


def v06_cancel_resume(ctx: Ctx) -> CheckResult:
    r = CheckResult("V06", PASS)
    case, acc = ctx.new_case("VAL-CANCEL")
    token = CancelToken()

    def stop(p):
        if p.mails_done >= 3:
            token.cancel()

    r1 = run_attempt(case, acc, ctx.connect, cancel=token, progress=stop, cfg=ctx.cfg)
    parts = list(case.work_dir.rglob("*.part"))
    r2 = run_attempt(case, acc, ctx.connect, cfg=ctx.cfg)
    hashes = Counter(s for (s,) in case.conn.execute("SELECT sha256 FROM message WHERE status = 'valid'"))
    want = Counter(e["sha256"] for e in ctx.expected)
    r.details += [_d(f"Versuch 1: {r1.status}, {r1.saved} gesichert; Teildateien danach: {len(parts)}",
                     f"Attempt 1: {r1.status}, {r1.saved} saved; partial files afterwards: {len(parts)}"),
                  _d(f"Versuch 2: {r2.status}, {r2.saved} neu, {r2.skipped_existing} vorhanden und nachgehasht",
                     f"Attempt 2: {r2.status}, {r2.saved} new, {r2.skipped_existing} existing and re-hashed")]
    if r1.status != "cancelled" or parts or r2.status != "done" or hashes != want \
            or r2.saved + r2.skipped_existing != len(ctx.expected):
        r.status = FAIL
    ctx.cancel_case = (case, acc)
    return r


def v07_tampered_file(ctx: Ctx) -> CheckResult:
    r = CheckResult("V07", PASS)
    case, acc = ctx.cancel_case
    path = case.conn.execute("SELECT zip_path FROM message WHERE status = 'valid' LIMIT 1").fetchone()[0]
    (case.work_dir / path).write_bytes(b"manipuliert")
    res = run_attempt(case, acc, ctx.connect, cfg=ctx.cfg)
    logged = case.conn.execute("SELECT COUNT(*) FROM log WHERE event = 'existing_file_invalid'").fetchone()[0]
    now_ok = hash_file(case.work_dir / path)["sha256"] in {e["sha256"] for e in ctx.expected}
    r.details += [_d(f"veränderte Datei: {path}", f"modified file: {path}"),
                  _d(f"protokolliert: {logged}× existing_file_invalid", f"logged: {logged}× existing_file_invalid"),
                  _d(f"neu geladen: {res.saved}, danach bitgenau: {_yn(now_ok, 'de')}",
                     f"downloaded again: {res.saved}, bit-exact afterwards: {_yn(now_ok, 'en')}")]
    if not (logged == 1 and res.saved == 1 and now_ok):
        r.status = FAIL
    case.close()
    return r


def v08_zip(ctx: Ctx) -> CheckResult:
    r = CheckResult("V08", PASS)
    z = ctx.finish.zip
    side = z.path.with_name(z.path.name + ".sha256").read_text(encoding="utf-8").split()[0]
    real = hash_file(z.path)["sha256"]
    with zipfile.ZipFile(z.path) as zf:
        names = zf.namelist()
        content = Counter(hashlib.sha256(zf.read(n)).hexdigest() for n in names if n.endswith(".eml"))
    want = Counter(e["sha256"] for e in ctx.expected)
    prefix = f"{testmailbox.USER}/"
    dirs = {n for n in names if n.endswith("/")}
    need = {f"{prefix}{f}/" for f in testmailbox.EMPTY_FOLDERS} | {f"{prefix}Entwürfe/"}
    side_ok = side == real == z.hashes["sha256"]
    r.details += [_d(f"{z.path.name}: {z.files} .eml, verifiziert: {_yn(z.verified, 'de')}",
                     f"{z.path.name}: {z.files} .eml, verified: {_yn(z.verified, 'en')}"),
                  _d(f".sha256-Datei = Datei-Hash: {_yn(side_ok, 'de')}", f".sha256 file = file hash: {_yn(side_ok, 'en')}"),
                  _d(f"Inhalt bitgenau: {_yn(content == want, 'de')}", f"content bit-exact: {_yn(content == want, 'en')}"),
                  _d(f"Ordner im ZIP: {len(dirs)}; geprüft: {', '.join(sorted(need))}",
                     f"folders in ZIP: {len(dirs)}; checked: {', '.join(sorted(need))}")]
    if not z.verified or side != real or content != want or not need <= dirs or names[0] != "log.csv":
        r.status = FAIL
    return r


def v09_zip_tamper(ctx: Ctx) -> CheckResult:
    r = CheckResult("V09", PASS)
    bad = ctx.work / "manipuliert.zip"
    with zipfile.ZipFile(ctx.finish.zip.path) as src, zipfile.ZipFile(bad, "w") as dst:
        done = False
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(".eml") and not done:
                data, done = data.replace(b"Subject:", b"Subjekt:"), True
            dst.writestr(info, data)
    v = verify_zip(ctx.case, bad)
    r.details += v.problems or [_d("keine Abweichung gefunden", "no difference found")]
    if v.verified or not any("Hash abweichend" in p for p in v.problems):
        r.status = FAIL
    return r


def v10_log(ctx: Ctx) -> CheckResult:
    r = CheckResult("V10", PASS)
    ok, bad = ctx.case.verify_log()
    count = ctx.case.conn.execute("SELECT COUNT(*) FROM log").fetchone()[0]
    r.details.append(_d(f"Hash-Kette der Sicherung: {'intakt' if ok else f'GEBROCHEN ab #{bad}'} ({count} Einträge)",
                        f"hash chain of the acquisition: {'intact' if ok else f'BROKEN at #{bad}'} ({count} entries)"))
    if not ok:
        r.status = FAIL
    for sql in ("UPDATE log SET details = 'x' WHERE id = 1", "DELETE FROM log WHERE id = 1"):
        try:
            ctx.case.conn.execute(sql)
            r.status = FAIL
            r.details.append(_d(f"{sql.split()[0]}: AUSGEFÜHRT", f"{sql.split()[0]}: EXECUTED"))
        except sqlite3.DatabaseError:
            r.details.append(_d(f"{sql.split()[0]}: abgewiesen", f"{sql.split()[0]}: refused"))
    copy_dir = ctx.work / "log-kopie"
    copy_dir.mkdir()
    src = sqlite3.connect(ctx.case.root / CASE_DB)
    dst = sqlite3.connect(copy_dir / CASE_DB)
    src.backup(dst)
    src.close()
    dst.execute("DROP TRIGGER log_no_update")
    dst.execute("UPDATE log SET details = 'manipuliert' WHERE id = 2")
    dst.commit()
    dst.close()
    copy = Case.open(copy_dir)
    ok2, bad2 = copy.verify_log()
    copy.close()
    r.details.append(_d(f"Kopie mit geändertem Eintrag #2: {'NICHT erkannt' if ok2 else f'erkannt (ab #{bad2})'}",
                        f"copy with modified entry #2: {'NOT detected' if ok2 else f'detected (at #{bad2})'}"))
    if ok2:
        r.status = FAIL
    return r


def v11_timestamp(ctx: Ctx) -> CheckResult:
    r = CheckResult("V11", PASS)
    if ctx.tsa is None:
        return CheckResult("V11", SKIP, [_d("openssl ts nicht verfügbar", "openssl ts not available")])
    ts = ctx.finish.timestamp
    if ts is None:
        return CheckResult("V11", FAIL, [ctx.finish.timestamp_error or _d("kein Zeitstempel", "no timestamp")])
    z = ctx.finish.zip.path
    tsr = z.with_name(z.name + ".tsr")
    v = subprocess.run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(z), "-CAfile",
                        str(ctx.tsa.ca_file)], capture_output=True, text=True)
    verified = "Verification: OK" in v.stdout
    same = ts.hashed_message == ctx.finish.zip.hashes["sha256"]
    r.details += [_d(f"Zeit laut Dienst: {ts.gen_time}, Seriennr. {ts.serial}, Genauigkeit {ts.accuracy}",
                     f"time per service: {ts.gen_time}, serial {ts.serial}, accuracy {ts.accuracy}"),
                  _d(f"gestempelter Hash = ZIP-SHA-256: {_yn(same, 'de')}", f"timestamped hash = ZIP SHA-256: {_yn(same, 'en')}"),
                  f"openssl ts -verify: {(v.stdout or v.stderr).strip()}",
                  _d("Hinweis: Test-Zeitstempeldienst mit eigener Test-CA (geprüft wird FED, nicht ein öffentlicher Dienst)",
                     "Note: test timestamp service with its own test CA (this checks FED, not a public service)")]
    if not verified or ts.hashed_message != ctx.finish.zip.hashes["sha256"]:
        r.status = FAIL
    return r


def v12_reports(ctx: Ctx) -> CheckResult:
    r = CheckResult("V12", PASS)
    for p in (ctx.finish.report_txt, ctx.finish.report_pdf, ctx.case.root / "log.csv"):
        if not p.exists() or p.stat().st_size == 0:
            r.status = FAIL
            r.details.append(_d(f"fehlt: {p.name}", f"missing: {p.name}"))
    if ctx.finish.report_pdf.exists() and not ctx.finish.report_pdf.read_bytes().startswith(b"%PDF"):
        r.status = FAIL
        r.details.append(_d("report.pdf ist keine PDF-Datei", "report.pdf is not a PDF file"))
    n = 0
    for name, sha in ctx.case.conn.execute("SELECT file_name, sha256 FROM output"):
        p = ctx.case.root / name
        if name == "log.csv":
            continue  # wird beim Packen neu geschrieben und im ZIP mitgeführt
        n += 1
        if not p.exists() or hash_file(p)["sha256"] != sha:
            r.status = FAIL
            r.details.append(_d(f"Hash passt nicht: {name}", f"hash mismatch: {name}"))
    r.details.insert(0, _d(f"{n} Ausgabedateien gegen die Case-Datei geprüft",
                           f"{n} output files checked against the case file"))
    return r


def v13_umlauts(ctx: Ctx) -> CheckResult:
    from fed.core.naming import email_domain

    r = CheckResult("V13", PASS)
    addr, pw = testmailbox.UMLAUT_USER, testmailbox.UMLAUT_PASSWORD

    def connect() -> ReadOnlyImap:
        c = ReadOnlyImap(ctx.srv.host, ctx.srv.port, cafile=str(ctx.srv.cafile))
        c.connect()
        c.login_password(addr, pw)
        return c

    case = Case.create(ctx.work, CaseInfo("VAL-UMLAUT", "Validierung"))
    imap = connect()
    acc = register_account(case, AccountSpec(addr, "VAL-Ä1", ctx.srv.host, ctx.srv.port), imap)
    scan(case, acc, imap)
    imap.logout()
    res = run_attempt(case, acc, connect, cfg=ctx.cfg)
    fin = finish_case(case, Config(), language="de", delete_work=False)
    with zipfile.ZipFile(fin.zip.path) as zf:
        emls = [i for i in zf.infolist() if i.filename.endswith(".eml")]
        tops = {i.filename.split("/")[0] for i in emls}
        utf8 = all(i.flag_bits & 0x800 for i in emls)
        got = Counter(hashlib.sha256(zf.read(i)).hexdigest() for i in emls)
    want = Counter(e["sha256"] for e in ctx.expected)
    r.details += [_d(f"Domain für DNS/TLS: {email_domain(addr)}", f"domain for DNS/TLS: {email_domain(addr)}"),
                  _d(f"Anmeldung und Sicherung: {res.status}, {res.saved} Mails", f"login and acquisition: {res.status}, "
                                                                                  f"{res.saved} messages"),
                  _d(f"Ordner im ZIP: {', '.join(sorted(tops))}; UTF-8-Kennzeichen: {_yn(utf8, 'de')}",
                     f"folder in ZIP: {', '.join(sorted(tops))}; UTF-8 flag: {_yn(utf8, 'en')}"),
                  _d(f"bitgenau: {_yn(got == want, 'de')}", f"bit-exact: {_yn(got == want, 'en')}")]
    if res.status != "done" or tops != {addr} or not utf8 or got != want or not fin.zip.verified:
        r.status = FAIL
    case.close()
    return r


STEPS = [v01_read_only, v02_guard, v03_completeness, v04_bit_exact, v05_times, v06_cancel_resume,
         v07_tampered_file, v08_zip, v09_zip_tamper, v10_log, v11_timestamp, v12_reports, v13_umlauts]


# ---------------------------------------------------------------- Ablauf
def run(work: Path) -> Report:
    rep = Report(started_utc=datetime.now(UTC).isoformat(timespec="seconds"))
    rep.environment = {
        "tool": f"{APP_NAME} {__version__}", "tool_sha256": tool_fingerprint(), "commit": _git_commit(),
        "python": platform.python_version(), "os": platform.platform(),
        "server": _version(["dovecot", "--version"]), "openssl": _version(["openssl", "version"]),
        "mailbox": _expected(),
    }
    srv = dovecot_server.start()
    tsa = fake_tsa.FakeTsa() if fake_tsa.available() else None
    try:
        if tsa:
            tsa.__enter__()
        ctx = Ctx(srv, work)
        ctx.tsa = tsa
        ctx.snapshot_before = _maildir_snapshot(ctx.maildir)
        ctx.case, acc = ctx.new_case("VAL-1")
        res = run_attempt(ctx.case, acc, ctx.connect, cfg=ctx.cfg)
        if res.status != "done":
            raise RuntimeError(f"Sicherung nicht abgeschlossen: {res.status} {res.warnings}")
        if tsa:
            ctx.cfg.tsa_url = tsa.url
        ctx.finish = finish_case(ctx.case, ctx.cfg, language="de", delete_work=False)
        for step in STEPS:
            try:
                rep.results.append(step(ctx))
            except Exception as e:
                cid = step.__name__[:3].upper()
                rep.results.append(CheckResult(cid, FAIL, [f"Exception: {type(e).__name__}: {e}",
                                                           *traceback.format_exc().strip().splitlines()[-3:]]))
        ctx.case.close()
    finally:
        if tsa:
            tsa.__exit__(None, None, None)
        srv.stop()
    rep.finished_utc = datetime.now(UTC).isoformat(timespec="seconds")
    return rep


# ---------------------------------------------------------------- Ausgabe
def _local(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return f"{dt:%Y-%m-%d %H:%M:%S} UTC / {dt.astimezone():%Y-%m-%d %H:%M:%S %Z}"


def _env_rows(rep: Report, L: dict) -> list[tuple[str, str]]:
    e = rep.environment
    secs = int((datetime.fromisoformat(rep.finished_utc) - datetime.fromisoformat(rep.started_utc)).total_seconds())
    return [(L["date"], _local(rep.started_utc)), (L["duration"], f"{secs} s"), (L["tool"], e["tool"]),
            (L["fp"], e["tool_sha256"]), (L["commit"], e["commit"]), (L["python"], e["python"]),
            (L["os"], e["os"]), (L["server"], f"Dovecot {e['server']}"), (L["openssl"], e["openssl"])]


def _counts(rep: Report) -> Counter:
    return Counter(r.status for r in rep.results)


def render_txt(rep: Report, lang: str) -> str:
    L = T[lang]
    out = [f"{APP_NAME} – {L['title']}", "=" * 60, L["subtitle"], "", L["env"], "-" * len(L["env"])]
    rows = _env_rows(rep, L)
    w = max(len(k) for k, _ in rows)
    out += [f"{k:<{w}}  {v}" for k, v in rows]
    c = _counts(rep)
    out += ["", L["summary"], "-" * len(L["summary"]),
            f"{L[PASS]}: {c[PASS]}   {L[FAIL]}: {c[FAIL]}   {L[SKIP]}: {c[SKIP]}",
            L["overall_ok"] if rep.ok else L["overall_fail"], "", L["checks"], "-" * len(L["checks"])]
    for r in rep.results:
        title, exp = CHECKS[r.id][lang]
        out += [f"{r.id}  {title}  [{L[r.status]}]", f"     {L['expected']}: {exp}"]
        out += [f"     · {_txt(d, lang)}" for d in r.details] + [""]
    out += [L["mailbox"], "-" * len(L["mailbox"])]
    for m in rep.environment["mailbox"]:
        out.append(f"{m['folder']:<22} {m['received_utc']}  {int(m['size']):>8}  {m['sha256']}  {m['note']}")
    return "\n".join(out) + "\n"


def write_pdf(rep: Report, lang: str, path: Path) -> None:
    from fpdf import FPDF

    L = T[lang]
    fonts = resources.files("fed.resources") / "fonts"
    logo = resources.files("fed.resources") / "fed-icon-256.png"

    class P(FPDF):
        def header(self):
            self.image(str(logo), x=15, y=10, h=12)
            self.set_font("DejaVu", "B", 11)
            self.set_xy(30, 11)
            self.cell(0, 5, f"{APP_NAME}")
            self.set_font("DejaVu", "", 8)
            self.set_xy(30, 16)
            self.cell(0, 4, f"{L['title']} · {rep.environment['tool']}")
            self.set_draw_color(200, 205, 214)
            self.line(15, 25, 195, 25)
            self.set_y(29)

        def footer(self):
            self.set_y(-12)
            self.set_font("DejaVu", "", 7)
            self.set_text_color(90, 98, 112)
            self.cell(0, 5, f"SHA-256 {rep.environment['tool_sha256']}", align="L")
            self.set_x(15)
            self.cell(0, 5, f"{L['page']} {self.page_no()} / {{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = P(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 18)
    pdf.add_font("DejaVu", "", str(fonts / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(fonts / "DejaVuSans-Bold.ttf"))
    pdf.add_font("Mono", "", str(fonts / "DejaVuSansMono.ttf"))
    pdf.set_title(f"{APP_NAME} – {L['title']}")
    pdf.set_creator(rep.environment["tool"])
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 15)
    pdf.cell(0, 9, L["title"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.cell(0, 5, L["subtitle"], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    def heading(text):
        pdf.ln(2)
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")

    heading(L["env"])
    for k, v in _env_rows(rep, L):
        pdf.set_font("DejaVu", "", 8)
        pdf.cell(48, 5, k)
        pdf.set_font("Mono" if k in (L["fp"], L["commit"]) else "DejaVu", "", 8)
        pdf.multi_cell(0, 5, v, new_x="LMARGIN", new_y="NEXT")

    c = _counts(rep)
    heading(L["summary"])
    pdf.set_font("DejaVu", "B", 10)
    pdf.set_text_color(*((22, 128, 61) if rep.ok else (185, 28, 28)))
    pdf.multi_cell(0, 6, L["overall_ok"] if rep.ok else L["overall_fail"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("DejaVu", "", 9)
    pdf.cell(0, 5, f"{L[PASS]}: {c[PASS]}   {L[FAIL]}: {c[FAIL]}   {L[SKIP]}: {c[SKIP]}",
             new_x="LMARGIN", new_y="NEXT")

    heading(L["checks"])
    colors = {PASS: (22, 128, 61), FAIL: (185, 28, 28), SKIP: (161, 98, 7)}
    for r in rep.results:
        title, exp = CHECKS[r.id][lang]
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_font("DejaVu", "B", 9)
        pdf.cell(12, 6, r.id)
        pdf.cell(120, 6, title)
        pdf.set_text_color(*colors[r.status])
        pdf.cell(0, 6, L[r.status], align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("DejaVu", "", 8)
        pdf.set_x(27)
        pdf.multi_cell(0, 4.2, f"{L['expected']}: {exp}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(70, 76, 88)
        for d in r.details:
            pdf.set_x(27)
            pdf.multi_cell(0, 4.2, f"· {_txt(d, lang)}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1.5)

    pdf.add_page()
    heading(L["mailbox"])
    pdf.set_font("DejaVu", "B", 7)
    widths = (32, 30, 14, 58, 46)
    for w, h in zip(widths, (L["folder"], L["received"], L["size"], "SHA-256", L["note"]), strict=True):
        pdf.cell(w, 5, h)
    pdf.ln()
    for m in rep.environment["mailbox"]:
        pdf.set_font("DejaVu", "", 7)
        pdf.cell(widths[0], 4.5, m["folder"])
        pdf.cell(widths[1], 4.5, m["received_utc"])
        pdf.cell(widths[2], 4.5, m["size"], align="R")
        pdf.set_font("Mono", "", 5.5)
        pdf.cell(widths[3], 4.5, m["sha256"][:40] + "…")
        pdf.set_font("DejaVu", "", 7)
        pdf.multi_cell(widths[4], 4.5, m["note"], new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def render_notes(rep: Report, lang: str = "en", report_url: str = "") -> str:
    """Kurzfassung als Markdown für die Release-Notizen."""
    L = T[lang]
    icon = {PASS: "✅", FAIL: "❌", SKIP: "⚪"}
    c = _counts(rep)
    e = rep.environment
    lines = [f"## {L['title']}", "",
             f"**{L['overall_ok'] if rep.ok else L['overall_fail']}** "
             f"{L[PASS]}: {c[PASS]} · {L[FAIL]}: {c[FAIL]} · {L[SKIP]}: {c[SKIP]}", "",
             f"| | {L['checks']} | {L['result']} |", "| --- | --- | --- |"]
    lines += [f"| {r.id} | {CHECKS[r.id][lang][0]} | {icon[r.status]} {L[r.status]} |" for r in rep.results]
    lines += ["", f"{e['tool']} · {L['commit']} `{e['commit'][:12]}` · Python {e['python']} · "
                  f"Dovecot {e['server'].split()[0]} · {_local(rep.started_utc).split(' / ')[0]}"]
    if report_url:
        lines += ["", f"→ [{L['title']} (PDF)]({report_url})"]
    return "\n".join(lines) + "\n"


def write_all(rep: Report, out: Path, langs: list[str]) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for lang in langs:
        txt, pdf = out / f"validation-report-{lang}.txt", out / f"validation-report-{lang}.pdf"
        txt.write_text(render_txt(rep, lang), encoding="utf-8")
        write_pdf(rep, lang, pdf)
        files += [txt, pdf]
    js = out / "validation-report.json"
    js.write_text(json.dumps({**asdict(rep), "ok": rep.ok}, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(js)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("validation-report"))
    ap.add_argument("--lang", default="de,en", help="de, en oder de,en")
    ap.add_argument("--notes", type=Path, help="zusätzlich Markdown-Kurzfassung (englisch) für Release-Notizen")
    ap.add_argument("--report-url", default="", help="Link zum PDF in der Kurzfassung")
    a = ap.parse_args(argv)
    if not dovecot_server.available():
        print("dovecot und openssl werden benötigt (z. B. apt install dovecot-imapd)", file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="fed-validation-"))
    try:
        rep = run(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    files = write_all(rep, a.out, [x.strip() for x in a.lang.split(",") if x.strip()])
    if a.notes:
        a.notes.write_text(render_notes(rep, "en", a.report_url), encoding="utf-8")
        files.append(a.notes)
    c = _counts(rep)
    for r in rep.results:
        print(f"{r.id}  {T['de'][r.status]:<16} {CHECKS[r.id]['de'][0]}")
    print(f"\n{c[PASS]} bestanden, {c[FAIL]} nicht bestanden, {c[SKIP]} nicht geprüft")
    for f in files:
        print(f"  {f}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
