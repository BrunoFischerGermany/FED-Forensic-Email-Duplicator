"""Case anlegen und öffnen, Case-Datei (SQLite) und Log mit Hash-Kette.

Ablage auf dem Ziel-Datenträger::

    <Casenumber>/
    ├─ case.sqlite
    ├─ work/         .eml während der Sicherung
    ├─ discarded/    Altstände bei Neubeginn
    ├─ <Casenumber>_<zeit>.zip (+ .sha256, .tsr)
    ├─ log.csv
    ├─ report.txt / report.pdf
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from fed import __version__
from fed.core.naming import sanitize_component

SCHEMA_VERSION = "1"
_LOG_LOCK = threading.Lock()
GENESIS_HASH = "0" * 64
CASE_DB = "case.sqlite"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CaseInfo:
    case_number: str
    examiner: str
    notes: str = ""


class Case:
    """Ein geöffneter Case. Nach ``close()`` nur noch lesend nutzbar."""

    def __init__(self, root: Path, conn: sqlite3.Connection) -> None:
        self.root = root
        self._db = root / CASE_DB
        self._local = threading.local()
        self._local.conn = conn
        self._all = [conn]
        self._lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        """Eine Verbindung je Thread (WAL: GUI liest, während der Download schreibt)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = _connect(self._db)
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    # ------------------------------------------------------------ anlegen/öffnen
    @classmethod
    def create(cls, base_dir: Path, info: CaseInfo, tool_sha256: str | None = None) -> Case:
        if not info.case_number.strip():
            raise ValueError("Casenumber fehlt")
        if not info.examiner.strip():
            raise ValueError("Examiner fehlt")
        root = base_dir / sanitize_component(info.case_number.strip())
        if (root / CASE_DB).exists():
            raise FileExistsError(f"Case existiert bereits: {root}")
        (root / "work").mkdir(parents=True, exist_ok=True)
        (root / "discarded").mkdir(exist_ok=True)
        if tool_sha256 is None:
            from fed.core.hashing import tool_fingerprint

            tool_sha256 = tool_fingerprint()
        conn = _connect(root / CASE_DB)
        conn.executescript(resources.files("fed.resources").joinpath("schema.sql").read_text("utf-8"))
        now = datetime.now(UTC)
        with conn:
            conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            conn.execute(
                "INSERT INTO case_info (id, case_number, examiner, notes, created_utc, "
                "created_local, tool_version, tool_sha256) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (
                    info.case_number.strip(),
                    info.examiner.strip(),
                    info.notes,
                    now.isoformat(timespec="milliseconds"),
                    now.astimezone().isoformat(timespec="milliseconds"),
                    __version__,
                    tool_sha256,
                ),
            )
        case = cls(root, conn)
        case.log("INFO", "case_created", {"case_number": info.case_number, "path": str(root)})
        return case

    @classmethod
    def open(cls, root: Path) -> Case:
        db = root / CASE_DB
        if not db.exists():
            raise FileNotFoundError(db)
        case = cls(root, _connect(db))
        _migrate(case)
        case.log("INFO", "case_opened", {"tool_version": __version__})
        return case

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except sqlite3.ProgrammingError:
                    pass
            self._all.clear()
        self._local = threading.local()

    # ------------------------------------------------------------ Einstellungen des Case
    ZIP_MODES = ("gesamt", "je_konto")

    def zip_mode(self) -> str | None:
        """Gewählte ZIP-Aufteilung (``gesamt``/``je_konto``) oder ``None``, wenn noch nicht entschieden."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'zip_mode'").fetchone()
        return row[0] if row else None

    def set_zip_mode(self, mode: str, source: str = "examiner") -> None:
        """ZIP-Aufteilung festlegen (protokolliert); nach Abschluss nicht mehr änderbar."""
        if mode not in self.ZIP_MODES:
            raise ValueError(f"ZIP-Aufteilung unbekannt: {mode}")
        if self.closed:
            raise RuntimeError("Case ist abgeschlossen")
        old = self.zip_mode()
        if old == mode:
            return
        with self.conn:
            self.conn.execute("INSERT INTO meta (key, value) VALUES ('zip_mode', ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))
        self.log("INFO", "zip_mode_set", {"mode": mode, "previous": old, "source": source})

    # ------------------------------------------------------------ Log
    def log(self, level: str, event: str, details: dict | str = "", attempt_id: int | None = None) -> None:
        """Hängt einen Eintrag an das Log an (Hash-Kette, Commit sofort)."""
        text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, sort_keys=True)
        ts = utc_now()
        with _LOG_LOCK, self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT entry_hash FROM log ORDER BY id DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS_HASH
            entry = _entry_hash(ts, attempt_id, level, event, text, prev)
            self.conn.execute(
                "INSERT INTO log (ts_utc, attempt_id, level, event, details, prev_hash, entry_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, attempt_id, level, event, text, prev, entry),
            )

    def verify_log(self) -> tuple[bool, int | None]:
        """Prüft die Hash-Kette. Rückgabe: (ok, id des ersten fehlerhaften Eintrags)."""
        prev = GENESIS_HASH
        for row in self.conn.execute(
            "SELECT id, ts_utc, attempt_id, level, event, details, prev_hash, entry_hash FROM log ORDER BY id"
        ):
            id_, ts, att, level, event, details, prev_hash, entry_hash = row
            if prev_hash != prev or entry_hash != _entry_hash(ts, att, level, event, details, prev):
                return False, id_
            prev = entry_hash
        return True, None

    # ------------------------------------------------------------ Config und Logo
    MAX_LOGO_BYTES = 2 * 1024 * 1024

    def store_config_snapshot(self, toml_text: str, logo_path: Path | None) -> None:
        """Kopie der Config und des Logos (als BLOB) – spätere Änderungen betreffen diesen Case nicht."""
        blob = mime = sha = name = None
        if logo_path and logo_path.exists():
            blob = logo_path.read_bytes()
            if len(blob) > self.MAX_LOGO_BYTES:
                raise ValueError("Logo größer als 2 MB")
            if blob[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif blob[:3] == b"\xff\xd8\xff":
                mime = "image/jpeg"
            else:
                raise ValueError("Logo muss PNG oder JPG sein")
            sha = hashlib.sha256(blob).hexdigest()
            name = logo_path.name
        with self.conn:
            self.conn.execute(
                "INSERT INTO config_snapshot (id, toml_text, logo_file, logo_mime, logo_blob, logo_sha256) "
                "VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET toml_text = excluded.toml_text, "
                "logo_file = excluded.logo_file, logo_mime = excluded.logo_mime, logo_blob = excluded.logo_blob, "
                "logo_sha256 = excluded.logo_sha256",
                (toml_text, name, mime, blob, sha),
            )
        self.log("INFO", "config_snapshot", {"logo": name, "logo_sha256": sha})

    def logo(self) -> tuple[bytes, str, str] | None:
        row = self.conn.execute("SELECT logo_blob, logo_mime, logo_sha256 FROM config_snapshot WHERE id = 1").fetchone()
        return (row[0], row[1], row[2]) if row and row[0] else None

    def info(self) -> dict:
        cur = self.conn.execute("SELECT * FROM case_info WHERE id = 1")
        return dict(zip([c[0] for c in cur.description], cur.fetchone(), strict=True))

    @property
    def closed(self) -> bool:
        return self.info()["closed_utc"] is not None

    def record_system_check(self, info: dict) -> None:
        cols = ["ts_utc", "user", "hostname", "os", "local_time", "utc_time", "tz", "ntp_offset_s", "online",
                "proxy", "target_path", "target_removable", "free_bytes"]
        with self.conn:
            self.conn.execute(f"INSERT INTO system_check ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              [info.get(c) for c in cols])
        self.log("INFO", "system_check", info)

    # ------------------------------------------------------------ Pfade
    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def discarded_dir(self) -> Path:
        return self.root / "discarded"


def _migrate(case: Case) -> None:
    """Ältere Case-Dateien ergänzen (nur neue Spalten, nie Daten ändern); wird protokolliert."""
    for table, col, ddl in (("account", "notes", "TEXT"), ("output", "account_id", "INTEGER")):
        cols = {r[1] for r in case.conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            with case.conn:
                case.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            case.log("INFO", "schema_migrated", {"added": f"{table}.{col}"})


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _entry_hash(ts: str, attempt_id: int | None, level: str, event: str, details: str, prev: str) -> str:
    payload = json.dumps([ts, attempt_id, level, event, details, prev], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
