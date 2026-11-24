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
from fed.core.naming import folder_to_zip_path, sanitize_component, zip_basename
from fed.core.timefmt import local_str
from fed.core.msg import code, describe
from fed.core.msg import t as _t

# Feste englische Spaltennamen (unabhängig von der Sprache, wie die Namen im Case-Ordner)
CSV_COLUMNS = [
    "Account", "Evidence", "Folder", "UID", "Received_UTC", "Received_Local", "Date_Header", "From", "To", "CC", "BCC",
    "Recipient_Note", "Delivered_To", "Attachments", "Inline", "Attachment_Names", "Subject", "Message_ID",
    "Size", "File", "SHA256", "MD5", "SHA512", "Attempt",
    "DKIM", "DKIM_Domain", "DKIM_Detail", "Provider_SPF", "Provider_DKIM", "Provider_DMARC", "Provider_Authserv",
    "Gmail_MsgID", "Gmail_ThrID", "Gmail_Labels", "Gmail_Duplicate",
    "Source", "Partial",
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
    # Gmail-Dubletten je (Konto, gm_msgid) EINMAL zählen – früher lief dafür je Zeile eine korrelierte
    # Unterabfrage ohne Index auf gm_msgid (O(n^2); bei ~14.000 Mails minutenlang). Jetzt O(n).
    dup: dict = {}
    for acc, gid, n in case.conn.execute(
            "SELECT f.account_id, m.gm_msgid, COUNT(*) FROM message m JOIN folder f ON f.id = m.folder_id "
            "WHERE m.status = 'valid' AND m.gm_msgid IS NOT NULL GROUP BY f.account_id, m.gm_msgid"):
        dup[(acc, gid)] = n
    cur = case.conn.execute(
        "SELECT a.email, a.evidence_number, f.display_name, m.uid, m.internaldate, m.date_header, m.msg_from, "
        "m.msg_to, m.msg_cc, m.msg_bcc, m.recipient_note, m.delivered_to, m.attachment_count, m.inline_count, "
        "m.attachments, m.subject, m.message_id, m.size, m.zip_path, m.sha256, m.md5, m.sha512, t.nr, "
        "m.dkim_result, m.dkim_domain, m.dkim_detail, m.auth_results, m.gm_msgid, m.gm_thrid, m.gm_labels, "
        "m.source, m.partial, f.account_id "
        "FROM message m JOIN folder f ON f.id = m.folder_id JOIN account a ON a.id = f.account_id "
        "JOIN attempt t ON t.id = m.attempt_id WHERE m.status = 'valid' ORDER BY m.zip_path")
    rows = []
    for r in cur:
        names = ", ".join(a.get("name", "") for a in json.loads(r[14] or "[]"))
        ar = json.loads(r[26] or "{}")
        gm_dup = "yes" if r[27] and dup.get((r[32], r[27]), 0) > 1 else ""
        rows.append(dict(zip(CSV_COLUMNS, [
            r[0], r[1], r[2], r[3], r[4], local_str(r[4]), r[5] or "", _j(r[6]), _j(r[7]), _j(r[8]), _j(r[9]), r[10] or "",
            _j(r[11]), r[12], r[13], names, r[15] or "", r[16] or "", r[17], r[18], r[19], r[20] or "",
            r[21] or "", r[22], r[23] or "", r[24] or "", r[25] or "", ar.get("spf", ""), ar.get("dkim", ""),
            ar.get("dmarc", ""), ar.get("authserv", ""), r[27] or "", r[28] or "",
            ", ".join(json.loads(r[29])) if r[29] else "", gm_dup,
            "apple_mail" if r[30] == "apple_mail" else "imap", "yes" if r[31] else ""], strict=True)))
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


def _unique_zip(folder: Path, base: str) -> Path:
    """`<base>.zip` im Ordner; existiert der Name schon, `_2`, `_3` … anhängen (Muster ohne Zeit)."""
    target = folder / f"{base}.zip"
    n = 2
    while target.exists() or target.with_suffix(".zip.part").exists():
        target = folder / f"{base}_{n}.zip"
        n += 1
    return target


def build_zip(case: Case, cfg: Config | None = None, cancel: CancelToken | None = None,
              progress: ProgressCallback | None = None, account_id: int | None = None,
              case_bytes: int = 0) -> ZipResult:
    """Packt alle Konten in ein ZIP – oder mit ``account_id`` nur dieses Konto
    (``<Case>_<Evidence>_<Zeit>.zip``, darin ``log.csv`` nur mit dessen Mails).
    ``case_bytes``: Gesamt-Bytes aller Mails im Fall (für die fallweite Abschluss-Restzeit)."""
    cfg = cfg or Config()
    cancel = cancel or CancelToken()
    acc_label = ""
    if account_id is not None:
        email, evidence = case.conn.execute("SELECT email, evidence_number FROM account WHERE id = ?",
                                            (account_id,)).fetchone()
        acc_label = f"{evidence} · {email}" if evidence else email  # z. B. "DF001 · info@…"

    def _p(**kw):  # Progress-Hilfe: Konto-Label und Fall-Gesamtbytes immer mitgeben
        return Progress(account=acc_label, case_bytes_total=case_bytes, **kw)

    if progress:
        progress(_p(phase="collect"))  # 14k Mails laden kann dauern – sofort anzeigen
    info = case.info()
    rows = message_rows(case)
    account_sql, account_args = "", ()
    pattern = cfg.programm.zip_name_muster
    now = datetime.now(UTC)
    if account_id is None:
        base = zip_basename(pattern, case_number=info["case_number"], when=now,
                            spaces_to_underscore=case.replace_spaces)
        zip_path = _unique_zip(case.root, base)
        csv_path = case.root / "log.csv"
        scope = case.work_dir
    else:
        rows = [r for r in rows if r["Account"] == email]
        base = zip_basename(pattern, case_number=info["case_number"], evidence=evidence or "", email=email,
                            when=now, spaces_to_underscore=case.replace_spaces)
        zip_path = _unique_zip(case.root, base)
        ev = case.file_name(evidence or email)
        csv_path = case.root / f"log_{ev}.csv"
        scope = case.work_dir / sanitize_component(email)
        account_sql, account_args = " AND a.id = ?", (account_id,)
    tmp = zip_path.with_suffix(".zip.part")
    prog = _p(phase="zip", mails_total=len(rows), bytes_total=sum(r["Size"] for r in rows))
    case.log("INFO", "zip_started", {"file": zip_path.name, "messages": len(rows), "account_id": account_id})

    if progress:
        progress(_p(phase="csv", mails_total=len(rows)))  # log.csv schreiben
    _write_csv(rows, csv_path)
    known = {r["File"] for r in rows}
    if progress:
        progress(_p(phase="scan"))  # Arbeitsordner auf zusätzliche Dateien prüfen
    stray = [str(p.relative_to(case.work_dir)).replace("\\", "/") for p in scope.rglob("*.eml")
             if str(p.relative_to(case.work_dir)).replace("\\", "/") not in known] if scope.exists() else []
    for s in stray:
        # Konto mitgeben, damit die Warnung auch im Einzelbericht des betroffenen Kontos erscheint
        # (ein ZIP je Konto); ohne Konto bliebe sie nur im Gesamtbericht sichtbar.
        details = {"path": s} if account_id is None else {"path": s, "account_id": account_id}
        case.log("WARNING", "zip_file_not_in_case", details)

    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.write(csv_path, "log.csv")
            # Ordnerstruktur inkl. leerer, gewählter Ordner (gleiche Archiv-Pfade nur einmal schreiben,
            # sonst warnt zipfile „Duplicate name“, z. B. bei gleichnamigen Ordnern aus einem Apple-Mail-Import)
            written_dirs: set[str] = set()
            for email, name, delim in case.conn.execute(
                    "SELECT a.email, f.display_name, f.delimiter FROM folder f JOIN account a ON a.id = f.account_id "
                    "WHERE f.selected = 1" + account_sql + " ORDER BY a.id, f.id", account_args):
                arc = f"{sanitize_component(email)}/{folder_to_zip_path(name, delim)}/"
                if arc in written_dirs:
                    continue
                written_dirs.add(arc)
                zi = zipfile.ZipInfo(arc, date_time=_zip_time(datetime.now(UTC).timestamp()))
                zi.external_attr = 0o40755 << 16 | 0x10
                zf.writestr(zi, b"")
            for r in rows:
                cancel.raise_if_cancelled()
                src = case.work_dir / r["File"]
                mtime = src.stat().st_mtime
                zi = zipfile.ZipInfo(r["File"], date_time=_zip_time(mtime))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.extra = _ext_timestamp(mtime)
                zi.external_attr = 0o100644 << 16
                with src.open("rb") as fin, zf.open(zi, "w", force_zip64=True) as fout:
                    while chunk := fin.read(_CHUNK):
                        fout.write(chunk)
                prog.mails_done += 1
                prog.bytes_done += r["Size"]
                prog.sample()
                if progress:
                    progress(prog)
    except BaseException:
        tmp.unlink(missing_ok=True)
        case.log("WARNING", "zip_aborted", {"file": zip_path.name})
        raise
    tmp.replace(zip_path)

    result = verify_zip(case, zip_path, rows, cancel, progress, account=acc_label, case_bytes=case_bytes)
    result.problems = [code("not_in_case", s) for s in stray] + result.problems
    result.hashes = hash_file(zip_path, cfg.programm.hash_extra)
    (zip_path.parent / (zip_path.name + ".sha256")).write_text(f"{result.hashes['sha256']}  {zip_path.name}\n",
                                                               encoding="utf-8")
    _record_output(case, zip_path, result.hashes, account_id)
    _record_output(case, csv_path, hash_file(csv_path, cfg.programm.hash_extra), account_id)
    case.log("INFO" if result.verified else "ERROR", "zip_done",
             {"file": zip_path.name, "files": result.files, "verified": result.verified, "account_id": account_id,
              **result.hashes})
    return result


def verify_case_zips(case: Case, cancel: CancelToken | None = None) -> list[ZipResult]:
    """Alle ZIPs des Case (ein ZIP gesamt oder eines je Konto) gegen die Case-Datei prüfen –
    die verschlüsselte Weitergabe-Kopie ausgenommen."""
    rows = message_rows(case)
    out = []
    for name, acc in case.conn.execute(
            "SELECT file_name, account_id FROM output WHERE file_name LIKE '%.zip' "
            "AND file_name NOT LIKE '%\\_encrypted.zip' ESCAPE '\\' ORDER BY id"):
        path = case.root / name
        if not path.exists():
            continue
        if acc is None:
            subset = rows
        else:
            email = case.conn.execute("SELECT email FROM account WHERE id = ?", (acc,)).fetchone()[0]
            subset = [r for r in rows if r["Account"] == email]
        out.append(verify_zip(case, path, subset, cancel))
    return out


def verify_zip(case: Case, zip_path: Path, rows: list[dict] | None = None, cancel: CancelToken | None = None,
               progress: ProgressCallback | None = None, account: str = "", case_bytes: int = 0) -> ZipResult:
    """Öffnet das ZIP neu und hasht jede .eml gegen die Case-Datei."""
    rows = rows if rows is not None else message_rows(case)
    cancel = cancel or CancelToken()
    expected = {r["File"]: r["SHA256"] for r in rows}
    problems: list[str] = []
    prog = Progress(phase="verify", mails_total=len(rows), bytes_total=sum(r["Size"] for r in rows),
                    account=account, case_bytes_total=case_bytes)
    with zipfile.ZipFile(zip_path) as zf:
        if zf.testzip() is not None:
            problems.append(code("crc_error"))
        names = {n for n in zf.namelist() if n.endswith(".eml")}
        for missing in sorted(set(expected) - names):
            problems.append(code("missing_in_zip", missing))
        for extra in sorted(names - set(expected)):
            problems.append(code("unexpected_in_zip", extra))
        for name in sorted(names & set(expected)):
            cancel.raise_if_cancelled()
            h = MultiHasher()
            with zf.open(name) as f:
                while chunk := f.read(_CHUNK):
                    h.update(chunk)
                    prog.bytes_done += len(chunk)
            if h.hexdigests()["sha256"] != expected[name]:
                problems.append(code("hash_mismatch", name))
            prog.mails_done += 1
            prog.sample()
            if progress:
                progress(prog)
    ok = not [p for p in problems if not p.startswith("not_in_case")]
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


def write_dns_keys_csv(case: Case, path: Path) -> int:
    """Alle aufbewahrten DKIM-DNS-Schlüssel vollständig als CSV (der Bericht zeigt bei großen Fällen nur eine Auswahl)."""
    import csv

    rows = case.conn.execute(
        "SELECT name, status, fetched_utc, sha256, record FROM dns_record ORDER BY name, id").fetchall()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Status", "Abgerufen_UTC", "SHA-256", "Record"])
        for name, status, fetched, sha, record in rows:
            w.writerow([name, status, fetched, sha or "", (record or "").replace("\n", " ")])
    return len(rows)


def write_dns_snapshot_csv(case: Case, path: Path) -> int:
    """DNS-Schnappschuss der Domains vollständig als CSV (der Bericht kann statt der Tabelle darauf verweisen)."""
    import csv

    rows = case.conn.execute(
        "SELECT domain, rtype, name, value, status, fetched_utc, sha256 FROM domain_dns ORDER BY domain, rtype, id"
    ).fetchall()
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Domain", "Typ", "Name", "Wert", "Status", "Abgerufen_UTC", "SHA-256"])
        for domain, rtype, name, value, status, fetched, sha in rows:
            w.writerow([domain, rtype, name, (value or "").replace("\n", " "), status, fetched, sha or ""])
    return len(rows)


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
    encrypted: dict | None = None  # verschlüsselte Weitergabe-Kopie (ohne Passwort)

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
    import threading

    tsr = zip_result.path.parent / (zip_result.path.name + ".tsr")
    case.log("INFO", "timestamp_requested", {"tsa": cfg.tsa_url, "sha256": zip_result.hashes["sha256"]})
    # Harte Gesamtfrist: urllibs timeout gilt je Socket-Operation, nicht als Gesamtzeit. Ein TSA, der die
    # Verbindung annimmt aber stockt (oder gefilterte Pakete beim Connect), würde den Abschluss sonst
    # minutenlang blockieren. Der Zeitstempel ist optional – nach der Frist geht es ohne ihn weiter.
    timeout = cfg.programm.socket_timeout_s
    box: dict = {}

    def _worker() -> None:
        try:
            box["info"] = tsmod.request_timestamp(zip_result.hashes["sha256"], cfg.tsa_url, tsr, cfg.proxy_url, timeout)
        except BaseException as e:   # im Thread gefangen, unten ausgewertet
            box["err"] = e

    th = threading.Thread(target=_worker, name="fed-tsa", daemon=True)
    th.start()
    th.join(timeout + 2)
    if th.is_alive():
        msg = _t("Zeitstempeldienst hat nicht innerhalb von {s}s geantwortet", s=int(timeout + 2))
        case.log("WARNING", "timestamp_timeout", {"tsa": cfg.tsa_url, "zip": zip_result.path.name,
                                                  "account_id": account_id})
        return None, msg
    err = box.get("err")
    if isinstance(err, tsmod.TimestampError):
        case.log("WARNING", "timestamp_failed", {"tsa": cfg.tsa_url, "error": str(err), "zip": zip_result.path.name,
                                                  "account_id": account_id})
        return None, str(err)
    if err is not None:
        raise err
    info = box["info"]
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
        raise RuntimeError(_t("Arbeitsordner wird nur nach erfolgreicher ZIP-Verifikation gelöscht"))
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
                delete_work: bool | None = None, encrypt_password: str | None = None,
                closing_note: str | None = None, closing_note_html: str | None = None) -> FinishResult:
    """ZIP packen und prüfen, Case abschließen, ggf. Arbeitsordner löschen, Berichte erzeugen und hashen.

    ``delete_work``: ``None`` = nach Config (``arbeitsordner_loeschen == "ja"``).
    ``encrypt_password``: zusätzlich verschlüsselte Kopie für die Weitergabe (Passwort wird nie gespeichert).
    ``closing_note``/``closing_note_html``: Abschlussvermerk für den Bericht (``None`` = unverändert lassen).
    """
    from fed.core.report import write_reports

    cfg = cfg or Config()
    if case.closed:
        raise ValueError(_t("Fall ist bereits abgeschlossen"))
    if encrypt_password is not None:
        from fed.core.encrypt import check_password

        problem = check_password(encrypt_password)
        if problem:
            raise ValueError(_t("Passwort für die verschlüsselte Kopie ungeeignet: {problem}", problem=problem))
    case.recover_interrupted()
    running = case.conn.execute("SELECT COUNT(*) FROM attempt WHERE status = 'running'").fetchone()[0]
    if running:
        raise ValueError(_t("Es läuft noch ein Versuch"))
    from fed.core.syscheck import host_snapshot

    case.log("INFO", "finalize_started", {"host": host_snapshot()})  # Rechner/Benutzer des Abschlusses
    if closing_note is not None or closing_note_html is not None:
        case.set_closing_note(closing_note or "", closing_note_html)
    mode = zip_mode_for(case, cfg)
    case.log("INFO", "zip_mode_used", {"mode": mode})
    # Gesamt-Bytes aller Mails im Fall – für die fallweite Abschluss-Restzeit (ZIP + Verify je einmal).
    case_bytes = case.conn.execute("SELECT COALESCE(SUM(size), 0) FROM message WHERE status = 'valid'").fetchone()[0]
    if mode == "je_konto":
        ids = [r[0] for r in case.conn.execute("SELECT id FROM account ORDER BY id")]
        zips = [build_zip(case, cfg, cancel, progress, acc, case_bytes=case_bytes) for acc in ids]
        write_log_csv(case, case.root / "log.csv")  # Gesamtliste aller Konten neben den ZIPs
        record_output(case, case.root / "log.csv", cfg)
    else:
        ids = [None]
        zips = [build_zip(case, cfg, cancel, progress, case_bytes=case_bytes)]
    bad = [z for z in zips if not z.verified]
    if bad:
        raise RuntimeError(_t("ZIP-Verifikation fehlgeschlagen: {problems}", problems="; ".join(describe(p) for z in bad for p in z.problems)))

    def _phase(name: str) -> None:  # Schrittmarke für die GUI (diese Schritte haben keinen Zähler)
        if progress:
            progress(Progress(phase=name))

    _phase("timestamp")
    timestamps = []
    skip = False  # ist der TSA beim ersten Konto nicht erreichbar, nicht jedes weitere Konto erneut abwarten
    for z, acc in zip(zips, ids, strict=True):
        if skip:
            case.log("WARNING", "timestamp_skipped", {"zip": z.path.name, "account_id": acc})
            timestamps.append((None, _t("Zeitstempel übersprungen – Dienst zuvor nicht erreichbar")))
            continue
        info, err = add_timestamp(case, cfg, z, acc)
        timestamps.append((info, err))
        if info is None and err:          # Zeitstempel fehlgeschlagen/zeitüberschritten → Rest überspringen
            skip = True
    close_case(case)
    if delete_work is None:
        delete_work = cfg.programm.arbeitsordner_loeschen == "ja"
    if delete_work:
        delete_work_dir(case, zips)
    else:
        case.log("INFO", "work_dir_kept", {"path": str(case.work_dir)})
    from fed.core.languages import report_language_for

    if case.conn.execute("SELECT 1 FROM dns_record LIMIT 1").fetchone():
        write_dns_keys_csv(case, case.root / "dns_keys.csv")  # vollständige DKIM-Schlüsselliste als eigene Datei
        record_output(case, case.root / "dns_keys.csv", cfg)
    if case.conn.execute("SELECT 1 FROM domain_dns LIMIT 1").fetchone():
        write_dns_snapshot_csv(case, case.root / "dns_snapshot.csv")  # DNS-Schnappschuss als eigene Datei
        record_output(case, case.root / "dns_snapshot.csv", cfg)
    report_language = report_language_for(language or cfg.programm.bericht_sprache or cfg.programm.sprache)
    txt, pdf = write_reports(case, report_language, cfg.programm.bericht_mailliste,
                             cfg.programm.bericht_mailliste_max, encrypted_planned=encrypt_password is not None,
                             progress=progress, dkim_list=cfg.programm.bericht_dkimliste,
                             dkim_list_max=cfg.programm.bericht_dkimliste_max, dns_mode=cfg.programm.bericht_dns,
                             ts_qualified=cfg.tsa_qualifiziert)
    record_output(case, txt, cfg)
    record_output(case, pdf, cfg)
    from fed.core.report import account_reports

    encrypted = None
    if encrypt_password is not None:
        _phase("encrypt")
        encrypted = encrypted_copy(case, [z.path for z in zips], encrypt_password, cfg)
    return FinishResult(zips, txt, pdf, timestamps, account_reports(case), mode, encrypted)


def encrypted_copy(case: Case, zip_paths: list[Path], password: str, cfg: Config | None = None) -> dict:
    """Verschlüsselte Weitergabe-Kopie erstellen, prüfen, hashen und protokollieren (ohne Passwort)."""
    from fed.core.encrypt import create_copy, files_for_copy

    target = case.root / f"{case.file_name(case.info()['case_number'])}_encrypted.zip"
    info = create_copy(target, files_for_copy(case.root, zip_paths), password)
    if not info["verified"]:
        case.log("ERROR", "encrypted_copy_failed", {k: v for k, v in info.items() if k != "files"})
        raise RuntimeError(_t("Prüfung der verschlüsselten Kopie fehlgeschlagen"))
    side = target.with_name(target.name + ".sha256")
    side.write_text(f"{info['sha256']}  {target.name}\n", encoding="utf-8")
    record_output(case, target, cfg)
    case.log("INFO", "encrypted_copy_created", info)
    return info


def zip_mode_for(case: Case, cfg: Config | None = None) -> str:
    """ZIP-Aufteilung beim Abschluss: ein Konto -> ``gesamt``; sonst Entscheidung im Case,
    sonst Einstellung (``fragen`` ohne Antwort -> ``gesamt``)."""
    n = case.conn.execute("SELECT COUNT(*) FROM account").fetchone()[0]
    if n <= 1:
        return "gesamt"
    mode = case.zip_mode() or (cfg or Config()).programm.zip_aufteilung
    return mode if mode in case.ZIP_MODES else "gesamt"


def preview_zip_names(case: Case, cfg: Config | None = None,
                      when: datetime | None = None) -> list[tuple[str, str]]:
    """Dateinamen der ZIP-Archive, die ``finish_case`` erzeugen würde – ohne etwas zu schreiben.

    Spiegelt die Namensbildung aus :func:`build_zip` (gleiches Muster, gleiche Eindeutigkeit per
    ``_unique_zip``, das nur prüft, nichts anlegt). Rückgabe je Archiv ``(Bezeichnung, Dateiname)``:
    bei ``gesamt`` ein Eintrag mit leerer Bezeichnung, bei ``je_konto`` einer je Konto mit
    Asservatennummer (sonst Adresse) als Bezeichnung. ``when`` nur für reproduzierbare Tests;
    zeitabhängige Muster (``{zeit}`` …) sind in der Vorschau naturgemäß nur ein Momentwert.
    """
    cfg = cfg or Config()
    info = case.info()
    pattern = cfg.programm.zip_name_muster
    now = when or datetime.now(UTC)
    if zip_mode_for(case, cfg) == "je_konto":
        out: list[tuple[str, str]] = []
        for _aid, email, evidence in case.conn.execute(
                "SELECT id, email, evidence_number FROM account ORDER BY id"):
            base = zip_basename(pattern, case_number=info["case_number"], evidence=evidence or "",
                                email=email, when=now, spaces_to_underscore=case.replace_spaces)
            out.append((evidence or email, _unique_zip(case.root, base).name))
        return out
    base = zip_basename(pattern, case_number=info["case_number"], when=now,
                        spaces_to_underscore=case.replace_spaces)
    return [("", _unique_zip(case.root, base).name)]
