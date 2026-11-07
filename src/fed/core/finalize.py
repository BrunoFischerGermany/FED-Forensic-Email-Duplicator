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

CSV_COLUMNS = [
    "Konto", "Evidence", "Ordner", "UID", "Eingangszeit_UTC", "Date_Header", "Absender", "An", "CC", "BCC",
    "Empfaenger_Hinweis", "Delivered_To", "Anlagen", "Inline", "Anlagen_Namen", "Betreff", "Message_ID",
    "Groesse", "Datei", "SHA256", "MD5", "SHA512", "Versuch",
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
        "m.attachments, m.subject, m.message_id, m.size, m.zip_path, m.sha256, m.md5, m.sha512, t.nr "
        "FROM message m JOIN folder f ON f.id = m.folder_id JOIN account a ON a.id = f.account_id "
        "JOIN attempt t ON t.id = m.attempt_id WHERE m.status = 'valid' ORDER BY m.zip_path")
    rows = []
    for r in cur:
        names = ", ".join(a.get("name", "") for a in json.loads(r[14] or "[]"))
        rows.append(dict(zip(CSV_COLUMNS, [
            r[0], r[1], r[2], r[3], r[4], r[5] or "", _j(r[6]), _j(r[7]), _j(r[8]), _j(r[9]), r[10] or "",
            _j(r[11]), r[12], r[13], names, r[15] or "", r[16] or "", r[17], r[18], r[19], r[20] or "",
            r[21] or "", r[22]], strict=True)))
    return rows


def write_log_csv(case: Case, path: Path) -> int:
    """Liste aller gesicherten Mails; Semikolon und UTF-8 mit BOM (öffnet direkt in Excel)."""
    rows = message_rows(case)
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
              progress: ProgressCallback | None = None) -> ZipResult:
    cfg = cfg or Config()
    cancel = cancel or CancelToken()
    info = case.info()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    zip_path = case.root / f"{sanitize_component(info['case_number'])}_{stamp}.zip"
    tmp = zip_path.with_suffix(".zip.part")
    rows = message_rows(case)
    prog = Progress(phase="zip", mails_total=len(rows), bytes_total=sum(r["Groesse"] for r in rows))
    case.log("INFO", "zip_started", {"file": zip_path.name, "messages": len(rows)})

    csv_path = case.root / "log.csv"
    write_log_csv(case, csv_path)
    known = {r["Datei"] for r in rows}
    stray = [str(p.relative_to(case.work_dir)).replace("\\", "/") for p in case.work_dir.rglob("*.eml")
             if str(p.relative_to(case.work_dir)).replace("\\", "/") not in known]
    for s in stray:
        case.log("WARNING", "zip_file_not_in_case", {"path": s})

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.write(csv_path, "log.csv")
            # Ordnerstruktur inkl. leerer, gewählter Ordner
            for email, name, delim in case.conn.execute(
                    "SELECT a.email, f.display_name, f.delimiter FROM folder f JOIN account a ON a.id = f.account_id "
                    "WHERE f.selected = 1 ORDER BY a.id, f.id"):
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
    _record_output(case, zip_path, result.hashes)
    _record_output(case, csv_path, hash_file(csv_path, cfg.programm.hash_extra))
    case.log("INFO" if result.verified else "ERROR", "zip_done",
             {"file": zip_path.name, "files": result.files, "verified": result.verified, **result.hashes})
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


def _record_output(case: Case, path: Path, hashes: dict[str, str]) -> None:
    with case.conn:
        case.conn.execute("INSERT INTO output (file_name, sha256, md5, sha512, size, created_utc) VALUES (?,?,?,?,?,?)",
                          (path.name, hashes["sha256"], hashes.get("md5"), hashes.get("sha512"),
                           path.stat().st_size, utc_now()))


def record_output(case: Case, path: Path, cfg: Config | None = None) -> dict[str, str]:
    cfg = cfg or Config()
    h = hash_file(path, cfg.programm.hash_extra)
    _record_output(case, path, h)
    return h


def close_case(case: Case) -> None:
    with case.conn:
        case.conn.execute("UPDATE case_info SET closed_utc = ? WHERE id = 1", (utc_now(),))
    case.log("INFO", "case_closed", "")


@dataclass
class FinishResult:
    zip: ZipResult
    report_txt: Path
    report_pdf: Path


def finish_case(case: Case, cfg: Config | None = None, language: str | None = None,
                cancel: CancelToken | None = None, progress: ProgressCallback | None = None) -> FinishResult:
    """ZIP packen und prüfen, Case abschließen, Berichte erzeugen und hashen."""
    from fed.core.report import write_reports

    cfg = cfg or Config()
    if case.closed:
        raise ValueError("Case ist bereits abgeschlossen")
    running = case.conn.execute("SELECT COUNT(*) FROM attempt WHERE status = 'running'").fetchone()[0]
    if running:
        raise ValueError("Es läuft noch ein Versuch")
    z = build_zip(case, cfg, cancel, progress)
    if not z.verified:
        raise RuntimeError("ZIP-Verifikation fehlgeschlagen: " + "; ".join(z.problems))
    close_case(case)
    txt, pdf = write_reports(case, language or cfg.programm.bericht_sprache or cfg.programm.sprache,
                             cfg.programm.bericht_mailliste, cfg.programm.bericht_mailliste_max)
    record_output(case, txt, cfg)
    record_output(case, pdf, cfg)
    return FinishResult(z, txt, pdf)
