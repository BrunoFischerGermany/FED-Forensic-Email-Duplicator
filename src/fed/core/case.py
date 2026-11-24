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
from fed.core import logo
from fed.core.naming import file_name
from fed.core.syscheck import host_snapshot
from fed.core.msg import t

SCHEMA_VERSION = "1"
_LOG_LOCK = threading.Lock()
# Versuche, die in diesem Prozess gerade laufen: (Case-Pfad, attempt.id). Alles andere mit
# status = 'running' stammt aus einem abgebrochenen Programmlauf (Absturz, Stromausfall).
_LIVE: set[tuple[str, int]] = set()
LIVE_LOCK = threading.Lock()
GENESIS_HASH = "0" * 64
CASE_DB = "case.sqlite"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class CaseInfo:
    case_number: str
    examiner: str
    notes: str = ""
    notes_html: str | None = None  # formatierte Fassung; ``notes`` wird dann daraus abgeleitet


class Case:
    """Ein geöffneter Case. Nach ``close()`` nur noch lesend nutzbar."""

    def __init__(self, root: Path, conn: sqlite3.Connection) -> None:
        self.root = root
        self._db = root / CASE_DB
        self._local = threading.local()
        self._local.conn = conn
        self._all = [conn]
        self._lock = threading.Lock()
        self.chain_ok_at_open: bool = True  # Ergebnis der Kettenprüfung beim Öffnen (Case.open)
        self.chain_first_bad: int | None = None

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
    def create(cls, base_dir: Path, info: CaseInfo, tool_sha256: str | None = None,
               replace_spaces: bool = True) -> Case:
        if not info.case_number.strip():
            raise ValueError(t("Aktenzeichen fehlt"))
        if not info.examiner.strip():
            raise ValueError(t("Examiner fehlt"))
        root = base_dir / file_name(info.case_number.strip(), replace_spaces)
        if (root / CASE_DB).exists():
            raise FileExistsError(f"Fall existiert bereits: {root}")
        (root / "work").mkdir(parents=True, exist_ok=True)
        (root / "discarded").mkdir(exist_ok=True)
        if tool_sha256 is None:
            from fed.core.hashing import tool_fingerprint

            tool_sha256 = tool_fingerprint()
        from fed.core.richtext import normalize

        notes, notes_html = normalize(info.notes_html, info.notes)
        conn = _connect(root / CASE_DB)
        conn.executescript(resources.files("fed.resources").joinpath("schema.sql").read_text("utf-8"))
        now = datetime.now(UTC)
        with conn:
            conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            conn.execute(
                "INSERT INTO case_info (id, case_number, examiner, notes, notes_html, created_utc, "
                "created_local, tool_version, tool_sha256) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    info.case_number.strip(),
                    info.examiner.strip(),
                    notes,
                    notes_html,
                    now.isoformat(timespec="milliseconds"),
                    now.astimezone().isoformat(timespec="milliseconds"),
                    __version__,
                    tool_sha256,
                ),
            )
        case = cls(root, conn)
        with case.conn:  # Schreibweise der Dateinamen gilt für den ganzen Case
            case.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('name_spaces', ?)",
                              ("underscore" if replace_spaces else "keep",))
        case.log("INFO", "case_created", {"case_number": info.case_number, "path": str(root),
                                          "name_spaces": "underscore" if replace_spaces else "keep",
                                          "host": host_snapshot()})
        return case

    @classmethod
    def open(cls, root: Path) -> Case:
        db = root / CASE_DB
        if not db.exists():
            raise FileNotFoundError(db)
        case = cls(root, _connect(db))
        _migrate(case)
        ok, bad = case.verify_log()  # Kette bei jedem Öffnen prüfen; Ergebnis steht im Eintrag selbst
        case.chain_ok_at_open, case.chain_first_bad = ok, bad
        case.log("INFO" if ok else "ERROR", "case_opened",
                 {"tool_version": __version__, "host": host_snapshot(), "chain_ok": ok, "first_bad": bad})
        case.recover_interrupted()
        return case

    # ------------------------------------------------------------ laufende Versuche
    def _key(self, attempt_id: int) -> tuple[str, int]:
        return (str(self.root.resolve()), attempt_id)

    def mark_live(self, attempt_id: int) -> None:
        _LIVE.add(self._key(attempt_id))

    def unmark_live(self, attempt_id: int) -> None:
        _LIVE.discard(self._key(attempt_id))

    def is_live(self, attempt_id: int) -> bool:
        return self._key(attempt_id) in _LIVE

    def recover_interrupted(self, account_id: int | None = None) -> int:
        """Versuche mit status 'running', die in keinem laufenden Vorgang stecken, als abgebrochen
        ('failed', phase 'interrupted') abschließen und protokollieren. Rückgabe: Anzahl."""
        with LIVE_LOCK:
            sql = "SELECT id, account_id FROM attempt WHERE status = 'running'"
            rows = self.conn.execute(sql + (" AND account_id = ?" if account_id else ""),
                                     (account_id,) if account_id else ()).fetchall()
            stale = [(i, a) for i, a in rows if not self.is_live(i)]
            for attempt_id, _acc in stale:
                with self.conn:
                    self.conn.execute("UPDATE attempt SET status = 'failed', phase = 'interrupted', "
                                      "ended_utc = COALESCE(ended_utc, ?) WHERE id = ?", (utc_now(), attempt_id))
        for attempt_id, acc in stale:
            self.log("WARNING", "attempt_interrupted", {"account_id": acc}, attempt_id)
        return len(stale)

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                try:
                    c.close()
                except sqlite3.ProgrammingError:
                    pass
            self._all.clear()
        self._local = threading.local()

    DEVICE_OWNERS = ("own", "subject")

    def set_device_owner(self, owner: str, hostname: str = "", asked_utc: str | None = None) -> None:
        """Auf wessen Gerät läuft FED? ``own`` = eigenes Gerät (z. B. Dienstgerät), ``subject`` = Gerät der
        betroffenen Person. Jede Angabe wird protokolliert (auch beim erneuten Öffnen auf einem anderen Rechner)."""
        if owner not in self.DEVICE_OWNERS:
            raise ValueError(owner)
        with self.conn:
            self.conn.execute("UPDATE case_info SET device_owner = ? WHERE id = 1", (owner,))
        details = {"device_owner": owner, "hostname": hostname}
        if asked_utc:  # beim Programmstart beantwortet
            details["asked_utc"] = asked_utc
        self.log("INFO", "device_owner_confirmed", details)

    def device_owner_history(self) -> list[dict]:
        """Alle Angaben zum ausführenden Gerät in zeitlicher Reihenfolge (aus dem Log)."""
        rows = self.conn.execute("SELECT ts_utc, details FROM log WHERE event = 'device_owner_confirmed' ORDER BY id")
        out = []
        for ts, details in rows:
            d = json.loads(details) if details else {}
            out.append({"ts_utc": ts, "device_owner": d.get("device_owner"), "hostname": d.get("hostname", "")})
        return out

    def settings_state(self) -> dict[str, str] | None:
        """Einstellungsstand des Falls: Kopie bei der Fallanlage plus alle seither protokollierten Änderungen."""
        import tomllib

        from fed.core import config

        row = self.conn.execute("SELECT toml_text FROM config_snapshot WHERE id = 1").fetchone()
        if row is None:
            return None
        try:
            snap = config.parse_lenient(row[0])
            try:
                config.validate(snap)  # normalisiert Altwerte (z. B. „immer“ -> „liste“) wie beim Laden
            except config.ConfigError:
                pass
            state = config.flat_settings(snap)
        except (tomllib.TOMLDecodeError, TypeError, ValueError):
            return None
        for (details,) in self.conn.execute("SELECT details FROM log WHERE event = 'settings_changed' ORDER BY id"):
            for c in json.loads(details).get("changes", []):
                state[c["key"]] = c["new"]
        return state

    def record_settings(self, cfg, step: str) -> list[dict]:
        """Wirksame Einstellungen mit dem Stand des Falls vergleichen; Abweichungen protokollieren
        (``settings_changed``, Geheimnisse geschwärzt). Rückgabe: die Änderungen."""
        from fed.core import config

        base = self.settings_state()
        if base is None:
            return []
        cur = config.flat_settings(cfg)
        changes = [{"key": k, "old": base.get(k, ""), "new": cur.get(k, "")}
                   for k in sorted(set(base) | set(cur)) if base.get(k, "") != cur.get(k, "")]
        if changes:
            summary = "; ".join(f"{c['key']}: {c['old'] or '–'} → {c['new'] or '–'}" for c in changes)
            self.log("INFO", "settings_changed", {"step": step, "changes": changes, "summary": summary})
        return changes

    def settings_changes(self) -> list[dict]:
        """Alle protokollierten Einstellungsänderungen (für den Bericht)."""
        out = []
        for ts, details in self.conn.execute(
                "SELECT ts_utc, details FROM log WHERE event = 'settings_changed' ORDER BY id"):
            d = json.loads(details)
            out += [{"ts_utc": ts, "step": d.get("step", ""), **c} for c in d.get("changes", [])]
        return out

    def host_history(self) -> list[dict]:
        """Rechner/Benutzer je Arbeitsschritt (Fallanlage, Öffnen, Sicherungsversuch, Abschluss) aus dem
        Log – so ist nachvollziehbar, wenn z. B. vor Ort gesichert und im Amt abgeschlossen wurde."""
        steps = {"case_created": "create", "case_opened": "open", "attempt_started": "attempt",
                 "finalize_started": "finalize"}
        rows = self.conn.execute(
            "SELECT l.ts_utc, l.event, l.details, a.nr, acc.email FROM log l "
            "LEFT JOIN attempt a ON a.id = l.attempt_id LEFT JOIN account acc ON acc.id = a.account_id "
            f"WHERE l.event IN ({', '.join('?' * len(steps))}) ORDER BY l.id", list(steps))
        out = []
        for ts, event, details, nr, email in rows:
            try:
                d = json.loads(details) if details else {}
            except ValueError:
                continue
            host = d.get("host") if isinstance(d, dict) else None
            if not isinstance(host, dict):
                continue  # ältere Fälle: Schritt ohne Rechnerangabe
            out.append({"ts_utc": ts, "step": steps[event], "user": host.get("user") or "",
                        "hostname": host.get("hostname") or "", "os": host.get("os") or "",
                        "tz": host.get("tz") or "", "nr": nr, "email": email})
        return out

    def set_closing_note(self, text: str = "", html_value: str | None = None) -> bool:
        """Abschlussvermerk des Examiners (vor dem Bericht); wird protokolliert. True, wenn geändert."""
        from fed.core.richtext import normalize

        if self.closed:
            raise RuntimeError(t("Fall ist abgeschlossen"))
        plain, html_ = normalize(html_value, text)
        plain = plain.strip() or None
        old = self.conn.execute("SELECT closing_note, closing_note_html FROM case_info WHERE id = 1").fetchone()
        if (old[0], old[1]) == (plain, html_):
            return False
        with self.conn:
            self.conn.execute("UPDATE case_info SET closing_note = ?, closing_note_html = ? WHERE id = 1",
                              (plain, html_))
        details = {"old": old[0] or "", "new": plain or ""}
        if html_ or old[1]:
            details.update(old_html=old[1] or "", new_html=html_ or "")
        self.log("INFO", "closing_note_set", details)
        return True

    # ------------------------------------------------------------ Einstellungen des Case
    @property
    def replace_spaces(self) -> bool:
        """Leerzeichen in Dateinamen durch _ ersetzen – beim Anlegen festgelegt (ältere Cases: nein)."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'name_spaces'").fetchone()
        return bool(row) and row[0] == "underscore"

    def file_name(self, text: str) -> str:
        """Datei-/Ordnername aus Casenumber oder Evidence-Nummer nach der Regel dieses Case."""
        return file_name(text, self.replace_spaces)

    ZIP_MODES = ("gesamt", "je_konto")

    def zip_mode(self) -> str | None:
        """Gewählte ZIP-Aufteilung (``gesamt``/``je_konto``) oder ``None``, wenn noch nicht entschieden."""
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'zip_mode'").fetchone()
        return row[0] if row else None

    def set_zip_mode(self, mode: str, source: str = "examiner") -> None:
        """ZIP-Aufteilung festlegen (protokolliert); nach Abschluss nicht mehr änderbar."""
        if mode not in self.ZIP_MODES:
            raise ValueError(t("ZIP-Aufteilung unbekannt: {mode}", mode=mode))
        if self.closed:
            raise RuntimeError(t("Fall ist abgeschlossen"))
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
        """Kopie der Config und des Logos (als BLOB) – spätere Änderungen betreffen diesen Case nicht.

        Geheimnisse (Google-Client-Secret, Zugangsdaten in der Proxy-URL) werden vor dem Speichern
        geschwärzt; welche Schlüssel betroffen waren, steht im Log (nie der Wert)."""
        from fed.core.config import redact_text

        toml_text, redacted = redact_text(toml_text)
        blob = mime = sha = name = None
        if logo_path and logo_path.exists():
            blob = logo_path.read_bytes()
            if len(blob) > self.MAX_LOGO_BYTES:
                raise ValueError(t("Logo größer als 2 MB"))
            mime = logo.detect(blob)
            if mime is None:
                raise ValueError(t("Logo muss PNG, JPG oder SVG sein"))
            name = logo_path.name
            if mime == "image/svg+xml":  # PDF braucht ein Rasterbild: umwandeln, Original-Hash protokollieren
                svg_sha = hashlib.sha256(blob).hexdigest()
                try:
                    blob = logo.svg_to_png(blob)
                except Exception as e:
                    raise ValueError(t("Logo: SVG kann nicht umgewandelt werden ({error})", error=e)) from e
                mime, name = "image/png", logo_path.with_suffix(".png").name
                self.log("INFO", "logo_converted", {"svg": logo_path.name, "svg_sha256": svg_sha,
                                                    "png_sha256": hashlib.sha256(blob).hexdigest(),
                                                    "pixels": logo.SVG_PIXELS})
            sha = hashlib.sha256(blob).hexdigest()
        with self.conn:
            self.conn.execute(
                "INSERT INTO config_snapshot (id, toml_text, logo_file, logo_mime, logo_blob, logo_sha256) "
                "VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET toml_text = excluded.toml_text, "
                "logo_file = excluded.logo_file, logo_mime = excluded.logo_mime, logo_blob = excluded.logo_blob, "
                "logo_sha256 = excluded.logo_sha256",
                (toml_text, name, mime, blob, sha),
            )
        details = {"logo": name, "logo_sha256": sha}
        if redacted:
            details["redacted"] = redacted
        self.log("INFO", "config_snapshot", details)

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
    case.conn.execute(
        "CREATE TABLE IF NOT EXISTS dns_record (id INTEGER PRIMARY KEY, name TEXT NOT NULL, record TEXT, sha256 TEXT, "
        "status TEXT NOT NULL, error TEXT, fetched_utc TEXT NOT NULL, attempt_id INTEGER REFERENCES attempt(id))")
    # DNS-Schnappschuss der Konto-Domain zum Sicherungszeitpunkt (MX, SPF/TXT, DMARC, NS, SOA, A/AAAA).
    case.conn.execute(
        "CREATE TABLE IF NOT EXISTS domain_dns (id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES account(id), "
        "domain TEXT NOT NULL, rtype TEXT NOT NULL, name TEXT NOT NULL, value TEXT, sha256 TEXT, status TEXT NOT NULL, "
        "error TEXT, fetched_utc TEXT NOT NULL, attempt_id INTEGER REFERENCES attempt(id))")
    for table, col, ddl in (("account", "notes", "TEXT"), ("output", "account_id", "INTEGER"),
                            ("message", "dkim_result", "TEXT"), ("message", "dkim_domain", "TEXT"),
                            ("message", "dkim_selector", "TEXT"), ("message", "dkim_detail", "TEXT"),
                            ("message", "auth_results", "TEXT"), ("message", "gm_thrid", "TEXT"),
                            ("account", "filters", "TEXT"), ("account", "notes_html", "TEXT"),
                            ("case_info", "notes_html", "TEXT"), ("case_info", "closing_note", "TEXT"),
                            ("case_info", "closing_note_html", "TEXT"), ("case_info", "device_owner", "TEXT"),
                            ("message", "source", "TEXT"),
                            ("message", "partial", "INTEGER NOT NULL DEFAULT 0")):
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
