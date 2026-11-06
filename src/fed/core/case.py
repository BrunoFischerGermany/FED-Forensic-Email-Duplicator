"""Case anlegen und öffnen, Case-Datei (SQLite) und Log mit Hash-Kette.

Ablage auf dem Ziel-Datenträger::

    <Casenumber>/
    ├─ case.sqlite
    ├─ arbeit/       .eml während der Sicherung
    ├─ verworfen/    Altstände bei Neubeginn
    ├─ <Casenumber>_<zeit>.zip (+ .sha256, .tsr)
    ├─ log.csv
    ├─ bericht.txt / bericht.pdf
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from fed import __version__
from fed.core.naming import sanitize_component

SCHEMA_VERSION = "1"
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
        self.conn = conn

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
        (root / "arbeit").mkdir(parents=True, exist_ok=True)
        (root / "verworfen").mkdir(exist_ok=True)
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
        case.log("INFO", "case_opened", {"tool_version": __version__})
        return case

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ Log
    def log(self, level: str, event: str, details: dict | str = "", attempt_id: int | None = None) -> None:
        """Hängt einen Eintrag an das Log an (Hash-Kette, Commit sofort)."""
        text = details if isinstance(details, str) else json.dumps(details, ensure_ascii=False, sort_keys=True)
        ts = utc_now()
        with self.conn:
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
        return self.root / "arbeit"

    @property
    def discarded_dir(self) -> Path:
        return self.root / "verworfen"


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _entry_hash(ts: str, attempt_id: int | None, level: str, event: str, details: str, prev: str) -> str:
    payload = json.dumps([ts, attempt_id, level, event, details, prev], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
