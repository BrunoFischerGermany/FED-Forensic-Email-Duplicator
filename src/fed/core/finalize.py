"""Case abschließen: log.csv, ZIP mit Zeitstempeln, Verifikation, Hashes.

ZIP-Aufbau::

    log.csv
    <adresse>/<IMAP-Ordner>/<Eingangszeit>_UID<n>.eml
    <adresse>/<leerer Ordner>/

- Nur Mails mit Status ``valid`` aus der Case-Datei kommen ins ZIP; Dateien im
  Arbeitsordner ohne Eintrag werden protokolliert und nicht übernommen.
- Jede .eml bekommt als Zeit die gewählte Quelle (Standard Eingangszeit), als
  klassische ZIP-Zeit in UTC und zusätzlich im Extended-Timestamp-Feld (0x5455).
- Kompression DEFLATE, ZIP64 bei Bedarf.
- Nach dem Schreiben wird das ZIP neu geöffnet und jede .eml erneut gehasht.
"""

from __future__ import annotations

import csv
import json
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fed.core.cancel import CancelToken, Progress, ProgressCallback
from fed.core.case import Case, utc_now
from fed.core.config import Config
from fed.core.hashing import MultiHasher, hash_file
from fed.core.naming import folder_to_zip_path, sanitize_component
from fed.core.timefmt import local_str

CSV_COLUMNS = [
    "Konto", "Evidence", "Ordner", "UID", "Eingangszeit_UTC", "Eingangszeit_lokal", "Date_Header", "Absender", "An", "CC", "BCC",
    "Empfaenger_Hinweis", "Delivered_To", "Anlagen", "Inline", "Anlagen_Namen", "Betreff", "Message_ID",
    "Groesse", "Datei", "SHA256", "MD5", "SHA512", "Versuch",
    "DKIM", "DKIM_Domain", "DKIM_Detail", "Anbieter_SPF", "Anbieter_DKIM", "Anbieter_DMARC", "Anbieter_Pruefer",
]
_CHUNK = 1024 * 1024


@dataclass
class ZipResult:
    path: Path
    hashes: dict[str, str]
    files: int
    size: int
    verified: bool
    problems: list[str] = field(default_factory=list)


def _j(value: str | None) -> str:
    try:
        items = json.loads(value) if value else []
    except json.JSONDecodeError:
        return value or ""
    return ", ".join(items) if isinstance(items, list) else str(items)


def message_rows(case: Case) -> list[dict]:
    cur = case.conn.execute(
        "SELECT a.email, a.evidence_number, f.display_name, m.uid, m.internaldate, m.date_header, m.msg_from, "
        "m.msg_to, m.msg_cc, m.msg_bcc, m.recipient_note, m.delivered_to, m.attachment_count, m.inline_count, "
        "m.attachments, m.subject, m.message_id, m.size, m.zip_path, m.sha256, m.md5, m.sha512, t.nr, "
        "m.dkim_result, m.dkim_domain, m.dkim_detail, m.auth_results "
        "FROM message m JOIN folder f ON f.id = m.folder_id JOIN account a ON a.id = f.account_id "
        "JOIN attempt t ON t.id = m.attempt_id WHERE m.status = 'valid' ORDER BY m.zip_path")
    rows = []
    for r in cur:
        names = ", ".join(a.get("name", "") for a in json.loads(r[14] or "[]"))
        ar = json.loads(r[26] or "{}")
        rows.append(dict(zip(CSV_COLUMNS, [
            r[0], r[1], r[2], r[3], r[4], local_str(r[4]), r[5] or "", _j(r[6]), _j(r[7]), _j(r[8]), _j(r[9]), r[10] or "",
            _j(r[11]), r[12], r[13], names, r[15] or "", r[16] or "", r[17], r[18], r[19], r[20] or "",
            r[21] or "", r[22], r[23] or "", r[24] or "", r[25] or "", ar.get("spf", ""), ar.get("dkim", ""),
            ar.get("dmarc", ""), ar.get("authserv", "")], strict=True)))
    return rows


def write_log_csv(case: Case, path: Path) -> int:
    """Liste aller gesicherten Mails; Semikolon und UTF-8 mit BOM (öffnet direkt in Excel)."""
    return _write_csv(message_rows(case), path)


def _write_csv(rows: list[dict], path: Path) -> int:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _zip_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    dt = datetime.fromtimestamp(mtime, UTC)
    dt = max(min(dt, datetime(2107, 12, 31, 23, 59, 58, tzinfo=UTC)), datetime(1980, 1, 1, tzinfo=UTC))
    return dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second


def _ext_timestamp(mtime: float) -> bytes:
    """Extended-Timestamp-Feld 0x5455 (nur mtime, UTC, Sekunden)."""
    t = max(min(int(mtime), 2**31 - 1), -(2**31))
    return struct.pack("<HHBl", 0x5455, 5, 1, t)


def build_zip(case: Case, cfg: Config | None = None, cancel: CancelToken | None = None,
              progress: ProgressCallback | None = None, account_id: int | None = None) -> ZipResult:
    """Packt alle Konten in ein ZIP – oder mit ``account_id`` nur dieses Konto
    (``<Case>_<Evidence>_<Zeit>.zip``, darin ``log.csv`` nur mit dessen Mails)."""
    cfg = cfg or Config()
    cancel = cancel or CancelToken()
    info = case.info()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rows = message_rows(case)
    account_sql, account_args = "", ()
    if account_id is None:
        zip_path = case.root / f"{sanitize_component(info['case_number'])}_{stamp}.zip"
        csv_path = case.root / "log.csv"
        scope = case.work_dir
    else:
        email, evidence = case.conn.execute("SELECT email, evidence_number FROM account WHERE id = ?",
                                            (account_id,)).fetchone()
        rows = [r for r in rows if r["Konto"] == email]
        ev = sanitize_component(evidence or email)
        zip_path = case.root / f"{sanitize_component(info['case_number'])}_{ev}_{stamp}.zip"
        csv_path = case.root / f"log_{ev}.csv"
        scope = case.work_dir / sanitize_component(email)
        account_sql, account_args = " AND a.id = ?", (account_id,)
    tmp = zip_path.with_suffix(".zip.part")
    prog = Progress(phase="zip", mails_total=len(rows), bytes_total=sum(r["Groesse"] for r in rows))
    case.log("INFO", "zip_started", {"file": zip_path.name, "messages": len(rows), "account_id": account_id})

    _write_csv(rows, csv_path)
    known = {r["Datei"] for r in rows}
    stray = [str(p.relative_to(case.work_dir)).replace("\\", "/") for p in scope.rglob("*.eml")
             if str(p.relative_to(case.work_dir)).replace("\\", "/") not in known] if scope.exists() else []
    for s in stray:
        case.log("WARNING", "zip_file_not_in_case", {"path": s})

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.write(csv_path, "log.csv")
            # Ordnerstruktur inkl. leerer, gewählter Ordner
            for email, name, delim in case.conn.execute(
                    "SELECT a.email, f.display_name, f.delimiter FROM folder f JOIN account a ON a.id = f.account_id "
                    "WHERE f.selected = 1" + account_sql + " ORDER BY a.id, f.id", account_args):
                zi = zipfile.ZipInfo(f"{sanitize_component(email)}/{folder_to_zip_path(name, delim)}/",
                                     date_time=_zip_time(datetime.now(UTC).timestamp()))
                zi.external_attr = 0o40755 << 16 | 0x10
                zf.writestr(zi, b"")
            for r in rows:
                cancel.raise_if_cancelled()
                src = case.work_dir / r["Datei"]
                mtime = src.stat().st_mtime
                zi = zipfile.ZipInfo(r["Datei"], date_time=_zip_time(mtime))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.extra = _ext_timestamp(mtime)
                zi.external_attr = 0o100644 << 16
                with src.open("rb") as fin, zf.open(zi, "w", force_zip64=True) as fout:
                    while chunk := fin.read(_CHUNK):
                        fout.write(chunk)
                prog.mails_done += 1
                prog.bytes_done += r["Groesse"]
                prog.sample()
                if progress:
                    progress(prog)
    except BaseException:
        tmp.unlink(missing_ok=True)
        case.log("WARNING", "zip_aborted", {"file": zip_path.name})
        raise
    tmp.replace(zip_path)

    result = verify_zip(case, zip_path, rows, cancel, progress)
    result.problems = [f"nicht in der Case-Datei, nicht übernommen: {s}" for s in stray] + result.problems
    result.hashes = hash_file(zip_path, cfg.programm.hash_extra)
    (zip_path.parent / (zip_path.name + ".sha256")).write_text(f"{result.hashes['sha256']}  {zip_path.name}\n",
                                                               encoding="utf-8")
    _record_output(case, zip_path, result.hashes, account_id)
    _record_output(case, csv_path, hash_file(csv_path, cfg.programm.hash_extra), account_id)
    case.log("INFO" if result.verified else "ERROR", "zip_done",
             {"file": zip_path.name, "files": result.files, "verified": result.verified, "account_id": account_id,
              **result.hashes})
    return result


def verify_zip(case: Case, zip_path: Path, rows: list[dict] | None = None, cancel: CancelToken | None = None,
               progress: ProgressCallback | None = None) -> ZipResult:
    """Öffnet das ZIP neu und hasht jede .eml gegen die Case-Datei."""
    rows = rows if rows is not None else message_rows(case)
    cancel = cancel or CancelToken()
    expected = {r["Datei"]: r["SHA256"] for r in rows}
    problems: list[str] = []
    prog = Progress(phase="verify", mails_total=len(rows))
    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            problems.append("ZIP-Prüfsumme (CRC) fehlerhaft")
        names = {n for n in zf.namelist() if n.endswith(".eml")}
        for missing in sorted(set(expected) - names):
            problems.append(f"fehlt im ZIP: {missing}")
        for extra in sorted(names - set(expected)):
            problems.append(f"unerwartet im ZIP: {extra}")
        for name in sorted(names & set(expected)):
            cancel.raise_if_cancelled()
            h = MultiHasher()
            with zf.open(name) as f:
                while chunk := f.read(_CHUNK):
                    h.update(chunk)
            if h.hexdigests()["sha256"] != expected[name]:
                problems.append(f"Hash abweichend: {name}")
            prog.mails_done += 1
            if progress:
                progress(prog)
    ok = not [p for p in problems if not p.startswith("nicht in der Case-Datei")]
    case.log("INFO" if ok else "ERROR", "zip_verified", {"file": zip_path.name, "checked": len(names),
                                                          "problems": problems})
    return ZipResult(zip_path, {}, len(names), zip_path.stat().st_size, ok, problems)


def _record_output(case: Case, path: Path, hashes: dict[str, str], account_id: int | None = None) -> None:
    with case.conn:
        case.conn.execute("INSERT INTO output (file_name, sha256, md5, sha512, size, created_utc, account_id) "
                          "VALUES (?,?,?,?,?,?,?)",
                          (path.name, hashes["sha256"], hashes.get("md5"), hashes.get("sha512"),
                           path.stat().st_size, utc_now(), account_id))


def record_output(case: Case, path: Path, cfg: Config | None = None, account_id: int | None = None) -> dict[str, str]:
    cfg = cfg or Config()
    h = hash_file(path, cfg.programm.hash_extra)
    _record_output(case, path, h, account_id)
    return h


def close_case(case: Case) -> None:
    with case.conn:
        case.conn.execute("UPDATE case_info SET closed_utc = ? WHERE id = 1", (utc_now(),))
    case.log("INFO", "case_closed", "")


@dataclass
class FinishResult:
    zips: list[ZipResult]  # ein ZIP für alle Konten oder eines je Konto
    report_txt: Path
    report_pdf: Path
    timestamps: list = field(default_factory=list)  # je ZIP: (TimestampInfo | None, Fehlertext)
    account_reports: list = field(default_factory=list)  # report_<Evidence>.txt/.pdf bei mehreren Konten
    zip_mode: str = "gesamt"

    @property
    def zip(self) -> ZipResult:
        """Erstes (bei „gesamt“ einziges) ZIP."""
        return self.zips[0]

    @property
    def timestamp(self):
        return self.timestamps[0][0] if self.timestamps else None

    @property
    def timestamp_error(self) -> str:
        return "; ".join(e for _, e in self.timestamps if e)


def add_timestamp(case: Case, cfg: Config, zip_result: ZipResult, account_id: int | None = None):
    """RFC-3161-Zeitstempel für den ZIP-Hash holen (falls ``tsa_url`` gesetzt).

    Ein Fehler bricht den Abschluss nicht ab: er wird als Warnung protokolliert und
    im Bericht genannt. Rückgabe ``(TimestampInfo | None, Fehlertext)``.
    """
    from fed.core import timestamp as tsmod

    if not cfg.tsa_url:
        return None, ""
    tsr = zip_result.path.parent / (zip_result.path.name + ".tsr")
    case.log("INFO", "timestamp_requested", {"tsa": cfg.tsa_url, "sha256": zip_result.hashes["sha256"]})
    try:
        info = tsmod.request_timestamp(zip_result.hashes["sha256"], cfg.tsa_url, tsr, cfg.proxy_url,
                                       cfg.programm.socket_timeout_s)
    except tsmod.TimestampError as e:
        case.log("WARNING", "timestamp_failed", {"tsa": cfg.tsa_url, "error": str(e), "zip": zip_result.path.name,
                                                  "account_id": account_id})
        return None, str(e)
    gen = datetime.fromisoformat(info.gen_time.replace("Z", "+00:00"))
    offset = round((datetime.now(UTC) - gen).total_seconds(), 3)
    case.log("INFO", "timestamp_done", {
        "file": tsr.name, "tsa": cfg.tsa_url, "gen_time": info.gen_time, "serial": info.serial,
        "policy": info.policy, "accuracy": info.accuracy, "nonce": str(info.nonce),
        "sha256": info.hashed_message, "local_minus_tsa_s": offset, "account_id": account_id})
    record_output(case, tsr, cfg, account_id)
    record_output(case, tsr.with_suffix(".tsq"), cfg, account_id)
    return info, ""


def delete_work_dir(case: Case, zip_result: ZipResult | list[ZipResult]) -> dict:
    """Löscht ``work/`` – nur nach erfolgreicher Verifikation aller ZIPs. ``discarded/`` bleibt erhalten."""
    import shutil

    zips = zip_result if isinstance(zip_result, list) else [zip_result]
    if not zips or not all(z.verified for z in zips):
        raise RuntimeError("Arbeitsordner wird nur nach erfolgreicher ZIP-Verifikation gelöscht")
    files = [p for p in case.work_dir.rglob("*") if p.is_file()] if case.work_dir.exists() else []
    info = {"files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "zip": ", ".join(z.path.name for z in zips),
            "zip_sha256": ", ".join(z.hashes.get("sha256", "") for z in zips),
            "verified_files": sum(z.files for z in zips)}
    if case.work_dir.exists():
        shutil.rmtree(case.work_dir)
    case.log("INFO", "work_dir_deleted", info)
    return info


def finish_case(case: Case, cfg: Config | None = None, language: str | None = None,
                cancel: CancelToken | None = None, progress: ProgressCallback | None = None,
                delete_work: bool | None = None) -> FinishResult:
    """ZIP packen und prüfen, Case abschließen, ggf. Arbeitsordner löschen, Berichte erzeugen und hashen.

    ``delete_work``: ``None`` = nach Config (``arbeitsordner_loeschen == "ja"``).
    """
    from fed.core.report import write_reports

    cfg = cfg or Config()
    if case.closed:
        raise ValueError("Case ist bereits abgeschlossen")
    running = case.conn.execute("SELECT COUNT(*) FROM attempt WHERE status = 'running'").fetchone()[0]
    if running:
        raise ValueError("Es läuft noch ein Versuch")
    mode = zip_mode_for(case, cfg)
    case.log("INFO", "zip_mode_used", {"mode": mode})
    if mode == "je_konto":
        ids = [r[0] for r in case.conn.execute("SELECT id FROM account ORDER BY id")]
        zips = [build_zip(case, cfg, cancel, progress, acc) for acc in ids]
        write_log_csv(case, case.root / "log.csv")  # Gesamtliste aller Konten neben den ZIPs
        record_output(case, case.root / "log.csv", cfg)
    else:
        ids = [None]
        zips = [build_zip(case, cfg, cancel, progress)]
    bad = [z for z in zips if not z.verified]
    if bad:
        raise RuntimeError("ZIP-Verifikation fehlgeschlagen: " + "; ".join(p for z in bad for p in z.problems))
    timestamps = [add_timestamp(case, cfg, z, acc) for z, acc in zip(zips, ids, strict=True)]
    close_case(case)
    if delete_work is None:
        delete_work = cfg.programm.arbeitsordner_loeschen == "ja"
    if delete_work:
        delete_work_dir(case, zips)
    else:
        case.log("INFO", "work_dir_kept", {"path": str(case.work_dir)})
    from fed.core.languages import report_language_for

    report_language = report_language_for(language or cfg.programm.bericht_sprache or cfg.programm.sprache)
    txt, pdf = write_reports(case, report_language,
                             cfg.programm.bericht_mailliste, cfg.programm.bericht_mailliste_max)
    record_output(case, txt, cfg)
    record_output(case, pdf, cfg)
    from fed.core.report import account_reports

    return FinishResult(zips, txt, pdf, timestamps, account_reports(case), mode)


def zip_mode_for(case: Case, cfg: Config | None = None) -> str:
    """ZIP-Aufteilung beim Abschluss: ein Konto -> ``gesamt``; sonst Entscheidung im Case,
    sonst Einstellung (``fragen`` ohne Antwort -> ``gesamt``)."""
    n = case.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0]
    if n <= 1:
        return "gesamt"
    mode = case.zip_mode() or (cfg or Config()).programm.zip_aufteilung
    return mode if mode in case.ZIP_MODES else "gesamt"
