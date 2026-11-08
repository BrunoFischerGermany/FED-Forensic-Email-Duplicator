"""Erzeugt das feste Test-/Validierungs-Postfach als Maildir (für Dovecot).

Alle Mails sind deterministisch (feste Inhalte, feste Eingangszeiten), daher
sind ihre SHA-256 bekannt und stehen in ``expected_hashes.csv``.

    python validation/testmailbox.py <maildir>          # Maildir erzeugen
    python validation/testmailbox.py --expected         # expected_hashes.csv neu schreiben
"""

from __future__ import annotations

import base64
import csv
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
USER = "test@example.de"
PASSWORD = "geheim123"
# zweites Konto mit Umlauten in Adresse, Domain (IDN) und Passwort – gleiches Postfach
UMLAUT_USER = "bärbel.böhmer@ärzte.de"
UMLAUT_PASSWORD = "Grüße-123"
SEPARATOR = "/"


@dataclass(frozen=True)
class TestMail:
    folder: str
    received: datetime
    raw: bytes
    seen: bool = False
    note: str = ""


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def _dt(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


def _b64(data: bytes) -> str:
    s = base64.b64encode(data).decode()
    return "\n".join(s[i : i + 76] for i in range(0, len(s), 76))


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000")
BIG = hashlib.sha256(b"fed").digest() * (3 * 1024 * 1024 // 32)  # 3 MiB, deterministisch


def mails() -> list[TestMail]:
    m: list[TestMail] = []
    m.append(TestMail("INBOX", _dt(2026, 11, 5, 7, 12, 44), _crlf(
        "From: Buchhaltung <buchhaltung@lieferant.example>\nTo: Max Mustermann <test@example.de>\n"
        "Subject: Rechnung 2026-1187\nDate: Thu, 05 Nov 2026 08:12:40 +0100\n"
        "Message-ID: <r1187@lieferant.example>\nMIME-Version: 1.0\n"
        "Content-Type: multipart/mixed; boundary=\"b1\"\n\n"
        "--b1\nContent-Type: text/plain; charset=utf-8\n\nAnbei die Rechnung.\n"
        f"--b1\nContent-Type: application/pdf; name=\"Rechnung.pdf\"\nContent-Disposition: attachment; filename=\"Rechnung.pdf\"\n"
        f"Content-Transfer-Encoding: base64\n\n{_b64(PDF)}\n--b1--\n"), seen=True, note="Anlage PDF, gelesen"))
    m.append(TestMail("INBOX", _dt(2026, 11, 4, 15, 3, 9), _crlf(
        "From: j.beispiel@example.org\nTo: test@example.de\nCc: a@example.org, b@example.org\n"
        "Subject: =?UTF-8?Q?AW:_Termin_Donnerstag_=C3=BCbermorgen?=\nDate: Wed, 04 Nov 2026 16:03:00 +0100\n"
        "Message-ID: <t2@example.org>\nContent-Type: text/plain; charset=utf-8\n\nPasst gut. Grüße\n"), note="Umlaut im Betreff, 2 CC"))
    m.append(TestMail("INBOX", _dt(2026, 11, 3, 9, 0, 0), b"From: alt@example.org\r\nTo: test@example.de\r\n"
        b"Subject: Latin-1 Rohdaten\r\nDate: Tue, 03 Nov 2026 10:00:00 +0100\r\n"
        b"Content-Type: text/plain; charset=iso-8859-1\r\nContent-Transfer-Encoding: 8bit\r\n\r\n"
        b"Gr\xfc\xdfe aus K\xf6ln\r\n", note="8bit Latin-1, Bytes muessen unveraendert bleiben"))
    m.append(TestMail("INBOX", _dt(2026, 11, 2, 12, 30, 0), _crlf(
        "From: news@example.org\nTo: test@example.de\nSubject: Ohne Datum\n"
        "Content-Type: text/plain\n\nDiese Mail hat keinen Date-Header.\n"), note="ohne Date-Header"))
    m.append(TestMail("INBOX", _dt(2026, 11, 1, 18, 0, 0), _crlf(
        "From: design@example.org\nTo: test@example.de\nSubject: Signatur mit Bild\nDate: Sun, 01 Nov 2026 19:00:00 +0100\n"
        "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"m\"\n\n"
        "--m\nContent-Type: multipart/related; boundary=\"r\"\n\n"
        "--r\nContent-Type: text/html; charset=utf-8\n\n<p>Hallo <img src=\"cid:logo1\"></p>\n"
        f"--r\nContent-Type: image/png\nContent-ID: <logo1>\nContent-Disposition: inline\nContent-Transfer-Encoding: base64\n\n{_b64(PNG)}\n--r--\n"
        f"--m\nContent-Type: text/csv; name=\"daten.csv\"\nContent-Disposition: attachment; filename=\"daten.csv\"\n\na;b\n1;2\n--m--\n"),
        note="1 Anlage + 1 inline"))
    m.append(TestMail("INBOX/Rechnungen", _dt(2026, 10, 30, 8, 47, 21), _crlf(
        "From: noreply@shop.example\nTo: test@example.de\nSubject: Ihre Bestellung 55-0192\n"
        "Date: Fri, 30 Oct 2026 09:47:00 +0100\nContent-Type: text/plain\n\nDanke.\n"), seen=True))
    m.append(TestMail("Projekte/2025", _dt(2025, 12, 1, 10, 0, 0), _crlf(
        "From: chef@example.de\nTo: test@example.de\nSubject: Projekt 2025\nDate: Mon, 01 Dec 2025 11:00:00 +0100\n\nText\n")))
    m.append(TestMail("Projekte/2026", _dt(2026, 2, 1, 10, 0, 0), _crlf(
        "From: chef@example.de\nTo: test@example.de\nSubject: Projekt 2026\nDate: Sun, 01 Feb 2026 11:00:00 +0100\n\nText\n")))
    m.append(TestMail("Entwürfe", _dt(2026, 11, 6, 9, 0, 0), _crlf(
        "From: test@example.de\nTo: j.beispiel@example.org\nSubject: Entwurf\n\nnoch nicht fertig\n"), seen=True, note="Umlaut im Ordner"))
    m.append(TestMail("Gesendet", _dt(2026, 10, 21, 10, 2, 17), _crlf(
        "From: test@example.de\nBcc: x1@example.org, x2@example.org, x3@example.org, x4@example.org\n"
        "Subject: Protokoll Besprechung\nDate: Wed, 21 Oct 2026 12:02:10 +0200\n\nProtokoll\n"), seen=True, note="nur BCC"))
    m.append(TestMail("Archiv", _dt(2025, 12, 18, 13, 30, 2), _crlf(
        "Delivered-To: test@example.de\nFrom: info@verein.example\nTo: undisclosed-recipients:;\n"
        "Subject: Einladung Jahresabschluss\nDate: Thu, 18 Dec 2025 14:30:00 +0100\n\nEinladung\n"), seen=True, note="per BCC erhalten"))
    m.append(TestMail("Groß", _dt(2026, 9, 1, 8, 0, 0), _crlf(
        "From: archiv@example.org\nTo: test@example.de\nSubject: Grosse Mail\nDate: Tue, 01 Sep 2026 10:00:00 +0200\n"
        "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"g\"\n\n--g\nContent-Type: text/plain\n\nGross\n"
        f"--g\nContent-Type: application/octet-stream; name=\"daten.bin\"\nContent-Disposition: attachment; filename=\"daten.bin\"\n"
        f"Content-Transfer-Encoding: base64\n\n{_b64(BIG)}\n--g--\n"), note="ca. 4 MB, stueckweises Laden"))
    # DKIM: fest signierte Mails (Test-Schlüssel in validation/dkim, Domain dkim.example)
    m.append(TestMail("Verband", _dt(2026, 11, 5, 9, 0, 5), (DKIM_DIR / "signed-pass.eml").read_bytes(),
                      note="DKIM gueltig signiert, Authentication-Results"))
    m.append(TestMail("Verband", _dt(2026, 11, 6, 9, 0, 5), (DKIM_DIR / "signed-tampered.eml").read_bytes(),
                      note="DKIM-Signatur, Text nachtraeglich veraendert"))
    return m


DKIM_DIR = HERE / "dkim"
DKIM_SELECTOR = "fed2026"
DKIM_DOMAIN = "dkim.example"


def dkim_dns() -> dict[str, str]:
    """Lokale Antwort auf die DNS-Abfrage des Test-Schlüssels (für Tests und Validierung)."""
    txt = (DKIM_DIR / f"{DKIM_SELECTOR}._domainkey.{DKIM_DOMAIN}.txt").read_text().strip()
    return {f"{DKIM_SELECTOR}._domainkey.{DKIM_DOMAIN}": txt}


EMPTY_FOLDERS = ["Leer"]


def _maildir_name(folder: str) -> str:
    """Maildir++-Verzeichnisname eines Ordners (mUTF-7, Trennzeichen '.')."""
    if folder == "INBOX":
        return ""
    return "." + ".".join(_encode_mutf7(p) for p in folder.split(SEPARATOR))


def _encode_mutf7(s: str) -> str:
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            b = base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append(f"&{b}-")
            buf = ""

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def folders() -> set[str]:
    """Alle Ordner des Testpostfachs (auch leere und reine Elternordner)."""
    return {m.folder for m in mails()} | set(EMPTY_FOLDERS) | {"Projekte"}


def build(maildir: Path) -> list[TestMail]:
    """Schreibt das Postfach nach ``maildir`` (z. B. ``home/test@example.de/Maildir``)."""
    folders_ = folders()
    for f in sorted(folders_):
        d = maildir / _maildir_name(f) if f != "INBOX" else maildir
        for sub in ("cur", "new", "tmp"):
            (d / sub).mkdir(parents=True, exist_ok=True)
    (maildir / "subscriptions").write_text("\n".join(sorted(folders_ - {"INBOX"})) + "\n", encoding="utf-8")
    for i, m in enumerate(mails(), start=1):
        d = maildir / _maildir_name(m.folder) if m.folder != "INBOX" else maildir
        ts = int(m.received.timestamp())
        name = f"{ts}.M{i}P1.fedtest,S={len(m.raw)}:2,{'S' if m.seen else ''}"
        p = d / "cur" / name
        p.write_bytes(m.raw)
        os.utime(p, (ts, ts))
    return mails()


def write_expected(path: Path = HERE / "expected_hashes.csv") -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["folder", "received_utc", "size", "sha256", "md5", "note"])
        for m in mails():
            w.writerow([m.folder, m.received.strftime("%Y-%m-%dT%H:%M:%SZ"), len(m.raw),
                        hashlib.sha256(m.raw).hexdigest(), hashlib.md5(m.raw).hexdigest(), m.note])


if __name__ == "__main__":
    if sys.argv[1:] == ["--expected"]:
        write_expected()
    elif len(sys.argv) == 2:
        build(Path(sys.argv[1]))
    else:
        print(__doc__)
