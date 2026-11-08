"""Nur lesender IMAP-Zugriff.

Regeln:
- Ordner nur mit ``EXAMINE`` öffnen (nie ``SELECT``)
- Mails nur mit ``UID FETCH ... (BODY.PEEK[] ...)``, damit kein \\Seen gesetzt wird
- keine schreibenden Befehle; ``guard()`` blockiert sie technisch für jeden gesendeten Befehl
- TLS-Zertifikat wird geprüft, Fingerprint protokolliert
"""

from __future__ import annotations

import hashlib
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

from fed.core.naming import decode_imap_utf7

# Befehle, die das Postfach verändern könnten und deshalb verboten sind
FORBIDDEN_COMMANDS = frozenset(
    {"STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "DELETE", "RENAME", "CREATE",
     "SUBSCRIBE", "UNSUBSCRIBE", "SELECT", "SETACL", "DELETEACL", "SETQUOTA", "SETMETADATA"}
)
STATUS_ITEMS = "(MESSAGES UNSEEN UIDNEXT UIDVALIDITY)"
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# imaplib kennt ID nicht; im erlaubten Zustand registrieren
imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))


class ForbiddenCommand(RuntimeError):
    pass


class ImapError(RuntimeError):
    pass


# FETCH-Elemente, die \\Seen setzen (RFC 3501 6.4.5): BODY[...] ohne PEEK, RFC822, RFC822.TEXT
_SEEN_SETTING = re.compile(r"(?<![.\w])BODY\[|(?<![.\w])RFC822(?![.\w])|RFC822\.TEXT", re.IGNORECASE)


def guard(command: str) -> None:
    """Wirft ``ForbiddenCommand`` für schreibende IMAP-Befehle und für FETCH-Elemente,
    die das Gelesen-Flag setzen würden (nur ``BODY.PEEK[...]`` ist erlaubt)."""
    parts = command.strip().split()
    verb = parts[0].upper()
    if verb == "UID" and len(parts) > 1:
        verb = parts[1].upper()
    if verb in FORBIDDEN_COMMANDS:
        raise ForbiddenCommand(f"Schreibender IMAP-Befehl blockiert: {verb}")
    if verb == "FETCH" and _SEEN_SETTING.search(command):
        raise ForbiddenCommand("FETCH ohne PEEK blockiert (würde \\Seen setzen)")


class _GuardMixin:
    """Prüft jeden Befehl, bevor imaplib ihn sendet."""

    def _command(self, name, *args):
        text = " ".join(a.decode("ascii", "replace") if isinstance(a, bytes) else str(a) for a in args
                        if isinstance(a, (str, bytes)))
        guard(f"{name} {text}")
        return super()._command(name, *args)


class _IMAP4(_GuardMixin, imaplib.IMAP4):
    pass


class _IMAP4_SSL(_GuardMixin, imaplib.IMAP4_SSL):
    pass


@dataclass
class FolderInfo:
    raw_name: str  # wie vom Server (mUTF-7)
    name: str  # dekodiert
    delimiter: str | None
    flags: tuple[str, ...]
    namespace: str = "personal"
    messages: int = 0
    unseen: int = 0
    uidnext: int = 0
    uidvalidity: int = 0
    size: int = 0

    @property
    def selectable(self) -> bool:
        return not any(f.lower() in ("\\noselect", "\\nonexistent") for f in self.flags)


@dataclass
class FetchedMessage:
    uid: int
    raw: bytes
    flags: tuple[str, ...]
    internaldate: datetime
    size_server: int
    gm_msgid: str | None = None
    gm_labels: tuple[str, ...] = field(default_factory=tuple)


def quote(raw_name: str) -> str:
    return '"' + raw_name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return re.sub(r"\\(.)", r"\1", s[1:-1])
    return s


def parse_internaldate(s: str) -> datetime:
    """``"17-Jul-1996 02:44:25 -0700"`` -> zeitzonenbehaftetes datetime (unabhängig von der Locale)."""
    m = re.match(r"\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})", s)
    if not m:
        raise ImapError(f"Unlesbares INTERNALDATE: {s!r}")
    d, mon, y, hh, mm, ss, sign, oh, om = m.groups()
    offset = timedelta(hours=int(oh), minutes=int(om)) * (-1 if sign == "-" else 1)
    return datetime(int(y), _MONTHS.index(mon.title()) + 1, int(d), int(hh), int(mm), int(ss),
                    tzinfo=timezone(offset))


def imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>NIL|"(?:\\.|[^"])*") (?P<name>.*)$')


def parse_list_line(item) -> tuple[tuple[str, ...], str | None, str] | None:
    """Eine Zeile der LIST-Antwort -> (flags, delimiter, raw_name)."""
    if isinstance(item, tuple):  # Name als Literal
        head, literal = item
        m = _LIST_RE.match(head.rstrip() + b" X")
        name = literal.decode("utf-8", "replace")
    else:
        m = _LIST_RE.match(item)
        name = None
    if not m:
        return None
    flags = tuple(m.group("flags").decode().split())
    delim_raw = m.group("delim").decode()
    delim = None if delim_raw == "NIL" else _unquote(delim_raw)
    if name is None:
        name = _unquote(m.group("name").decode("utf-8", "replace"))
    return flags, delim, name


class ReadOnlyImap:
    """Hülle um ``imaplib`` mit ausschließlich lesenden Operationen."""

    def __init__(self, host: str, port: int = 993, security: str = "ssl", timeout: float = 30.0,
                 cafile: str | None = None) -> None:
        self.host = host
        self.port = port
        self.security = security
        self.timeout = timeout
        self.ssl_context = ssl.create_default_context(cafile=cafile)  # Zertifikat wird geprüft
        self._imap: imaplib.IMAP4 | None = None
        self.current: FolderInfo | None = None

    # ------------------------------------------------------------ Verbindung
    def connect(self) -> None:
        from fed.core.naming import idna_host

        host = idna_host(self.host)  # Umlaut-Hostnamen: ASCII-Form für DNS und Zertifikatsprüfung
        if self.security == "ssl":
            self._imap = _IMAP4_SSL(host, self.port, ssl_context=self.ssl_context, timeout=self.timeout)
        elif self.security == "starttls":
            self._imap = _IMAP4(host, self.port, timeout=self.timeout)
            self._imap.starttls(self.ssl_context)
        else:
            raise ImapError("Unverschlüsselte Verbindungen sind nicht erlaubt")

    @property
    def imap(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise ImapError("Nicht verbunden")
        return self._imap

    def tls_info(self) -> dict:
        sock = self.imap.sock
        der = sock.getpeercert(binary_form=True)
        cert = sock.getpeercert()
        fp = hashlib.sha256(der).hexdigest().upper()
        return {
            "version": sock.version(),
            "cipher": sock.cipher()[0] if sock.cipher() else None,
            "sha256": ":".join(fp[i : i + 2] for i in range(0, len(fp), 2)),
            "subject": dict(x[0] for x in cert.get("subject", ())),
            "issuer": dict(x[0] for x in cert.get("issuer", ())),
            "not_after": cert.get("notAfter"),
        }

    def login_password(self, user: str, password: str) -> None:
        """Anmeldung mit Benutzername und Passwort.

        ``LOGIN`` erlaubt nur ASCII. Enthalten Benutzername oder Passwort Umlaute o. Ä.,
        wird ``AUTHENTICATE PLAIN`` (RFC 4616, UTF-8) verwendet.
        """
        if user.isascii() and password.isascii():
            self.imap.login(user, password)
            return
        if "AUTH=PLAIN" not in self.imap.capabilities:
            raise ImapError("Benutzername oder Passwort enthält Sonderzeichen (z. B. Umlaute), der Server bietet "
                            "aber keine Anmeldung per AUTHENTICATE PLAIN an")
        self.imap.authenticate("PLAIN", lambda _: f"\0{user}\0{password}".encode())

    def login_xoauth2(self, user: str, access_token: str) -> None:
        from fed.core.oauth import xoauth2_string

        self.imap.authenticate("XOAUTH2", lambda _: xoauth2_string(user, access_token))

    def noop(self) -> None:
        self.imap.noop()

    def logout(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            finally:
                self._imap = None

    # ------------------------------------------------------------ Server
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self.imap.capabilities)

    def server_info(self) -> dict:
        """CAPABILITY, ID, NAMESPACE, GETQUOTAROOT (soweit unterstützt)."""
        caps = self.capabilities()
        info: dict = {"capabilities": list(caps)}
        if "ID" in caps:
            typ, _ = self.imap._simple_command("ID", "NIL")
            data = self.imap.untagged_responses.pop("ID", [None])
            info["id"] = data[-1].decode("utf-8", "replace") if typ == "OK" and data[-1] else None
        if "NAMESPACE" in caps:
            typ, data = self.imap.namespace()
            info["namespace"] = data[0].decode("utf-8", "replace") if typ == "OK" else None
        if "QUOTA" in caps:
            typ, data = self.imap.getquotaroot('"INBOX"')
            info["quota"] = [d.decode("utf-8", "replace") if isinstance(d, bytes) else str(d) for d in data] if typ == "OK" else None
        return info

    # ------------------------------------------------------------ Ordner
    def list_folders(self) -> list[FolderInfo]:
        typ, data = self.imap.list('""', "*")
        if typ != "OK":
            raise ImapError(f"LIST fehlgeschlagen: {data}")
        folders = []
        for item in data:
            if item is None:
                continue
            parsed = parse_list_line(item)
            if parsed:
                flags, delim, raw = parsed
                folders.append(FolderInfo(raw, decode_imap_utf7(raw), delim, flags))
        folders.sort(key=lambda f: (f.name != "INBOX", f.name.lower()))
        return folders

    def status(self, folder: FolderInfo) -> dict:
        typ, data = self.imap.status(quote(folder.raw_name), STATUS_ITEMS)
        if typ != "OK":
            raise ImapError(f"STATUS {folder.name} fehlgeschlagen: {data}")
        text = data[0].decode("utf-8", "replace") if isinstance(data[0], bytes) else str(data[0])
        values = {k.upper(): int(v) for k, v in re.findall(r"(MESSAGES|UNSEEN|UIDNEXT|UIDVALIDITY) (\d+)", text)}
        folder.messages = values.get("MESSAGES", 0)
        folder.unseen = values.get("UNSEEN", 0)
        folder.uidnext = values.get("UIDNEXT", 0)
        folder.uidvalidity = values.get("UIDVALIDITY", 0)
        return values

    def examine(self, folder: FolderInfo) -> int:
        """Öffnet den Ordner nur lesend (EXAMINE). Gibt die Anzahl Mails zurück."""
        typ, data = self.imap.select(quote(folder.raw_name), readonly=True)
        if typ != "OK":
            raise ImapError(f"EXAMINE {folder.name} fehlgeschlagen: {data}")
        uv = self.imap.untagged_responses.get("UIDVALIDITY", [b"0"])[-1]
        folder.uidvalidity = int(uv) if uv else folder.uidvalidity
        self.current = folder
        return int(data[0] or 0)

    def search_uids(self, since: datetime | None = None, before: datetime | None = None) -> list[int]:
        criteria = []
        if since:
            criteria += ["SINCE", imap_date(since)]
        if before:
            criteria += ["BEFORE", imap_date(before)]
        typ, data = self.imap.uid("SEARCH", *(criteria or ["ALL"]))
        if typ != "OK":
            raise ImapError(f"SEARCH fehlgeschlagen: {data}")
        return sorted(int(x) for x in (data[0] or b"").split())

    def sizes(self) -> dict[int, int]:
        """UID -> RFC822.SIZE für alle Mails im geöffneten Ordner."""
        if self.current and self.current.messages == 0:
            return {}
        typ, data = self.imap.uid("FETCH", "1:*", "(UID RFC822.SIZE)")
        if typ != "OK":
            raise ImapError(f"FETCH SIZE fehlgeschlagen: {data}")
        result = {}
        for line in data:
            if isinstance(line, bytes):
                m = re.search(rb"UID (\d+).*RFC822\.SIZE (\d+)|RFC822\.SIZE (\d+).*UID (\d+)", line)
                if m:
                    if m.group(1):
                        result[int(m.group(1))] = int(m.group(2))
                    else:
                        result[int(m.group(4))] = int(m.group(3))
        return result

    # ------------------------------------------------------------ Mails
    def fetch(self, uid: int, chunk_bytes: int | None = None) -> FetchedMessage:
        """Lädt eine Mail unverändert. Ab ``chunk_bytes`` stückweise per ``BODY.PEEK[]<o.l>``."""
        gm = "X-GM-EXT-1" in self.capabilities()
        items = "(UID FLAGS INTERNALDATE RFC822.SIZE" + (" X-GM-MSGID X-GM-LABELS" if gm else "") + ")"
        typ, data = self.imap.uid("FETCH", str(uid), items)
        if typ != "OK" or not data or data[0] is None:
            raise ImapError(f"Mail UID {uid} nicht gefunden")
        meta = b" ".join(d if isinstance(d, bytes) else d[0] + d[1] for d in data if d)
        size = int(re.search(rb"RFC822\.SIZE (\d+)", meta).group(1))
        idate = parse_internaldate(re.search(rb'INTERNALDATE "([^"]+)"', meta).group(1).decode())
        fm = re.search(rb"FLAGS \(([^)]*)\)", meta)
        flags = tuple(fm.group(1).decode().split()) if fm else ()
        gm_msgid = None
        gm_labels: tuple[str, ...] = ()
        if gm:
            mm = re.search(rb"X-GM-MSGID (\d+)", meta)
            gm_msgid = mm.group(1).decode() if mm else None
            lm = re.search(rb"X-GM-LABELS \(([^)]*)\)", meta)
            gm_labels = tuple(lm.group(1).decode("utf-8", "replace").split()) if lm else ()

        if chunk_bytes and size > chunk_bytes:
            parts = []
            offset = 0
            while offset < size:
                parts.append(self._body(uid, f"BODY.PEEK[]<{offset}.{chunk_bytes}>"))
                if not parts[-1]:
                    break
                offset += len(parts[-1])
            raw = b"".join(parts)
        else:
            raw = self._body(uid, "BODY.PEEK[]")
        if len(raw) != size:
            raise ImapError(f"UID {uid}: {len(raw)} Bytes geladen, Server meldet {size}")
        return FetchedMessage(uid, raw, flags, idate, size, gm_msgid, gm_labels)

    def fetch_many(self, uids: list[int]) -> dict[int, FetchedMessage]:
        """Lädt mehrere Mails mit einem einzigen ``UID FETCH`` (spart je Mail einen Server-Umlauf).

        Nur für Mails unterhalb der Stückelungsgrenze verwenden. Mails, die der Server
        nicht mehr liefert (inzwischen gelöscht), fehlen im Ergebnis.
        """
        if not uids:
            return {}
        gm = "X-GM-EXT-1" in self.capabilities()
        items = "(UID FLAGS INTERNALDATE RFC822.SIZE" + (" X-GM-MSGID X-GM-LABELS" if gm else "") + " BODY.PEEK[])"
        typ, data = self.imap.uid("FETCH", ",".join(str(u) for u in uids), items)
        if typ != "OK":
            raise ImapError(f"FETCH {len(uids)} Mails fehlgeschlagen: {data}")
        result: dict[int, FetchedMessage] = {}
        i = 0
        while i < len(data):
            d = data[i]
            if not isinstance(d, tuple):
                i += 1
                continue
            head, raw = d
            trailer = data[i + 1] if i + 1 < len(data) and isinstance(data[i + 1], bytes) else b""
            meta = head + b" " + trailer
            i += 2 if trailer else 1
            if b"BODY[" not in head:
                continue
            um = re.search(rb"UID (\d+)", meta)
            sm = re.search(rb"RFC822\.SIZE (\d+)", meta)
            im = re.search(rb'INTERNALDATE "([^"]+)"', meta)
            if not (um and sm and im):
                raise ImapError(f"Unvollständige FETCH-Antwort: {meta[:200]!r}")
            uid, size = int(um.group(1)), int(sm.group(1))
            if len(raw) != size:
                raise ImapError(f"UID {uid}: {len(raw)} Bytes geladen, Server meldet {size}")
            fm = re.search(rb"FLAGS \(([^)]*)\)", meta)
            gm_id = re.search(rb"X-GM-MSGID (\d+)", meta) if gm else None
            gl = re.search(rb"X-GM-LABELS \(([^)]*)\)", meta) if gm else None
            result[uid] = FetchedMessage(
                uid, raw, tuple(fm.group(1).decode().split()) if fm else (),
                parse_internaldate(im.group(1).decode()), size,
                gm_id.group(1).decode() if gm_id else None,
                tuple(gl.group(1).decode("utf-8", "replace").split()) if gl else ())
        return result

    def _body(self, uid: int, item: str) -> bytes:
        typ, data = self.imap.uid("FETCH", str(uid), f"({item})")
        if typ != "OK":
            raise ImapError(f"FETCH {item} UID {uid} fehlgeschlagen: {data}")
        for d in data:
            if isinstance(d, tuple) and b"BODY[" in d[0]:
                return d[1]
        return b""


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
