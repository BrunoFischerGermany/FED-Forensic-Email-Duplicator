"""Gesamtbewertung einer Sicherung: Vollständigkeit und Integrität – mit Begründung in Klartext.

Zwei getrennte Urteile, weil es zwei verschiedene Fragen sind (eine Sicherung kann unvollständig
und trotzdem verifiziert sein):

- **Vollständigkeit**: ``complete`` (so viele Mails gesichert, wie der Server zum Sicherungszeitpunkt
  gemeldet hat) · ``partial`` = **Teilsicherung** (bewusst eingeschränkt: Ordner abgewählt, Zeitraum oder
  Suchkriterien – kein Mangel) · ``incomplete`` (Versuch abgebrochen/fehlgeschlagen, weniger gesichert als
  auf dem Server) · ``none`` (noch nichts gesichert).
- **Integrität**: ``verified`` (alle ZIPs nach dem Packen erneut gehasht und identisch, Log-Kette
  unverändert) · ``failed`` · ``pending`` (Fall noch nicht abgeschlossen).

Alles kommt aus der Fall-Datei (nur SQL, keine Dateien) – schnell genug für die Abschlussseite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from fed.core.case import Case
from fed.core.msg import N_, current_language
from fed.core.msg import t as _t


@dataclass
class Assessment:
    completeness: str = "none"  # complete | partial | incomplete | none
    completeness_reasons: list[str] = field(default_factory=list)
    integrity: str = "pending"  # verified | failed | pending
    integrity_reasons: list[str] = field(default_factory=list)
    figures: dict = field(default_factory=dict)


def num(value: int) -> str:
    """Ganzzahl mit Tausendertrennzeichen der aktuellen Sprache (Deutsch 3.399, sonst 3,399)."""
    return f"{value:,}".replace(",", "." if current_language() == "de" else ",")


def _secs(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def _event_account(details: str) -> int | None:
    try:
        d = json.loads(details) if details else {}
    except ValueError:
        return None
    return d.get("account_id") if isinstance(d, dict) else None


def assess(case: Case, account_id: int | None = None) -> Assessment:
    """Gesamtbewertung des Falls bzw. – mit ``account_id`` – eines Kontos."""
    from fed.core.acquisition import accounts_overview

    res = Assessment()
    overview = [o for o in accounts_overview(case) if account_id is None or o["id"] == account_id]
    many = len(overview) > 1
    reasons_inc, reasons_part = [], []
    for o in overview:
        who = f"{o['evidence'] or o['email']}: " if many else ""
        state = o["state"]
        if state in ("cancelled", "failed", "running", "new"):
            text = {"cancelled": _t("letzter Sicherungsversuch abgebrochen"),
                    "failed": _t("letzter Sicherungsversuch fehlgeschlagen"),
                    "running": _t("Sicherung nicht beendet"),
                    "new": _t("noch nicht gesichert")}[state]
            reasons_inc.append(who + text)
        elif state == "partial":
            reasons_inc.append(who + _t("{saved} von {expected} Mails gesichert (Server-Stand)",
                                        saved=num(o["saved"]), expected=num(o["expected"])))
        if o["folders_selected"] < o["folders_total"]:
            names = [r[0] for r in case.conn.execute(
                "SELECT display_name FROM folder WHERE account_id = ? AND selected = 0 ORDER BY display_name",
                (o["id"],))]
            shown = ", ".join(names[:5]) + (" …" if len(names) > 5 else "")
            reasons_part.append(who + _t("{n} Ordner bewusst nicht gewählt ({names})", n=len(names), names=shown))
        if o["date_filter"]:
            row = case.conn.execute("SELECT date_from, date_to, filters FROM account WHERE id = ?",
                                    (o["id"],)).fetchone()
            parts = []
            if row[0] or row[1]:
                parts.append(_t("Zeitraum {von} – {bis}", von=(row[0] or "…")[:10], bis=(row[1] or "…")[:10]))
            if row[2] and json.loads(row[2] or "{}"):
                parts.append(_t("Suchkriterien"))
            reasons_part.append(who + _t("eingeschränkt auf {what}", what=", ".join(parts) or _t("Filter")))
    ids = [o["id"] for o in overview]
    q = ",".join("?" * len(ids)) or "NULL"
    changed = case.conn.execute(
        f"SELECT COUNT(*) FROM folder WHERE account_id IN ({q}) AND selected = 1 AND status_before IS NOT NULL "
        "AND status_after IS NOT NULL AND status_before != status_after", ids).fetchone()[0]
    events = case.conn.execute(
        "SELECT l.level, l.event, l.details, t.account_id FROM log l LEFT JOIN attempt t ON t.id = l.attempt_id "
        "WHERE l.level IN ('WARNING', 'ERROR')").fetchall()
    events = [e for e in events if account_id is None or account_id in (e[3], _event_account(e[2]))]
    vanished = sum(1 for e in events if e[1] == "message_vanished")
    notes = []
    if changed:
        notes.append(_t("{n} Ordner haben sich während der Sicherung am Server geändert", n=changed))
    if vanished:
        notes.append(_t("{n} Mails während der Sicherung am Server gelöscht und übersprungen", n=num(vanished)))
    if not overview or all(o["state"] == "new" for o in overview):
        res.completeness = "none"
    elif reasons_inc:
        res.completeness = "incomplete"
    elif reasons_part:
        res.completeness = "partial"
    else:
        res.completeness = "complete"
    res.completeness_reasons = reasons_inc + reasons_part + notes

    # Integrität: ZIP-Verifikation (letztes Ergebnis je Datei), Log-Kette, Zeitstempel
    zips: dict[str, bool] = {}
    for (details,) in case.conn.execute("SELECT details FROM log WHERE event = 'zip_verified' ORDER BY id"):
        d = json.loads(details)
        problems = [p for p in d.get("problems", []) if not p.startswith("not_in_case")]
        zips[d.get("file", "")] = not problems
    if account_id is not None and zips:
        own = {r[0] for r in case.conn.execute(
            "SELECT file_name FROM output WHERE file_name LIKE '%.zip' AND (account_id = ? OR account_id IS NULL)",
            (account_id,))}
        zips = {k: v for k, v in zips.items() if k in own} or zips
    chain_ok, bad = case.verify_log()
    if not chain_ok:
        res.integrity = "failed"
        res.integrity_reasons.append(_t("Log-Kette gebrochen ab Eintrag {id}", id=bad))
    if zips:
        failed = [k for k, v in zips.items() if not v]
        if failed:
            res.integrity = "failed"
            res.integrity_reasons.append(_t("ZIP-Verifikation fehlgeschlagen: {files}", files=", ".join(failed)))
        elif chain_ok:
            res.integrity = "verified"
            res.integrity_reasons.append(_t("{n} ZIP erneut gehasht, alle Mails identisch; Log-Kette unverändert",
                                            n=len(zips)))
    elif chain_ok:
        res.integrity = "pending"
        res.integrity_reasons.append(_t("noch nicht abgeschlossen – ZIP-Verifikation folgt beim Abschluss"))
    ts_ok = sum(1 for (e,) in case.conn.execute("SELECT event FROM log WHERE event = 'timestamp_done'"))
    ts_bad = sum(1 for (e,) in case.conn.execute(
        "SELECT event FROM log WHERE event IN ('timestamp_failed', 'timestamp_timeout')"))
    if ts_ok:
        res.integrity_reasons.append(_t("{n} RFC-3161-Zeitstempel", n=ts_ok))
    if ts_bad:
        res.integrity_reasons.append(_t("Zeitstempel fehlgeschlagen ({n}×) – Hashes trotzdem geprüft", n=ts_bad))

    # Kennzahlen
    folders = case.conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(selected), 0) FROM folder WHERE account_id IN ({q})", ids).fetchone()
    attempts = case.conn.execute(
        f"SELECT started_utc, ended_utc FROM attempt WHERE account_id IN ({q})", ids).fetchall()
    started = [a[0] for a in attempts if a[0]]
    ended = [a[1] for a in attempts if a[1]]
    span = _secs(min(started), max(ended)) if started and ended and len(ended) == len(started) else None
    res.figures = {
        "accounts": len(overview),
        "folders_selected": folders[1], "folders_total": folders[0],
        "mails_saved": sum(o["saved"] for o in overview),
        "mails_expected": sum(o["expected"] for o in overview if not o["date_filter"]),
        "filtered": any(o["date_filter"] for o in overview),
        "bytes": sum(o["bytes"] for o in overview),
        "warnings": sum(1 for e in events if e[0] == "WARNING"),
        "errors": sum(1 for e in events if e[0] == "ERROR"),
        "seconds": span,
        "attempts": len(attempts),
    }
    return res


def figures_text(a: Assessment, fmt_bytes, fmt_duration) -> str:
    """Kennzahlen in einer Zeile, z. B. „3 Konten · 46/48 Ordner · 24.847/24.851 Mails · 4 Hinweise …“."""
    f = a.figures
    short = not f["filtered"] and f["mails_saved"] < f["mails_expected"]  # nur bei Fehlbestand „gesichert/Server“
    mails = f"{num(f['mails_saved'])}/{num(f['mails_expected'])}" if short else num(f["mails_saved"])
    parts = [_t("{n} Konten", n=f["accounts"]) if f["accounts"] != 1 else _t("1 Konto"),
             _t("{a}/{b} Ordner", a=f["folders_selected"], b=f["folders_total"]),
             _t("{n} Mails", n=mails), fmt_bytes(f["bytes"]),
             _t("{n} Hinweise", n=f["warnings"]) if f["warnings"] != 1 else _t("1 Hinweis")]
    if f["errors"]:
        parts.append(_t("{n} Fehler", n=f["errors"]))
    if f["seconds"] is not None:
        parts.append(fmt_duration(f["seconds"]))
    return " · ".join(parts)


LABELS = {"complete": N_("Vollständig"), "partial": N_("Teilsicherung"), "incomplete": N_("Unvollständig"),
          "none": N_("Noch nichts gesichert"), "verified": N_("Verifiziert"), "failed": N_("Nicht verifiziert"),
          "pending": N_("Noch nicht verifiziert")}


def label(state: str) -> str:
    return _t(LABELS[state])


__all__ = ["Assessment", "assess", "figures_text", "label", "num"]
