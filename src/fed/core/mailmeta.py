"""Metadaten aus einer Mail lesen (Absender, Empfänger, Anlagen) – ohne sie zu verändern.

Die Rohdaten werden nur gelesen; Hash und .eml bleiben unberührt.

Empfänger-Regeln:
- To, CC und BCC werden getrennt gespeichert.
- BCC steht nur in Kopien, die der Absender selbst gespeichert hat (z. B. „Gesendet“).
- Fehlen To und CC, wurde die Mail meist per BCC zugestellt. Dann werden
  ``Delivered-To``, ``X-Original-To`` bzw. ``Envelope-To`` ausgewertet und ein
  Hinweis (``recipient_note``) gesetzt.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses

from fed.core.msg import code, t

DELIVERY_HEADERS = ("Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To")
UNDISCLOSED = ("undisclosed-recipients", "undisclosed recipients", "unbekannte empfänger")


@dataclass
class Attachment:
    name: str
    mime: str
    size: int
    sha256: str


@dataclass
class MailMeta:
    subject: str = ""
    subject_raw: str = ""
    msg_from: list[str] = field(default_factory=list)
    msg_to: list[str] = field(default_factory=list)
    msg_cc: list[str] = field(default_factory=list)
    msg_bcc: list[str] = field(default_factory=list)
    delivered_to: list[str] = field(default_factory=list)
    recipient_note: str = ""
    message_id: str = ""
    date_header: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    inline_count: int = 0

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)

    def display_recipient(self) -> str:
        """Kurztext für die Spalte „Empfänger“ in Historie und log.csv."""
        if self.msg_to:
            extra = f" +{len(self.msg_cc)} CC" if self.msg_cc else ""
            return self.msg_to[0] + (f" +{len(self.msg_to) - 1}" if len(self.msg_to) > 1 else "") + extra
        if self.msg_cc:
            return f"CC: {self.msg_cc[0]}" + (f" +{len(self.msg_cc) - 1}" if len(self.msg_cc) > 1 else "")
        if self.msg_bcc:
            return t("nur BCC: {n} Empfänger", n=len(self.msg_bcc))
        if self.delivered_to:
            return t("kein To/CC – Delivered-To: {addr}", addr=self.delivered_to[0])
        return t("kein Empfänger im Header")


def _addresses(msg: EmailMessage, name: str) -> list[str]:
    values = [str(v) for v in msg.get_all(name, [])]
    result = []
    for display, addr in getaddresses(values):
        if addr:
            result.append(addr if not display else f"{display} <{addr}>")
    return result


def parse(raw: bytes) -> MailMeta:
    msg: EmailMessage = BytesParser(policy=policy.default).parsebytes(raw)
    raw_headers = BytesParser(policy=policy.compat32).parsebytes(raw, headersonly=True)
    meta = MailMeta(
        subject=str(msg.get("Subject", "") or ""),
        subject_raw=str(raw_headers.get("Subject", "") or ""),
        msg_from=_addresses(msg, "From"),
        msg_to=_addresses(msg, "To"),
        msg_cc=_addresses(msg, "Cc"),
        msg_bcc=_addresses(msg, "Bcc"),
        message_id=str(msg.get("Message-ID", "") or "").strip(),
        date_header=str(msg.get("Date", "") or ""),
    )
    for h in DELIVERY_HEADERS:
        for v in msg.get_all(h, []):
            addr = str(v).strip().strip("<>")
            if addr and addr not in meta.delivered_to:
                meta.delivered_to.append(addr)

    to_raw = " ".join(str(v) for v in msg.get_all("To", [])).lower()
    if not meta.msg_to and not meta.msg_cc:
        if meta.msg_bcc:
            meta.recipient_note = code("bcc_only")
        elif any(u in to_raw for u in UNDISCLOSED):
            meta.recipient_note = code("undisclosed-recipients")
        else:
            meta.recipient_note = code("no_to_cc")

    for part in msg.walk():
        if part is msg or (part.is_multipart() and part.get_content_type() != "message/rfc822"):
            continue
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        ctype = part.get_content_type()
        if disposition == "inline" and (part.get("Content-ID") or ctype.startswith("image/")) and not filename:
            meta.inline_count += 1
            continue
        if disposition == "inline" and part.get("Content-ID"):
            meta.inline_count += 1
            continue
        if disposition == "attachment" or filename or ctype == "message/rfc822":
            payload = part.get_payload(decode=True)
            if payload is None and ctype == "message/rfc822":
                payload = part.as_bytes()
            payload = payload or b""
            meta.attachments.append(
                Attachment(
                    name=filename or ("attached_message.eml" if ctype == "message/rfc822" else "(unnamed)"),
                    mime=ctype,
                    size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
            )
    return meta
