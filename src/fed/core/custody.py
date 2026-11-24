"""Chain of Custody: das vollständige Ereignis-Log eines Falls als eigenständige, prüfbare Datei.

``chain_of_custody.csv`` enthält jeden Log-Eintrag mit Zeit (UTC und lokal), Ereignis im Klartext,
Rechner/Benutzer und – unverändert – den Rohfeldern der Hash-Kette (``TS_UTC``, ``Attempt``,
``Level``, ``Event``, ``Details``, ``Prev_Hash``, ``Entry_Hash``). Damit lässt sich die Kette ohne FED
nachrechnen (Anleitung in ``chain_of_custody_README.txt``)::

    Entry_Hash = SHA-256( UTF-8( JSON-Array [TS_UTC, Attempt, Level, Event, Details, Prev_Hash] ) )

mit JSON wie Pythons ``json.dumps(..., ensure_ascii=False)`` (Trenner ``", "``), ``Attempt`` als Zahl
oder ``null``, ``Prev_Hash`` des ersten Eintrags = 64 × ``0``.

Die Hash-Kette allein schützt nicht davor, dass jemand mit Zugriff auf die Fall-Datei die ganze
Kette neu berechnet. Prüfanker ist deshalb ``manifest.json`` (Hash des letzten Eintrags, mit
RFC-3161-Zeitstempel) bzw. der unterschriebene Bericht.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from fed.core import timefmt as tf
from fed.core.case import GENESIS_HASH, Case

COC_NAME = "chain_of_custody.csv"
README_NAME = "chain_of_custody_README.txt"
COLUMNS = ["ID", "TS_UTC", "TS_Local", "Level", "Event", "Description", "Attempt", "Account", "User", "Host",
           "Details", "Prev_Hash", "Entry_Hash"]

README = """Chain of Custody – Prüfanleitung / verification guide
=====================================================

DE
--
chain_of_custody.csv enthält jeden Eintrag des Fall-Logs von FED (Forensic Email Duplicator) in
zeitlicher Reihenfolge (Trennzeichen Semikolon, UTF-8). Jeder Eintrag ist mit dem vorherigen über
SHA-256 verkettet. So prüfen Sie die Kette ohne FED:

  1. Für jede Zeile das JSON-Array bilden:
       [TS_UTC, Attempt, Level, Event, Details, Prev_Hash]
     Attempt als Zahl, leer = null; alle anderen Felder als Text, genau wie in der Datei.
     JSON-Schreibweise: Trenner ", " (Komma + Leerzeichen), Nicht-ASCII-Zeichen unverändert
     (wie Python json.dumps(..., ensure_ascii=False)).
  2. SHA-256 über die UTF-8-Bytes dieses Textes = Entry_Hash der Zeile.
  3. Prev_Hash jeder Zeile = Entry_Hash der vorherigen Zeile; erste Zeile: 64 × "0".

Wichtig: Die Kette zeigt Änderungen innerhalb der Kette. Gegen ein vollständiges Neuberechnen
schützt erst ein Anker außerhalb: Hash des letzten Eintrags in manifest.json (mit RFC-3161-
Zeitstempel manifest.json.tsr) und im unterschriebenen Bericht.

EN
--
chain_of_custody.csv lists every entry of the FED case log in order (semicolon-separated, UTF-8).
Each entry is chained to the previous one via SHA-256. To verify without FED:

  1. For each row build the JSON array [TS_UTC, Attempt, Level, Event, Details, Prev_Hash]
     (Attempt as number, empty = null; JSON separators ", ", non-ASCII characters unescaped,
     as Python json.dumps(..., ensure_ascii=False)).
  2. SHA-256 of the UTF-8 bytes = Entry_Hash of that row.
  3. Prev_Hash of each row = Entry_Hash of the previous row; first row: 64 × "0".

The chain reveals changes within the chain; protection against recomputing the whole chain comes
from an external anchor: the last entry hash in manifest.json (with RFC 3161 timestamp
manifest.json.tsr) and in the signed report.

Python (standard library only)
------------------------------
import csv, hashlib, json
prev = "0" * 64
with open("chain_of_custody.csv", encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f, delimiter=";"):
        att = int(r["Attempt"]) if r["Attempt"] else None
        payload = json.dumps([r["TS_UTC"], att, r["Level"], r["Event"], r["Details"], prev],
                             ensure_ascii=False)
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert r["Prev_Hash"] == prev and r["Entry_Hash"] == h, "broken at ID " + r["ID"]
        prev = h
print("OK, last entry hash:", prev)
"""


def _host(details: str) -> tuple[str, str]:
    try:
        d = json.loads(details) if details else {}
    except ValueError:
        return "", ""
    h = d.get("host") if isinstance(d, dict) else None
    if not isinstance(h, dict):
        return "", ""
    return h.get("user") or "", h.get("hostname") or ""


def rows(case: Case, language: str = "de") -> list[dict]:
    """Alle Log-Einträge als Zeilen für ``chain_of_custody.csv``."""
    from fed.core.report import describe_event, labels

    L = labels(language)
    out = []
    for id_, ts, att, level, event, details, prev, entry, email, nr in case.conn.execute(
            "SELECT l.id, l.ts_utc, l.attempt_id, l.level, l.event, l.details, l.prev_hash, l.entry_hash, "
            "acc.email, a.nr FROM log l LEFT JOIN attempt a ON a.id = l.attempt_id "
            "LEFT JOIN account acc ON acc.id = a.account_id ORDER BY l.id"):
        user, host = _host(details)
        out.append({
            "ID": id_, "TS_UTC": ts, "TS_Local": tf.local_str(ts), "Level": level, "Event": event,
            "Description": describe_event(event, details, L), "Attempt": "" if att is None else att,
            "Account": f"{email} #{nr}" if email else "", "User": user, "Host": host, "Details": details,
            "Prev_Hash": prev, "Entry_Hash": entry})
    return out


def write(case: Case, path: Path, language: str = "de", readme: bool = True) -> dict:
    """``chain_of_custody.csv`` (und daneben die Prüfanleitung) schreiben.

    Rückgabe: ``{"entries", "last_entry_hash", "chain_ok", "first_bad"}`` – Stand des Logs in der Datei.
    """
    ok, bad = case.verify_log()
    data = rows(case, language)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(data)
    if readme:
        path.with_name(README_NAME).write_text(README, encoding="utf-8")
    return {"entries": len(data), "last_entry_hash": data[-1]["Entry_Hash"] if data else None,
            "chain_ok": ok, "first_bad": bad}


def verify_csv(path: Path) -> tuple[bool, int | None, int, str]:
    """Kette einer ``chain_of_custody.csv`` nachrechnen (wie in der Anleitung).

    Rückgabe ``(ok, erste fehlerhafte ID, Anzahl Einträge, Hash des letzten Eintrags)``.
    """
    prev, n = GENESIS_HASH, 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            att = int(r["Attempt"]) if r["Attempt"] else None
            payload = json.dumps([r["TS_UTC"], att, r["Level"], r["Event"], r["Details"], prev], ensure_ascii=False)
            h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            n += 1
            if r["Prev_Hash"] != prev or r["Entry_Hash"] != h:
                return False, int(r["ID"]), n, prev
            prev = h
    return True, None, n, prev
