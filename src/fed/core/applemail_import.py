"""Apple Mail offline in einen Fall einlesen – erzeugt dieselbe Struktur wie eine IMAP-Sicherung.

Nutzt den Reader aus :mod:`fed.core.applemail` und schreibt je Konto ein **Asservat** (Account), seine
Ordner und die Mails als ``.eml`` in ``work/<adresse>/<ordner>/…`` mit Hash in die Case-Datei – so,
dass :mod:`fed.core.finalize` daraus wie gewohnt das ZIP mit Bericht baut.

Herkunft und Vollständigkeit werden festgehalten: ``message.source = 'apple_mail'`` und
``message.partial = 1`` für ``*.partial.emlx`` (unvollständig geladen; Anhänge ggf. nur auf dem
Server – später online nachholbar).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from fed.core import applemail, mailmeta
from fed.core.case import Case, utc_now
from fed.core.hashing import hash_bytes
from fed.core.msg import t
from fed.core.naming import eml_filename, folder_to_zip_path, sanitize_component

IMPORT_SOURCE = "apple_mail"
_DELIM = "/"


@dataclass
class AccountChoice:
    """Was die Oberfläche je Konto zur Bestätigung zeigt (Adresse ist vorbelegt, aber änderbar)."""

    account: applemail.AppleAccount
    address: str
    address_source: str  # accounts_db (maßgeblich) | headers (zu bestätigen) | "" (unbekannt)
    name: str
    host: str
    folders: int
    messages: int
    candidates: list[tuple[str, int]]

    @property
    def uuid(self) -> str:
        return self.account.uuid


@dataclass
class ImportResult:
    account_id: int
    saved: int = 0
    partial: int = 0
    folders: int = 0
    bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def scan(v10_dir: Path, accounts_db: Path | None = None) -> list[AccountChoice]:
    """Liest die Konten eines ``V#``-Verzeichnisses und schlägt je Konto eine Adresse vor.

    ``accounts_db`` (``Accounts4.sqlite``) liefert die maßgebliche Adresse; fehlt sie, wird sie
    nicht erraten, sondern aus den Kopfzeilen vorgeschlagen und muss bestätigt werden.
    """
    mapping = applemail.accounts_map(accounts_db) if accounts_db else {}
    out: list[AccountChoice] = []
    for acc in applemail.find_accounts(v10_dir):
        address, source, candidates = applemail.resolve_address(acc, mapping)
        info = mapping.get(acc.uuid)
        out.append(AccountChoice(acc, address, source, info.name if info else "", info.host if info else "",
                                 len(acc.folders), acc.message_count(), candidates))
    return out


def _message_time(meta: mailmeta.MailMeta, apple_meta: dict) -> tuple[datetime, str]:
    """Zeit der Mail: Apples Eingangszeit, sonst der ``Date``-Header, sonst jetzt."""
    received = applemail.AppleMessage.date_received(apple_meta)
    if received is not None:
        return received, "apple_received"
    if meta.date_header:
        try:
            dt = parsedate_to_datetime(meta.date_header)
            if dt is not None:
                return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)), "date_header"
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC), "import_time"


def import_account(case: Case, account: applemail.AppleAccount, email: str, evidence_number: str, *,
                   host: str = "", name: str = "", notes: str = "") -> ImportResult:
    """Legt ein Asservat aus einem Apple-Mail-Konto an und liest alle Ordner/Mails ein."""
    email = email.strip()
    if not email:
        raise ValueError(t("Für das Konto {uuid} ist keine Adresse angegeben.", uuid=account.uuid))
    with case.conn:
        cur = case.conn.execute(
            "INSERT INTO account (email, evidence_number, server, port, security, auth_method, username, "
            "autoconfig_source, notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (email, evidence_number, host or t("(Apple-Mail-Import)"), 0, "import", "import", email,
             IMPORT_SOURCE, notes.strip() or None))
    account_id = cur.lastrowid
    case.log("INFO", "account_registered", {"email": email, "evidence": evidence_number,
                                            "source": IMPORT_SOURCE, "apple_uuid": account.uuid,
                                            "apple_name": name, "host": host})
    with case.conn:
        attempt_id = case.conn.execute(
            "INSERT INTO attempt (account_id, nr, mode, reason, started_utc, status, phase) VALUES (?,?,?,?,?,?,?)",
            (account_id, 1, "import", IMPORT_SOURCE, utc_now(), "running", "import")).lastrowid
    case.mark_live(attempt_id)
    case.log("INFO", "attempt_started", {"nr": 1, "mode": "import", "reason": IMPORT_SOURCE}, attempt_id)

    res = ImportResult(account_id)
    account_dir = case.work_dir / sanitize_component(email)
    for folder in account.folders:
        messages = folder.messages()
        if not messages:
            continue
        rel_dir = folder_to_zip_path(folder.path, _DELIM)
        with case.conn:
            folder_id = case.conn.execute(
                "INSERT INTO folder (account_id, raw_name, display_name, delimiter, namespace, count_server, selected) "
                "VALUES (?,?,?,?,?,?,1) ON CONFLICT(account_id, raw_name) DO UPDATE SET count_server=excluded.count_server",
                (account_id, folder.path, folder.path, _DELIM, "personal", len(messages))).lastrowid
        saved_here = 0
        for idx, msg in enumerate(messages):
            try:
                raw, apple_meta = msg.read()
            except applemail.EmlxError as e:
                res.warnings.append(str(e))
                case.log("WARNING", "emlx_unreadable", {"folder": folder.path, "file": msg.path.name,
                                                        "error": str(e)}, attempt_id)
                continue
            uid = msg.number if msg.number >= 0 else 10_000_000 + idx
            _store(case, attempt_id, folder_id, rel_dir, account_dir, email, uid, raw, apple_meta, msg.partial)
            res.saved += 1
            res.bytes += len(raw)
            res.partial += int(msg.partial)
            saved_here += 1
        with case.conn:
            case.conn.execute("UPDATE folder SET count_saved=? WHERE id=?", (saved_here, folder_id))
        res.folders += 1

    with case.conn:
        case.conn.execute("UPDATE attempt SET status='done', phase='done', ended_utc=? WHERE id=?",
                          (utc_now(), attempt_id))
    case.log("INFO", "attempt_ended", {"status": "done", "saved": res.saved, "partial": res.partial,
                                       "folders": res.folders, "source": IMPORT_SOURCE}, attempt_id)
    return res


def _store(case: Case, attempt_id: int, folder_id: int, rel_dir: str, account_dir: Path, email: str,
           uid: int, raw: bytes, apple_meta: dict, partial: bool) -> None:
    import json

    meta = mailmeta.parse(raw)
    when, zsource = _message_time(meta, apple_meta)
    name = eml_filename(when, uid)
    target = account_dir / rel_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".eml.part")
    with part.open("wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(part, target)
    ts = when.timestamp()
    os.utime(target, (ts, ts))
    hashes = hash_bytes(raw)
    flags = " ".join(f for f, on in (("\\Seen", applemail.AppleMessage.seen(apple_meta)),
                                     ("\\Flagged", applemail.AppleMessage.flagged(apple_meta))) if on)
    zip_path = f"{sanitize_component(email)}/{rel_dir}/{name}"
    with case.conn:
        case.conn.execute(
            "INSERT INTO message (attempt_id, folder_id, uid, zip_path, sha256, md5, sha512, size, "
            "internaldate, date_header, zip_time_source, flags, message_id, msg_from, msg_to, msg_cc, msg_bcc, "
            "delivered_to, recipient_note, attachment_count, inline_count, attachments, subject, subject_raw, "
            "saved_utc, source, partial) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (attempt_id, folder_id, uid, zip_path, hashes["sha256"], hashes.get("md5"), hashes.get("sha512"),
             len(raw), when.astimezone(UTC).isoformat(), meta.date_header, zsource, flags, meta.message_id,
             json.dumps(meta.msg_from, ensure_ascii=False), json.dumps(meta.msg_to, ensure_ascii=False),
             json.dumps(meta.msg_cc, ensure_ascii=False), json.dumps(meta.msg_bcc, ensure_ascii=False),
             json.dumps(meta.delivered_to, ensure_ascii=False), meta.recipient_note, meta.attachment_count,
             meta.inline_count, json.dumps([a.__dict__ for a in meta.attachments], ensure_ascii=False),
             meta.subject, meta.subject_raw, utc_now(), IMPORT_SOURCE, int(partial)))
