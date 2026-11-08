"""Nur lesender Zugriff auf gesicherte Mails für den Betrachter (ohne Qt).

Die Mail wird aus ``work/`` gelesen oder – nach dem Löschen des Arbeitsordners – direkt aus dem
ZIP. Vor der Anzeige wird ihr SHA-256 gegen die Case-Datei geprüft. Nichts wird verändert;
Ansehen und Speichern einer Anlage werden protokolliert.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from fed.core.case import Case


@dataclass
class Attachment:
    index: int
    name: str
    mime: str
    size: int
    sha256: str
    inline: bool
    content_id: str = ""  # ohne < >; für cid:-Verweise im HTML


@dataclass
class Hop:
    nr: int  # 1 = ältester Eintrag (Absender-Seite)
    from_: str
    by: str
    with_: str
    time: datetime | None
    delay_s: float | None  # gegenüber der vorherigen Station
    raw: str


@dataclass
class MailView:
    message_id: int
    source: str  # "work" | ZIP-Dateiname
    sha256_ok: bool
    raw: bytes
    headers: list[tuple[str, str]]
    subject: str
    from_: str
    to: str
    cc: str
    date: str
    text: str
    html: str
    attachments: list[Attachment] = field(default_factory=list)
    hops: list[Hop] = field(default_factory=list)
    db: dict = field(default_factory=dict)  # Zeile aus der Case-Datei (UID, Ordner, DKIM, Labels …)


def _row(case: Case, message_id: int) -> dict:
    cur = case.conn.execute(
        "SELECT m.*, f.display_name AS folder, a.email AS account, a.evidence_number AS evidence, f.account_id "
        "FROM message m JOIN folder f ON f.id = m.folder_id JOIN account a ON a.id = f.account_id WHERE m.id = ?",
        (message_id,))
    r = cur.fetchone()
    if r is None:
        raise KeyError(f"Mail {message_id} nicht in der Case-Datei")
    return dict(zip([c[0] for c in cur.description], r, strict=True))


def _zip_candidates(case: Case, account_id: int) -> list[Path]:
    rows = case.conn.execute(
        "SELECT file_name FROM output WHERE file_name LIKE '%.zip' AND file_name NOT LIKE '%\\_encrypted.zip' ESCAPE '\\' "
        "AND (account_id IS NULL OR account_id = ?) ORDER BY id DESC", (account_id,)).fetchall()
    return [case.root / r[0] for r in rows if (case.root / r[0]).exists()]


def load_raw(case: Case, message_id: int) -> tuple[bytes, str, dict]:
    """Bytes der Mail, Quelle (``work`` oder ZIP-Name) und Datenbankzeile."""
    row = _row(case, message_id)
    p = case.work_dir / row["zip_path"]
    if p.exists():
        return p.read_bytes(), "work", row
    for z in _zip_candidates(case, row["account_id"]):
        with zipfile.ZipFile(z) as zf:
            try:
                return zf.read(row["zip_path"]), z.name, row
            except KeyError:
                continue
    raise FileNotFoundError(f"{row['zip_path']} weder in work/ noch in einem ZIP gefunden")




def parse_received(values: list[str]) -> list[Hop]:
    """``Received``-Kopfzeilen (oben = neueste) als Stationen vom Absender zum Empfänger."""
    hops: list[Hop] = []
    prev: datetime | None = None
    for nr, raw in enumerate(reversed(values), start=1):
        v = re.sub(r"\s+", " ", raw).strip()
        head, _, when = v.rpartition(";")
        t = None
        if when:
            try:
                t = parsedate_to_datetime(when.strip())
            except (TypeError, ValueError, IndexError):
                t = None
        m = re.search(r"\bfrom\s+(.+?)(?=\s+by\s|\s+with\s|\s+id\s|\s+for\s|$)", head, re.I)
        b = re.search(r"\bby\s+(.+?)(?=\s+with\s|\s+id\s|\s+for\s|\s+via\s|$)", head, re.I)
        w = re.search(r"\bwith\s+(\S+)", head, re.I)
        delay = (t - prev).total_seconds() if t and prev and t.tzinfo and prev.tzinfo else None
        hops.append(Hop(nr, m.group(1) if m else "", b.group(1) if b else "", w.group(1) if w else "", t, delay, v))
        prev = t or prev
    return hops


def _decode(part: EmailMessage) -> str:
    try:
        return part.get_content()
    except (LookupError, KeyError, ValueError, AssertionError):
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "latin-1", "replace")


def parse(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = [(k, str(v)) for k, v in msg.items()]
    text = html = ""
    body = msg.get_body(preferencelist=("plain",))
    if body is not None:
        text = _decode(body)
    hbody = msg.get_body(preferencelist=("html",))
    if hbody is not None:
        html = _decode(hbody)
    attachments = []
    for i, part in enumerate(msg.walk()):
        if part.is_multipart() or part in (body, hbody):
            continue
        disp = part.get_content_disposition()
        name = part.get_filename()
        cid = str(part.get("Content-ID", "")).strip().strip("<>").strip()
        if disp is None and not name and not cid:
            continue
        data = part.get_payload(decode=True) or b""
        attachments.append(Attachment(i, name or f"teil-{i}", part.get_content_type(), len(data),
                                      hashlib.sha256(data).hexdigest(), disp == "inline" or bool(cid), cid))
    return {
        "headers": headers, "subject": str(msg.get("Subject", "")), "from_": str(msg.get("From", "")),
        "to": str(msg.get("To", "")), "cc": str(msg.get("Cc", "")), "date": str(msg.get("Date", "")),
        "text": text, "html": html, "attachments": attachments,
        "hops": parse_received([str(v) for v in msg.get_all("Received", [])]),
    }


def open_message(case: Case, message_id: int, log: bool = True) -> MailView:
    raw, source, row = load_raw(case, message_id)
    ok = hashlib.sha256(raw).hexdigest() == row["sha256"]
    if log:
        case.log("INFO" if ok else "ERROR", "message_viewed",
                 {"message_id": message_id, "file": row["zip_path"], "source": source, "sha256_ok": ok})
    return MailView(message_id, source, ok, raw, db=row, **parse(raw))


def _part(view: MailView, index: int) -> EmailMessage:
    msg = BytesParser(policy=policy.default).parsebytes(view.raw)
    return next(p for i, p in enumerate(msg.walk()) if i == index)


def attachment_data(view: MailView, index: int) -> bytes:
    """Dekodierter Inhalt einer Anlage (nur im Speicher)."""
    return _part(view, index).get_payload(decode=True) or b""


def inline_resource(view: MailView, ref: str) -> bytes | None:
    """Inhalt eines eingebetteten Teils zu einem ``cid:``-Verweis (nur aus der Mail selbst)."""
    from urllib.parse import unquote

    ref = unquote(ref.removeprefix("cid:")).strip().strip("<>")
    if not ref:
        return None
    for a in view.attachments:
        if a.content_id and a.content_id == ref:
            return attachment_data(view, a.index)
    return None


def check_attachment(case: Case, view: MailView, index: int, log: bool = True):
    """Anlage im Speicher prüfen (Format, Endung, MIME, Risiken, Hashes); Vorschau wird protokolliert.

    Rückgabe: (Bytes, FileCheck). Der SHA-256 wird mit dem Wert beim Öffnen der Mail verglichen."""
    from fed.core.filecheck import check

    a = next(x for x in view.attachments if x.index == index)
    data = attachment_data(view, index)
    fc = check(a.name, data, a.mime)
    if log:
        case.log("WARNING" if fc.warnings else "INFO", "attachment_previewed",
                 {"message_id": view.message_id, "file": view.db["zip_path"], **fc.summary(),
                  "sha256_ok": fc.sha256 == a.sha256})
    return data, fc


def export_attachment(case: Case, view: MailView, index: int, target: Path) -> str:
    """Anlage unverändert speichern; SHA-256 wird geprüft und protokolliert. Rückgabe: SHA-256."""
    part = _part(view, index)
    data = part.get_payload(decode=True) or b""
    target.write_bytes(data)
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    case.log("INFO", "attachment_exported", {"message_id": view.message_id, "file": view.db["zip_path"],
                                              "attachment": part.get_filename() or f"teil-{index}",
                                              "target": str(target), "sha256": sha, "size": len(data)})
    return sha


def auth_summary(view: MailView) -> dict:
    """DKIM-Ergebnis der Sicherung und Authentication-Results des Anbieters."""
    r = view.db
    return {"dkim": r.get("dkim_result"), "domain": r.get("dkim_domain"), "selector": r.get("dkim_selector"),
            "detail": r.get("dkim_detail"), "provider": json.loads(r["auth_results"]) if r.get("auth_results") else {}}
