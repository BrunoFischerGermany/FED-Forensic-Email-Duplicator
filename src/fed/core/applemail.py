"""Apple Mail (macOS) offline einlesen – nur lesend, byte-genau.

Apple Mail legt Mails unter ``~/Library/Mail/V#/<Konto-UUID>/`` ab. Die Ordner sind Bündel mit der
Endung ``.mbox`` (Verzeichnisse, nicht MBOX-Dateien) und können verschachtelt sein; die
tatsächlichen Mails liegen je Ordner unter ``<UUID>/Data/Messages/`` als ``*.emlx`` bzw.
``*.partial.emlx``.

``.emlx``-Aufbau: erste Zeile = Byte-Länge der Nachricht (mit Leerzeichen aufgefüllt), danach
**genau so viele Bytes** die rohe Nachricht (RFC822), danach ein Apple-Plist mit Flags und Datum.
FED liest genau diese N Bytes als ``.eml`` – byte-genau wie das Original, der Hash gilt dafür.

``.partial.emlx`` bedeutet: Apple Mail hat die Nachricht nur unvollständig geladen (Anhänge liegen
ggf. noch auf dem Server). FED kennzeichnet solche Mails; vollständig holt man sie später online nach.

Die Zuordnung Konto-UUID → **E-Mail-Adresse** steht **nicht** im ``V#``-Ordner (Ordner und die
Datenbank ``Envelope Index`` kennen Konten nur über die UUID). FED schlägt die Adresse aus den
Kopfzeilen der Mails vor; die ermittelnde Person bestätigt sie. Liegt ein Vollabbild mit
``~/Library/Accounts`` vor, kann die Adresse von dort kommen (späterer Ausbau).
"""

from __future__ import annotations

import plistlib
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.parser import BytesHeaderParser
from pathlib import Path

from fed.core.msg import t

# Flags-Bits im Apple-Plist (nur die stabilen, verlässlichen werden ausgewertet; der Rohwert bleibt
# erhalten). Höhere Bits (Anhangzahl, Priorität) sind je macOS-Version uneinheitlich und werden nicht
# gedeutet.
_FLAG_SEEN = 1 << 0
_FLAG_FLAGGED = 1 << 4

_UUID = re.compile(r"\A[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\Z")
_ADDR = re.compile(rb"[\w.+-]+@[\w.-]+\.\w+")
# Welche Kopfzeile die eigene Adresse des Kontos trägt: im Postausgang der Absender, sonst der Empfänger.
_SENT_NAMES = ("sent", "gesendet", "postausgang", "drafts", "entwürfe", "entwurfe")


class EmlxError(Exception):
    """Eine ``.emlx``-Datei ist unlesbar oder unvollständig."""


def read_emlx(path: Path) -> tuple[bytes, dict]:
    """Liest eine ``.emlx``/``.partial.emlx``: gibt (rohe Nachricht, Plist-Metadaten) zurück.

    Die rohe Nachricht umfasst genau die in der ersten Zeile angegebene Byte-Zahl – byte-genau.
    """
    data = path.read_bytes()
    nl = data.find(b"\n")
    if nl < 0:
        raise EmlxError(t("{name}: keine Längenangabe", name=path.name))
    try:
        n = int(data[:nl].strip())
    except ValueError as e:
        raise EmlxError(t("{name}: ungültige Längenangabe", name=path.name)) from e
    body = data[nl + 1 : nl + 1 + n]
    if len(body) != n:
        raise EmlxError(t("{name}: unvollständig ({got} von {n} Bytes)", name=path.name, got=len(body), n=n))
    trailer = data[nl + 1 + n :].lstrip(b"\r\n")
    meta: dict = {}
    if trailer.startswith(b"<?xml"):
        try:
            loaded = plistlib.loads(trailer)
            if isinstance(loaded, dict):
                meta = loaded
        except (plistlib.InvalidFileException, ValueError):
            meta = {}
    return body, meta


@dataclass
class AppleMessage:
    """Eine Mail aus einer ``.emlx``-Datei. ``raw`` wird erst bei Bedarf gelesen (große Postfächer)."""

    path: Path
    number: int
    partial: bool

    def read(self) -> tuple[bytes, dict]:
        return read_emlx(self.path)

    @property
    def raw(self) -> bytes:
        return self.read()[0]

    @staticmethod
    def seen(meta: dict) -> bool:
        return bool(int(meta.get("flags", 0)) & _FLAG_SEEN)

    @staticmethod
    def flagged(meta: dict) -> bool:
        return bool(int(meta.get("flags", 0)) & _FLAG_FLAGGED)

    @staticmethod
    def date_received(meta: dict) -> datetime | None:
        v = meta.get("date-received")
        return datetime.fromtimestamp(int(v), UTC) if v else None


@dataclass
class AppleFolder:
    """Ein ``.mbox``-Ordner. ``path`` ist der lesbare Pfad wie ``INBOX/Sent`` (ohne ``.mbox``)."""

    path: str
    mbox_dir: Path

    def messages(self) -> list[AppleMessage]:
        out: list[AppleMessage] = []
        for msgdir in sorted(self.mbox_dir.glob("*/Data")):
            mdir = msgdir / "Messages"
            if not mdir.is_dir():
                continue
            for f in mdir.iterdir():
                name = f.name
                if name.endswith(".partial.emlx"):
                    num, partial = name[: -len(".partial.emlx")], True
                elif name.endswith(".emlx"):
                    num, partial = name[: -len(".emlx")], False
                else:
                    continue
                out.append(AppleMessage(f, int(num) if num.isdigit() else -1, partial))
        return sorted(out, key=lambda m: (m.number, m.path.name))


@dataclass
class AppleAccount:
    """Ein Konto (UUID-Ordner) mit seinen ``.mbox``-Ordnern und Adress-Vorschlägen."""

    uuid: str
    root: Path
    folders: list[AppleFolder] = field(default_factory=list)

    def message_count(self) -> int:
        return sum(len(f.messages()) for f in self.folders)


def _walk_mboxes(base: Path, prefix: str = "") -> list[AppleFolder]:
    """Baut rekursiv die Ordnerliste aus verschachtelten ``*.mbox``-Verzeichnissen."""
    out: list[AppleFolder] = []
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and entry.name.endswith(".mbox"):
            name = entry.name[: -len(".mbox")]
            path = f"{prefix}/{name}" if prefix else name
            out.append(AppleFolder(path, entry))
            out.extend(_walk_mboxes(entry, path))
    return out


def find_accounts(v10_dir: Path) -> list[AppleAccount]:
    """Alle Konten unter einem ``V#``-Verzeichnis (Unterordner mit UUID-Namen)."""
    accounts: list[AppleAccount] = []
    for entry in sorted(v10_dir.iterdir()):
        if entry.is_dir() and _UUID.match(entry.name):
            folders = _walk_mboxes(entry)
            if folders:
                accounts.append(AppleAccount(entry.name, entry, folders))
    return accounts


def find_v10_dirs(root: Path) -> list[Path]:
    """Sucht ``V#``-Verzeichnisse (``V10`` …) unter ``root`` – direkt, unter ``Mail/`` oder ``Library/Mail/``.

    Kein vollständiger Platten-Scan: nur die bekannten Stellen relativ zum angegebenen Ort.
    """
    found: list[Path] = []
    candidates = [root, root / "Mail", root / "Library" / "Mail"]
    for base in candidates:
        if not base.is_dir():
            continue
        if _UUID_child(base):
            found.append(base)
        for sub in sorted(base.glob("V*")):
            if sub.is_dir() and re.fullmatch(r"V\d+", sub.name) and _UUID_child(sub):
                found.append(sub)
    # Doppelte (gleicher Pfad) entfernen, Reihenfolge erhalten
    seen: set[Path] = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def _UUID_child(d: Path) -> bool:
    return any(c.is_dir() and _UUID.match(c.name) for c in d.iterdir())


@dataclass
class AccountInfo:
    """Maßgebliche Konto-Angaben aus ``~/Library/Accounts/Accounts4.sqlite``."""

    address: str = ""
    name: str = ""  # ZACCOUNTDESCRIPTION, z. B. „Freenet.de“
    aliases: tuple[str, ...] = ()


def _archived_emails(blob: object) -> list[str]:
    """Liest die E-Mail-Adressen aus einem NSKeyedArchiver-Plist (Apple speichert Strings dort in
    ``$objects``). Nur Treffer, die wie eine Adresse aussehen."""
    if not isinstance(blob, (bytes, bytearray)):
        return []
    try:
        d = plistlib.loads(bytes(blob))
    except (plistlib.InvalidFileException, ValueError):
        return []
    objs = d.get("$objects", []) if isinstance(d, dict) else []
    out: list[str] = []
    for o in objs:
        if isinstance(o, str) and _ADDR.fullmatch(o.encode("utf-8", "replace")):
            out.append(o)
    return out


def accounts_map(sqlite_path: Path) -> dict[str, AccountInfo]:
    """Zuordnung Konto-UUID → Adresse/Name/Aliase aus ``Accounts4.sqlite`` (maßgeblich).

    Die Datei wird samt ``-wal``/``-shm`` in einen temporären Ordner kopiert und dort gelesen, damit
    offene WAL-Daten einbezogen werden und das Original unberührt bleibt (nur lesen).
    """
    result: dict[str, AccountInfo] = {}
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / sqlite_path.name
        shutil.copy2(sqlite_path, work)
        for suffix in ("-wal", "-shm"):
            side = sqlite_path.with_name(sqlite_path.name + suffix)
            if side.exists():
                shutil.copy2(side, Path(tmp) / side.name)
        con = sqlite3.connect(work)
        try:
            con.row_factory = sqlite3.Row
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ZACCOUNT" not in have:
                return result
            props_by_owner: dict[int, dict[str, object]] = {}
            if "ZACCOUNTPROPERTY" in have:
                for p in con.execute("SELECT ZOWNER, ZKEY, ZVALUE FROM ZACCOUNTPROPERTY"):
                    props_by_owner.setdefault(p["ZOWNER"], {})[p["ZKEY"]] = p["ZVALUE"]
            for r in con.execute(
                "SELECT Z_PK, ZIDENTIFIER, ZUSERNAME, ZACCOUNTDESCRIPTION FROM ZACCOUNT WHERE ZIDENTIFIER IS NOT NULL"
            ):
                uuid = r["ZIDENTIFIER"]
                if not uuid or not _UUID.match(uuid):
                    continue
                props = props_by_owner.get(r["Z_PK"], {})
                identity = _archived_emails(props.get("IdentityEmailAddress"))
                user = (r["ZUSERNAME"] or "").strip()
                address = identity[0] if identity else (user if "@" in user else "")
                aliases = _archived_emails(props.get("EmailAliases"))
                info = AccountInfo(address, (r["ZACCOUNTDESCRIPTION"] or "").strip(), tuple(dict.fromkeys(aliases)))
                if info.address or info.name:
                    result[uuid] = info
        finally:
            con.close()
    return result


def resolve_address(account: AppleAccount, mapping: dict[str, AccountInfo] | None) -> tuple[str, str, list[tuple[str, int]]]:
    """Bestimmt die Adresse eines Kontos. Rückgabe: (Adresse, Quelle, Kandidaten).

    Quelle ``accounts_db`` = maßgeblich aus ``Accounts4.sqlite``; ``headers`` = aus den Kopfzeilen
    (von der ermittelnden Person zu bestätigen); ``""`` = keine Adresse gefunden. ``candidates``
    sind immer die Kopfzeilen-Treffer (zur Anzeige/Kontrolle).
    """
    candidates = address_candidates(account)
    info = (mapping or {}).get(account.uuid)
    if info and info.address:
        return info.address, "accounts_db", candidates
    return (candidates[0][0] if candidates else ""), ("headers" if candidates else ""), candidates


def address_candidates(account: AppleAccount, limit: int = 400) -> list[tuple[str, int]]:
    """Vorschläge für die eigene Adresse des Kontos, aus den Kopfzeilen (häufigste zuerst).

    Im Postausgang zählt der Absender (``From``), sonst der Empfänger (``Delivered-To``/``To``).
    Nur ein Vorschlag – bei Alias-Konten mehrere; die ermittelnde Person bestätigt.
    """
    parser = BytesHeaderParser()
    counter: Counter[str] = Counter()
    scanned = 0
    for folder in account.folders:
        is_sent = any(part.lower() in _SENT_NAMES for part in folder.path.split("/"))
        headers = ("from",) if is_sent else ("delivered-to", "x-envelope-to", "to")
        for msg in folder.messages():
            if scanned >= limit:
                break
            try:
                raw, _ = msg.read()
            except EmlxError:
                continue
            scanned += 1
            head = parser.parsebytes(raw, headersonly=True)
            for h in headers:
                for value in head.get_all(h, []):
                    for a in _ADDR.findall(value.encode("utf-8", "replace")):
                        counter[a.decode("ascii", "replace").lower()] += 1
    return counter.most_common()
