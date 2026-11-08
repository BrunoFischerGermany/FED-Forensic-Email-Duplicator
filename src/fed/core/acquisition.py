"""Ablauf der Sicherung: Konto registrieren, Scan, Versuche (Fortsetzen/Neubeginn), Download.

Ein Versuch:
1. Versuch anlegen (Fortsetzen oder Neubeginn mit Grund)
2. Bei Fortsetzen: UIDVALIDITY prüfen, vorhandene Dateien nachhashen
3. Vorher-Status je gewähltem Ordner (MESSAGES, UNSEEN, UIDNEXT, UIDVALIDITY)
4. Je Ordner fehlende UIDs laden -> ``work/`` (``.part``, fsync, umbenennen),
   Hashes, Zeitstempel = Eingangszeit, Metadaten, Commit je Mail
5. Nachher-Status je Ordner, Soll/Ist

Das ZIP entsteht erst beim Abschluss des Case (``build_zip``, Meilenstein 4).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from imaplib import IMAP4
from pathlib import Path

from fed.core import mailmeta
from fed.core.cancel import Cancelled, CancelToken, Progress, ProgressCallback
from fed.core.case import Case, utc_now
from fed.core.config import Config
from fed.core.hashing import hash_bytes, hash_file
from fed.core.imap_reader import FolderInfo, ImapError, ReadOnlyImap
from fed.core.naming import eml_filename, folder_to_zip_path, sanitize_component
from fed.core.msg import t as _t

MAX_RECONNECTS = 3
NETWORK_ERRORS = (OSError, socket.timeout, ssl.SSLError, IMAP4.abort)


class Mode(StrEnum):
    RESUME = "resume"
    RESTART = "restart"


class ResumeNotPossible(RuntimeError):
    """Fortsetzen ist nicht möglich (z. B. UIDVALIDITY geändert) – nur Neubeginn."""


@dataclass
class AccountSpec:
    email: str
    evidence_number: str
    server: str
    port: int = 993
    security: str = "ssl"
    username: str = ""
    auth_method: str = "password"
    autoconfig_source: str = "manual"
    date_from: datetime | None = None
    date_to: datetime | None = None  # einschließlich
    notes: str = ""  # Notiz des Examiners zum Konto

    def __post_init__(self) -> None:
        from fed.core.naming import normalize_email

        self.email = normalize_email(self.email)
        self.username = normalize_email(self.username)


@dataclass
class AttemptResult:
    attempt_id: int
    nr: int
    status: str
    saved: int = 0
    skipped_existing: int = 0
    expected: int = 0
    warnings: list[str] = field(default_factory=list)


Connector = Callable[[], ReadOnlyImap]
"""Liefert eine verbundene und angemeldete Verbindung (für Reconnects)."""


# ---------------------------------------------------------------- Konto und Scan
def register_account(case: Case, spec: AccountSpec, imap: ReadOnlyImap) -> int:
    tls = imap.tls_info()
    info = imap.server_info()
    with case.conn:
        cur = case.conn.execute(
            "INSERT INTO account (email, evidence_number, server, port, security, auth_method, username, "
            "autoconfig_source, tls_cert_sha256, capabilities, server_id, namespaces, quota, date_from, date_to, "
            "notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (spec.email, spec.evidence_number, spec.server, spec.port, spec.security, spec.auth_method,
             spec.username or spec.email, spec.autoconfig_source, tls["sha256"], json.dumps(info["capabilities"]),
             info.get("id"), info.get("namespace"), json.dumps(info.get("quota")),
             spec.date_from.date().isoformat() if spec.date_from else None,
             spec.date_to.date().isoformat() if spec.date_to else None, spec.notes.strip() or None),
        )
    account_id = cur.lastrowid
    case.log("INFO", "account_registered", {"email": spec.email, "evidence": spec.evidence_number,
                                             "server": f"{spec.server}:{spec.port}", "tls": tls,
                                             "notes": spec.notes.strip()})
    return account_id


def set_account_notes(case: Case, account_id: int, notes: str) -> bool:
    """Notiz eines Kontos ändern (alter und neuer Text werden protokolliert). True, wenn geändert."""
    if case.closed:
        raise RuntimeError(_t("Case ist abgeschlossen"))
    new = notes.strip() or None
    row = case.conn.execute("SELECT notes, email FROM account WHERE id = ?", (account_id,)).fetchone()
    if row is None or row[0] == new:
        return False
    with case.conn:
        case.conn.execute("UPDATE account SET notes = ? WHERE id = ?", (new, account_id))
    case.log("INFO", "account_notes_changed", {"account_id": account_id, "email": row[1], "old": row[0] or "",
                                                "new": new or ""})
    return True


def scan(case: Case, account_id: int, imap: ReadOnlyImap, cancel: CancelToken | None = None) -> list[dict]:
    """Liest Ordnerstruktur, Anzahl und Größe und speichert sie im Case."""
    rows = []
    for f in imap.list_folders():
        if cancel:
            cancel.raise_if_cancelled()
        if f.selectable:
            imap.status(f)
            imap.examine(f)
            f.size = sum(imap.sizes().values())
        with case.conn:
            case.conn.execute(
                "INSERT INTO folder (account_id, raw_name, display_name, delimiter, flags, namespace, uidvalidity, "
                "count_server, size_server, selected) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(account_id, raw_name) DO UPDATE SET uidvalidity=excluded.uidvalidity, "
                "count_server=excluded.count_server, size_server=excluded.size_server, flags=excluded.flags",
                (account_id, f.raw_name, f.name, f.delimiter, " ".join(f.flags), f.namespace,
                 f.uidvalidity, f.messages, f.size, 1 if f.selectable and f.namespace == "personal" else 0),
            )
        rows.append({"name": f.name, "messages": f.messages, "size": f.size, "selectable": f.selectable})
    case.log("INFO", "scan_done", {"account_id": account_id, "folders": len(rows),
                                   "messages": sum(r["messages"] for r in rows),
                                   "bytes": sum(r["size"] for r in rows)})
    is_gmail = "X-GM-EXT-1" in imap.capabilities()
    first = not case.conn.execute("SELECT 1 FROM attempt WHERE account_id = ? LIMIT 1", (account_id,)).fetchone()
    if is_gmail and first:
        names = gmail_recommended(case, account_id)
        if names:
            set_selection(case, account_id, set(names))
            case.log("INFO", "gmail_selection", {"account_id": account_id, "selected": names,
                                                 "reason": "gmail_all_mail"})
    return rows


GMAIL_SPECIAL = ("\\All", "\\Junk", "\\Trash")


def is_gmail_account(case: Case, account_id: int) -> bool:
    caps = case.conn.execute("SELECT capabilities FROM account WHERE id = ?", (account_id,)).fetchone()
    return bool(caps and caps[0] and "X-GM-EXT-1" in caps[0])


def gmail_recommended(case: Case, account_id: int) -> list[str]:
    """Gmail: „Alle Nachrichten“ + Spam + Papierkorb (über SPECIAL-USE, sprachunabhängig).

    „Alle Nachrichten“ enthält jede Mail genau einmal – außer Spam und Papierkorb. Leer, wenn der
    Server keine \\All-Kennung liefert."""
    rows = case.conn.execute("SELECT display_name, flags FROM folder WHERE account_id = ?", (account_id,)).fetchall()
    flags = {name: set((f or "").split()) for name, f in rows}
    if not any("\\All" in f for f in flags.values()):
        return []
    return sorted(name for name, f in flags.items() if f & set(GMAIL_SPECIAL))


def set_selection(case: Case, account_id: int, selected_names: set[str]) -> None:
    with case.conn:
        case.conn.execute("UPDATE folder SET selected = 0 WHERE account_id = ?", (account_id,))
        for name in selected_names:
            case.conn.execute("UPDATE folder SET selected = 1 WHERE account_id = ? AND display_name = ?",
                              (account_id, name))
    case.log("INFO", "selection_set", {"account_id": account_id, "selected": sorted(selected_names)})


def normalize_filters(filters: dict | None) -> dict[str, list[str]]:
    """Leere Werte entfernen, Komma-getrennte Eingaben aufteilen, nur bekannte Felder."""
    from fed.core.imap_reader import FILTER_KEYS

    out: dict[str, list[str]] = {}
    for key in FILTER_KEYS:
        raw = (filters or {}).get(key) or []
        values = raw.split(",") if isinstance(raw, str) else [v for x in raw for v in str(x).split(",")]
        values = [v.strip() for v in values if v.strip()]
        if values:
            out[key] = values
    return out


def set_content_filter(case: Case, account_id: int, filters: dict | None) -> dict[str, list[str]]:
    """Inhaltsfilter der Teilsicherung festlegen (protokolliert). Leer = ganze Ordner."""
    f = normalize_filters(filters)
    old = case.conn.execute("SELECT filters FROM account WHERE id = ?", (account_id,)).fetchone()
    if (json.loads(old[0]) if old and old[0] else {}) == f:
        return f
    with case.conn:
        case.conn.execute("UPDATE account SET filters = ? WHERE id = ?",
                          (json.dumps(f, ensure_ascii=False) if f else None, account_id))
    case.log("INFO", "content_filter_set", {"account_id": account_id, "filters": f})
    return f


def set_date_filter(case: Case, account_id: int, date_from: datetime | None, date_to: datetime | None) -> None:
    with case.conn:
        case.conn.execute("UPDATE account SET date_from = ?, date_to = ? WHERE id = ?",
                          (date_from.date().isoformat() if date_from else None,
                           date_to.date().isoformat() if date_to else None, account_id))
    case.log("INFO", "date_filter_set", {"account_id": account_id, "from": str(date_from), "to": str(date_to)})


# ---------------------------------------------------------------- Versuch
def run_attempt(
    case: Case,
    account_id: int,
    connect: Connector,
    mode: Mode = Mode.RESUME,
    cancel: CancelToken | None = None,
    progress: ProgressCallback | None = None,
    reason: str = "",
    delete_old: bool = False,
    cfg: Config | None = None,
) -> AttemptResult:
    cfg = cfg or Config()
    cancel = cancel or CancelToken()
    if case.closed:
        raise ValueError(_t("Case ist abgeschlossen – keine weiteren Versuche möglich"))
    conn = case.conn
    acc = conn.execute("SELECT email, date_from, date_to, filters FROM account WHERE id = ?", (account_id,)).fetchone()
    if not acc:
        raise ValueError(_t("Konto {id} unbekannt", id=account_id))
    email, date_from, date_to, filters_json = acc
    content_filter = json.loads(filters_json) if filters_json else None
    account_dir = case.work_dir / sanitize_component(email)

    prev_nr = conn.execute("SELECT COALESCE(MAX(nr), 0) FROM attempt WHERE account_id = ?", (account_id,)).fetchone()[0]
    nr = prev_nr + 1
    if mode is Mode.RESTART and not reason.strip():
        raise ValueError(_t("Für einen Neubeginn ist ein Grund Pflicht"))
    with conn:
        attempt_id = conn.execute(
            "INSERT INTO attempt (account_id, nr, mode, reason, started_utc, status, phase) VALUES (?,?,?,?,?,?,?)",
            (account_id, nr, mode.value, reason, utc_now(), "running", "start"),
        ).lastrowid
    case.log("INFO", "attempt_started", {"nr": nr, "mode": mode.value, "reason": reason}, attempt_id)
    result = AttemptResult(attempt_id, nr, "running")
    prog = Progress(phase="download")

    def set_phase(p: str) -> None:
        prog.phase = p
        with conn:
            conn.execute("UPDATE attempt SET phase = ? WHERE id = ?", (p, attempt_id))
        if progress:
            progress(prog)

    imap: ReadOnlyImap | None = None
    try:
        if mode is Mode.RESTART:
            set_phase("restart")
            _discard_previous(case, account_id, account_dir, nr, attempt_id, delete_old)
        else:
            set_phase("verify_existing")
            _verify_existing(case, account_id, attempt_id, cancel)

        imap = connect()
        folders = _selected_folders(case, account_id)
        set_phase("status_before")
        plan = []
        for row in folders:
            cancel.raise_if_cancelled()
            f = FolderInfo(row["raw_name"], row["display_name"], row["delimiter"], tuple((row["flags"] or "").split()))
            st = imap.status(f)
            if mode is Mode.RESUME and row["uidvalidity"] and st.get("UIDVALIDITY") != row["uidvalidity"]:
                has_saved = conn.execute("SELECT 1 FROM message WHERE folder_id = ? AND status = 'valid' LIMIT 1",
                                         (row["id"],)).fetchone()
                if has_saved:
                    raise ResumeNotPossible(_t("UIDVALIDITY von {folder} hat sich geändert – nur Neubeginn möglich", folder=f.name))
            with conn:
                conn.execute("UPDATE folder SET status_before = COALESCE(status_before, ?), uidvalidity = ? WHERE id = ?",
                             (json.dumps(st), st.get("UIDVALIDITY"), row["id"]))
            imap.examine(f)
            since = datetime.fromisoformat(date_from).replace(tzinfo=UTC) if date_from else None
            before = (datetime.fromisoformat(date_to) + timedelta(days=1)).replace(tzinfo=UTC) if date_to else None
            uids = imap.search_uids(since, before, content_filter)
            sizes = imap.sizes()
            done = {r[0] for r in conn.execute(
                "SELECT uid FROM message WHERE folder_id = ? AND status = 'valid'", (row["id"],))}
            todo = [u for u in uids if u not in done]
            result.expected += len(uids)
            result.skipped_existing += len(uids) - len(todo)
            plan.append((row, f, todo, {u: sizes.get(u, 0) for u in todo}))
            prog.mails_total += len(todo)
            prog.bytes_total += sum(sizes.get(u, 0) for u in todo)

        set_phase("download")
        imap.logout()  # Download läuft über eigene Verbindungen je Worker
        imap = None
        _download(case, attempt_id, plan, email, account_dir, connect, cfg, cancel, progress, prog, result)
        if cfg.programm.dkim_pruefen:
            set_phase("auth_check")
            auth_check(case, account_id, attempt_id, cancel, cfg.programm.socket_timeout_s)
        imap = connect()

        set_phase("status_after")
        for row in folders:
            f = FolderInfo(row["raw_name"], row["display_name"], row["delimiter"], ())
            st = imap.status(f)
            saved = conn.execute("SELECT COUNT(*) FROM message WHERE folder_id = ? AND status = 'valid'",
                                 (row["id"],)).fetchone()[0]
            before = json.loads(conn.execute("SELECT status_before FROM folder WHERE id = ?",
                                             (row["id"],)).fetchone()[0] or "{}")
            with conn:
                conn.execute("UPDATE folder SET status_after = ?, count_saved = ? WHERE id = ?",
                             (json.dumps(st), saved, row["id"]))
            if before and before != st:
                msg_ = f"{f.name}: Status vorher {before} != nachher {st}"
                result.warnings.append(msg_)
                case.log("WARNING", "status_changed", {"folder": f.name, "before": before, "after": st}, attempt_id)
        result.status = "done"
    except Cancelled:
        result.status = "cancelled"
        case.log("WARNING", "attempt_cancelled", {"phase": prog.phase, "saved": result.saved}, attempt_id)
    except ResumeNotPossible as e:
        result.status = "failed"
        case.log("ERROR", "resume_not_possible", str(e), attempt_id)
        raise
    except (ImapError, *NETWORK_ERRORS) as e:
        result.status = "failed"
        case.log("ERROR", "attempt_failed", {"phase": prog.phase, "error": str(e)}, attempt_id)
    except Exception as e:  # unerwartet (z. B. Datenträger voll): Versuch sauber als fehlgeschlagen beenden
        result.status = "failed"
        case.log("ERROR", "attempt_failed", {"phase": prog.phase, "error": f"{type(e).__name__}: {e}"}, attempt_id)
        raise
    finally:
        if imap is not None:
            imap.logout()
        elapsed = max(time.monotonic() - prog.started, 1e-6)
        with conn:
            conn.execute("UPDATE attempt SET ended_utc = ?, status = ?, avg_bytes_s = ? WHERE id = ?",
                         (utc_now(), result.status, prog.bytes_done / elapsed, attempt_id))
        _cleanup_part_files(account_dir)
        case.log("INFO", "attempt_ended", {"status": result.status, "saved": result.saved,
                                           "expected": result.expected}, attempt_id)
    return result


# ---------------------------------------------------------------- paralleler Download
BATCH_MAILS = 50  # Mails je FETCH-Befehl
BATCH_BYTES = 8 * 1024 * 1024  # höchstens so viele Bytes je FETCH-Befehl
TASK_MAILS = 300  # große Ordner werden in Pakete geteilt, damit alle Verbindungen ausgelastet sind


@dataclass
class _Task:
    row: dict
    folder: FolderInfo
    uids: list[int]
    sizes: dict[int, int]


def _tasks(plan) -> list[_Task]:
    tasks = []
    for row, f, todo, sizes in plan:
        for i in range(0, len(todo), TASK_MAILS):
            tasks.append(_Task(row, f, todo[i:i + TASK_MAILS], sizes))
    # größte Pakete zuerst: gleichmäßigere Auslastung
    tasks.sort(key=lambda t: -sum(t.sizes.get(u, 0) for u in t.uids))
    return tasks


def _batches(task: _Task, chunk: int) -> list[list[int]]:
    """Kleine Mails zu Stapeln bündeln; große (ab ``chunk``) einzeln und stückweise."""
    out, cur, cur_bytes = [], [], 0
    for u in task.uids:
        size = task.sizes.get(u, 0)
        if size > chunk:
            out.append([u])
            continue
        if cur and (len(cur) >= BATCH_MAILS or cur_bytes + size > BATCH_BYTES):
            out.append(cur)
            cur, cur_bytes = [], 0
        cur.append(u)
        cur_bytes += size
    if cur:
        out.append(cur)
    return out


def _download(case, attempt_id, plan, email, account_dir, connect, cfg, cancel, progress, prog, result) -> None:
    import queue
    import threading

    tasks = _tasks(plan)
    if not tasks:
        return
    n = max(1, min(cfg.programm.verbindungen, len(tasks)))
    chunk = cfg.programm.chunk_mb * 1024 * 1024
    q: queue.Queue[_Task] = queue.Queue()
    for t in tasks:
        q.put(t)
    lock = threading.Lock()
    errors: list[BaseException] = []
    active: dict[int, str] = {}
    started: list[int] = []
    case.log("INFO", "download_started", {"connections": n, "tasks": len(tasks), "mails": prog.mails_total},
             attempt_id)

    def report(msg, worker_id: int) -> None:
        with lock:
            result.saved += 1
            prog.mails_done += 1
            prog.bytes_done += msg.size_server
            prog.current_uid = msg.uid
            prog.folder = ", ".join(sorted(set(active.values())))
            prog.folder_done, prog.folder_total = prog.mails_done, prog.mails_total
            prog.sample()
            if progress:
                progress(prog)

    def worker(worker_id: int) -> None:
        imap = None
        current = None
        try:
            try:
                imap = connect()
            except Exception as e:  # weitere Verbindung vom Server abgelehnt -> mit weniger weiterarbeiten
                if worker_id == 0:
                    raise
                case.log("WARNING", "connection_refused", {"worker": worker_id + 1, "error": str(e)}, attempt_id)
                with lock:
                    prog.warnings += 1
                return
            with lock:
                started.append(worker_id)
            while not cancel.is_cancelled and not errors:
                try:
                    task = q.get_nowait()
                except queue.Empty:
                    return
                with lock:
                    active[worker_id] = task.folder.name
                for batch in _batches(task, chunk):
                    if cancel.is_cancelled or errors:
                        return
                    msgs = None
                    for attempt_conn in range(MAX_RECONNECTS + 1):
                        try:
                            if current != task.folder.raw_name:
                                imap.examine(task.folder)
                                current = task.folder.raw_name
                            if len(batch) == 1 and task.sizes.get(batch[0], 0) > chunk:
                                msgs = {batch[0]: imap.fetch(batch[0], chunk_bytes=chunk)}
                            else:
                                msgs = imap.fetch_many(batch)
                            break
                        except NETWORK_ERRORS as e:
                            if attempt_conn == MAX_RECONNECTS:
                                raise
                            case.log("WARNING", "reconnect", {"worker": worker_id + 1, "folder": task.folder.name,
                                                              "error": str(e), "try": attempt_conn + 1}, attempt_id)
                            with lock:
                                prog.warnings += 1
                            imap.logout()
                            time.sleep(min(2 ** attempt_conn, 10))
                            imap = connect()
                            current = None
                    for uid in batch:
                        if cancel.is_cancelled:
                            return
                        msg = msgs.get(uid)
                        if msg is None:
                            case.log("WARNING", "message_vanished", {"folder": task.folder.name, "uid": uid},
                                     attempt_id)
                            with lock:
                                prog.warnings += 1
                            continue
                        _store_message(case, attempt_id, task.row, task.folder, msg, email, account_dir, cfg,
                                       None, prog)
                        report(msg, worker_id)
                with lock:
                    active.pop(worker_id, None)
        except BaseException as e:  # an den Haupt-Thread weitergeben
            with lock:
                errors.append(e)
        finally:
            if imap is not None:
                imap.logout()

    threads = [threading.Thread(target=worker, args=(i,), name=f"fed-download-{i + 1}", daemon=True)
               for i in range(n)]
    for i, t in enumerate(threads):
        t.start()
        if i == 0:
            time.sleep(0.05)
    for t in threads:
        t.join()
    case.log("INFO", "download_connections", {"requested": n, "used": len(started)}, attempt_id)
    if errors:
        raise errors[0]
    cancel.raise_if_cancelled()


# ---------------------------------------------------------------- Hilfen
def _selected_folders(case: Case, account_id: int) -> list[dict]:
    cur = case.conn.execute(
        "SELECT id, raw_name, display_name, delimiter, flags, uidvalidity FROM folder "
        "WHERE account_id = ? AND selected = 1 AND flags NOT LIKE '%\\Noselect%' ORDER BY id", (account_id,))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur]


def _zip_time(msg, meta: mailmeta.MailMeta, cfg: Config) -> tuple[datetime, str]:
    if cfg.programm.zip_zeit_quelle == "date_header" and meta.date_header:
        try:
            return parsedate_to_datetime(meta.date_header), "date_header"
        except (TypeError, ValueError):
            pass
    return msg.internaldate, "internaldate"


def _store_message(case, attempt_id, row, f, msg, email, account_dir, cfg, result, prog) -> None:
    rel_dir = folder_to_zip_path(f.name, f.delimiter)
    name = eml_filename(msg.internaldate, msg.uid)
    target = account_dir / rel_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    hashes = hash_bytes(msg.raw, cfg.programm.hash_extra)
    part = target.with_suffix(".eml.part")
    with part.open("wb") as fh:
        fh.write(msg.raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(part, target)
    meta = mailmeta.parse(msg.raw)
    ztime, zsource = _zip_time(msg, meta, cfg)
    ts = ztime.timestamp()
    os.utime(target, (ts, ts))
    if not meta.date_header:
        prog.warnings += 1  # Zähler nur zur Anzeige
        case.log("WARNING", "no_date_header", {"folder": f.name, "uid": msg.uid}, attempt_id)
    zip_path = f"{sanitize_component(email)}/{rel_dir}/{name}"
    with case.conn:
        case.conn.execute(
            "INSERT INTO message (attempt_id, folder_id, uid, zip_path, sha256, md5, sha512, size, size_server, "
            "internaldate, date_header, zip_time_source, flags, gm_msgid, gm_labels, message_id, msg_from, msg_to, "
            "msg_cc, msg_bcc, delivered_to, recipient_note, attachment_count, inline_count, attachments, subject, "
            "subject_raw, saved_utc, gm_thrid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, row["id"], msg.uid, zip_path, hashes["sha256"], hashes.get("md5"), hashes.get("sha512"),
             len(msg.raw), msg.size_server, msg.internaldate.astimezone(UTC).isoformat(), meta.date_header,
             zsource, " ".join(msg.flags), msg.gm_msgid,
             json.dumps(msg.gm_labels, ensure_ascii=False) if msg.gm_labels else None,
             meta.message_id, json.dumps(meta.msg_from, ensure_ascii=False), json.dumps(meta.msg_to, ensure_ascii=False),
             json.dumps(meta.msg_cc, ensure_ascii=False), json.dumps(meta.msg_bcc, ensure_ascii=False),
             json.dumps(meta.delivered_to, ensure_ascii=False), meta.recipient_note, meta.attachment_count,
             meta.inline_count, json.dumps([a.__dict__ for a in meta.attachments], ensure_ascii=False),
             meta.subject, meta.subject_raw, utc_now(), msg.gm_thrid),
        )
    if result is not None:
        result.saved += 1


def _verify_existing(case: Case, account_id: int, attempt_id: int, cancel: CancelToken) -> None:
    """Hasht bereits gesicherte Dateien nach; fehlende/abweichende werden verworfen und neu geladen."""
    bad = 0
    rows = case.conn.execute(
        "SELECT m.id, m.zip_path, m.sha256 FROM message m JOIN folder f ON f.id = m.folder_id "
        "WHERE f.account_id = ? AND m.status = 'valid'", (account_id,)).fetchall()
    for mid, zip_path, sha in rows:
        cancel.raise_if_cancelled()
        p = case.work_dir / zip_path
        if not p.exists() or hash_file(p)["sha256"] != sha:
            bad += 1
            with case.conn:
                case.conn.execute("UPDATE message SET status = 'discarded' WHERE id = ?", (mid,))
            case.log("ERROR", "existing_file_invalid", {"path": zip_path, "exists": p.exists()}, attempt_id)
    case.log("INFO", "existing_verified", {"checked": len(rows), "invalid": bad}, attempt_id)


def _discard_previous(case: Case, account_id: int, account_dir: Path, nr: int, attempt_id: int,
                      delete_old: bool) -> None:
    rows = case.conn.execute(
        "SELECT m.zip_path, m.sha256 FROM message m JOIN folder f ON f.id = m.folder_id "
        "WHERE f.account_id = ? AND m.status = 'valid'", (account_id,)).fetchall()
    with case.conn:
        case.conn.execute(
            "UPDATE message SET status = 'discarded' WHERE status = 'valid' AND folder_id IN "
            "(SELECT id FROM folder WHERE account_id = ?)", (account_id,))
        case.conn.execute("UPDATE folder SET status_before = NULL, status_after = NULL, count_saved = 0 "
                          "WHERE account_id = ?", (account_id,))
    if not account_dir.exists():
        case.log("INFO", "restart_nothing_to_discard", "", attempt_id)
        return
    if delete_old:
        shutil.rmtree(account_dir)
        case.log("WARNING", "previous_deleted", {"files": len(rows), "sha256": [r[1] for r in rows]}, attempt_id)
        with case.conn:
            case.conn.execute("UPDATE attempt SET deleted = 1 WHERE id = ?", (attempt_id,))
    else:
        dest = case.discarded_dir / f"attempt_{nr - 1}" / account_dir.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(account_dir), str(dest))
        rel = str(dest.relative_to(case.root))
        with case.conn:
            case.conn.execute("UPDATE attempt SET moved_to = ? WHERE id = ?", (rel, attempt_id))
        case.log("INFO", "previous_moved", {"to": rel, "files": len(rows)}, attempt_id)


def _cleanup_part_files(account_dir: Path) -> None:
    if account_dir.exists():
        for p in account_dir.rglob("*.eml.part"):
            p.unlink(missing_ok=True)


def summary(case: Case, account_id: int) -> dict:
    """Soll/Ist je Ordner für Anzeige und Bericht."""
    rows = case.conn.execute(
        "SELECT display_name, selected, count_server, count_saved FROM folder WHERE account_id = ? ORDER BY id",
        (account_id,)).fetchall()
    return {"folders": [{"name": r[0], "selected": bool(r[1]), "server": r[2], "saved": r[3]} for r in rows],
            "saved_total": sum(r[3] or 0 for r in rows if r[1])}


def auth_check(case: Case, account_id: int, attempt_id: int | None = None, cancel: CancelToken | None = None,
               timeout: float = 5.0) -> dict[str, int]:
    """DKIM/Authentication-Results für alle noch nicht (oder nur vorläufig) geprüften Mails eines Kontos.

    Liest die gesicherten Dateien (unverändert), prüft parallel, schreibt das Ergebnis je Mail und
    speichert die abgefragten DNS-Schlüssel. Abbruch jederzeit möglich (Rest wird beim Fortsetzen geprüft).
    """
    from concurrent.futures import ThreadPoolExecutor

    from fed.core import authcheck

    cancel = cancel or CancelToken()
    rows = case.conn.execute(
        "SELECT m.id, m.zip_path FROM message m JOIN folder f ON f.id = m.folder_id WHERE f.account_id = ? "
        "AND m.status = 'valid' AND (m.dkim_result IS NULL OR m.dkim_result = 'temperror') ORDER BY m.id",
        (account_id,)).fetchall()
    if not rows:
        return {}
    dns = authcheck.DnsCache(timeout=min(timeout, 10))

    def one(row):
        if cancel.is_cancelled:
            return row[0], None
        p = case.work_dir / row[1]
        return row[0], authcheck.check(p.read_bytes(), dns) if p.exists() else None

    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, rows))
    conn = case.conn
    with conn:
        for mid, r in results:
            if r is None:
                continue
            counts[r.dkim] = counts.get(r.dkim, 0) + 1
            conn.execute("UPDATE message SET dkim_result = ?, dkim_domain = ?, dkim_selector = ?, dkim_detail = ?, "
                         "auth_results = ? WHERE id = ?",
                         (r.dkim, r.dkim_domain or None, r.dkim_selector or None, r.dkim_detail or None,
                          json.dumps(r.provider, ensure_ascii=False) if r.provider else None, mid))
        for rec in dns.records.values():
            known = conn.execute("SELECT 1 FROM dns_record WHERE name = ? AND COALESCE(sha256, '') = ? AND status = ?",
                                 (rec.name, rec.sha256 or "", rec.status)).fetchone()
            if not known:
                conn.execute("INSERT INTO dns_record (name, record, sha256, status, error, fetched_utc, attempt_id) "
                             "VALUES (?,?,?,?,?,?,?)", (rec.name, rec.record, rec.sha256, rec.status,
                                                        rec.error or None, rec.fetched_utc, attempt_id))
    case.log("INFO", "auth_check_done", {"account_id": account_id, "checked": sum(counts.values()), **counts,
                                          "dns_records": len(dns.records)}, attempt_id)
    cancel.raise_if_cancelled()
    return counts


def accounts_overview(case: Case) -> list[dict]:
    """Je Konto im Case: Stand der Sicherung für die Übersicht.

    ``state``: ``new`` (angelegt, noch kein Versuch) | ``running`` | ``done`` | ``partial``
    (fertig, aber weniger gesichert als auf dem Server) | ``cancelled`` | ``failed``.
    """
    out = []
    for acc_id, email, evidence, server, auth, dfrom, dto, notes, filters in case.conn.execute(
            "SELECT id, email, evidence_number, server, auth_method, date_from, date_to, notes, filters FROM account "
            "ORDER BY id"):
        attempts = case.conn.execute("SELECT nr, status, started_utc, ended_utc FROM attempt WHERE account_id = ? "
                                     "ORDER BY nr", (acc_id,)).fetchall()
        folders = case.conn.execute("SELECT selected, count_server, status_after FROM folder WHERE account_id = ?",
                                    (acc_id,)).fetchall()
        sel, total = sum(1 for f in folders if f[0]), len(folders)
        # erwartet: Stand des Servers nach der letzten Sicherung (STATUS), sonst Stand beim Scan
        expected = sum((json.loads(f[2]).get("MESSAGES", f[1] or 0) if f[2] else f[1] or 0)
                       for f in folders if f[0])
        saved, size = case.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(m.size), 0) FROM message m JOIN folder f ON f.id = m.folder_id "
            "WHERE f.account_id = ? AND m.status = 'valid'",
            (acc_id,)).fetchone()
        last = attempts[-1] if attempts else None
        state = last[1] if last else "new"
        if state == "done" and not (dfrom or dto or filters) and saved < expected:
            state = "partial"
        out.append({"id": acc_id, "email": email, "evidence": evidence, "server": server, "auth": auth,
                    "attempts": len(attempts), "state": state, "saved": saved, "expected": expected,
                    "bytes": size, "folders_selected": sel, "folders_total": total,
                    "date_filter": bool(dfrom or dto or filters), "last_utc": (last[3] or last[2]) if last else None,
                    "notes": notes or ""})
    return out


__all__ = ["AccountSpec", "AttemptResult", "Mode", "ResumeNotPossible", "accounts_overview", "register_account",
           "run_attempt", "scan", "set_account_notes", "set_selection", "summary"]

