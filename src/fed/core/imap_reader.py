"""Nur lesender IMAP-Zugriff.

Regeln:
- Ordner nur mit ``EXAMINE`` öffnen (nie ``SELECT``)
- Mails nur mit ``UID FETCH ... (BODY.PEEK[] ...)``, damit kein \\Seen gesetzt wird
- keine schreibenden Befehle; ``guard()`` blockiert sie technisch
- TLS-Zertifikat wird geprüft, Fingerprint protokolliert
"""

from __future__ import annotations

import imaplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime

# Befehle, die das Postfach verändern könnten und deshalb verboten sind
FORBIDDEN_COMMANDS = frozenset(
    {"STORE", "COPY", "MOVE", "EXPUNGE", "APPEND", "DELETE", "RENAME", "CREATE",
     "SUBSCRIBE", "UNSUBSCRIBE", "SELECT", "SETACL", "DELETEACL", "SETQUOTA", "SETMETADATA"}
)


class ForbiddenCommand(RuntimeError):
    pass


def guard(command: str) -> None:
    """Wirft ``ForbiddenCommand`` für schreibende IMAP-Befehle."""
    verb = command.strip().split()[0].upper()
    if verb == "UID":
        verb = command.strip().split()[1].upper()
    if verb in FORBIDDEN_COMMANDS:
        raise ForbiddenCommand(f"Schreibender IMAP-Befehl blockiert: {verb}")


@dataclass
class FolderInfo:
    raw_name: bytes
    name: str
    delimiter: str | None
    flags: tuple[str, ...]
    messages: int = 0
    unseen: int = 0
    uidnext: int = 0
    uidvalidity: int = 0
    size: int = 0


@dataclass
class FetchedMessage:
    uid: int
    raw: bytes
    flags: tuple[str, ...]
    internaldate: datetime
    size_server: int
    gm_msgid: str | None = None
    gm_labels: tuple[str, ...] = field(default_factory=tuple)


class ReadOnlyImap:
    """Dünne Hülle um ``imaplib.IMAP4_SSL`` mit nur lesenden Operationen."""

    def __init__(self, host: str, port: int = 993, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.ssl_context = ssl.create_default_context()  # Zertifikat wird geprüft
        self._imap: imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        raise NotImplementedError("folgt in Meilenstein 2")

    def cert_sha256(self) -> str:
        raise NotImplementedError

    def login_password(self, user: str, password: str) -> None:
        raise NotImplementedError

    def login_xoauth2(self, user: str, access_token: str) -> None:
        raise NotImplementedError

    def server_info(self) -> dict:
        """CAPABILITY, ID, NAMESPACE, GETQUOTAROOT."""
        raise NotImplementedError

    def list_folders(self) -> list[FolderInfo]:
        raise NotImplementedError

    def examine(self, folder: FolderInfo) -> None:
        raise NotImplementedError

    def search_uids(self, since: datetime | None = None, before: datetime | None = None) -> list[int]:
        raise NotImplementedError

    def fetch(self, uid: int, chunk_bytes: int | None = None) -> FetchedMessage:
        raise NotImplementedError

    def logout(self) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            finally:
                self._imap = None
