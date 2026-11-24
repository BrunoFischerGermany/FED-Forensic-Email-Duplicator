"""„Fallakte prüfen“: alles, was sich an einem Fall nachprüfen lässt, in einem Durchgang.

1. **Log-Kette** – jeder Eintrag gegen seinen Vorgänger (SHA-256).
2. **Manifest** – Datei gegen ``manifest.json.sha256`` und gegen den protokollierten Hash; der darin
   festgehaltene Log-Stand (Hash des letzten Eintrags) muss in der Kette an derselben Stelle stehen.
3. **Ausgabedateien** – jede im Manifest bzw. in der Fall-Datei geführte Datei erneut gehasht.
4. **ZIP-Seitendateien** – ``<zip>.sha256`` passt zum ZIP.
5. **Mails** – jede ``.eml`` im ZIP (bzw. im Arbeitsordner, solange es kein ZIP gibt) gegen die Fall-Datei.
6. **Zeitstempel** – jede ``.tsr`` bezieht sich auf den aktuellen Hash ihrer Datei (ZIP/Manifest).
   Die Signatur des Zeitstempeldienstes prüft FED nicht selbst; dafür steht der
   ``openssl ts -verify``-Befehl im Bericht.

Ergebnis je Punkt: ``ok`` (True), Fehler (False) oder Hinweis (None, z. B. „kein Manifest – Fall vor
Einführung abgeschlossen“). Jede Prüfung wird im Log festgehalten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from fed.core.cancel import CancelToken
from fed.core.case import Case
from fed.core.hashing import hash_file
from fed.core.msg import describe
from fed.core.msg import t as _t


@dataclass
class CheckItem:
    name: str
    ok: bool | None  # True = bestanden, False = Fehler, None = Hinweis / nicht prüfbar
    detail: str = ""


@dataclass
class CaseCheck:
    items: list[CheckItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i.ok is not False for i in self.items)

    @property
    def errors(self) -> list[CheckItem]:
        return [i for i in self.items if i.ok is False]

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.items.append(CheckItem(name, ok, detail))


def _log_chain(case: Case, res: CaseCheck) -> None:
    ok, bad = case.verify_log()
    n = case.conn.execute("SELECT COUNT(*) FROM log").fetchone()[0]
    res.add(_t("Log-Kette"), ok, _t("{n} Einträge unverändert", n=n) if ok
            else _t("gebrochen ab Eintrag {id}", id=bad))


def _manifest(case: Case, res: CaseCheck) -> dict | None:
    from fed.core.finalize import MANIFEST_NAME

    path = case.root / MANIFEST_NAME
    if not path.exists():
        res.add(_t("Manifest"), None, _t("nicht vorhanden – Fall noch offen oder vor Einführung des Manifests "
                                         "abgeschlossen") if case.closed else _t("nicht vorhanden – Fall noch offen"))
        return None
    sha = hash_file(path)["sha256"]
    problems = []
    side = path.with_name(path.name + ".sha256")
    if not side.exists() or side.read_text(encoding="utf-8").split()[:1] != [sha]:
        problems.append(_t("passt nicht zu {file}", file=side.name))
    logged = case.conn.execute("SELECT details FROM log WHERE event = 'manifest_created' ORDER BY id DESC LIMIT 1"
                               ).fetchone()
    if not logged or sha not in (logged[0] or ""):
        problems.append(_t("Hash nicht wie im Log protokolliert"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        res.add(_t("Manifest"), False, _t("nicht lesbar"))
        return None
    lg = data.get("log") or {}
    row = case.conn.execute("SELECT entry_hash FROM log WHERE id = ?", (lg.get("last_id"),)).fetchone()
    if not row or row[0] != lg.get("last_entry_hash"):
        problems.append(_t("festgehaltener Log-Stand (Eintrag {id}) passt nicht zur Kette", id=lg.get("last_id")))
    res.add(_t("Manifest"), not problems, "; ".join(problems) or _t(
        "SHA-256 {sha} · verankert Log bis Eintrag {id}", sha=sha, id=lg.get("last_id")))
    return data


def _outputs(case: Case, manifest: dict | None, res: CaseCheck, cancel: CancelToken) -> None:
    from fed.core.finalize import MANIFEST_NAME

    expected: dict[str, str] = {}
    for name, sha in case.conn.execute("SELECT file_name, sha256 FROM output ORDER BY id"):
        expected.setdefault(name, sha)
    if manifest:
        for f in manifest.get("files", []):
            if expected.get(f["file"], f["sha256"]) != f["sha256"]:
                res.add(_t("Ausgabedateien"), False, _t("{file}: Manifest und Fall-Datei widersprechen sich",
                                                        file=f["file"]))
            expected[f["file"]] = f["sha256"]
    expected.pop(MANIFEST_NAME, None)  # oben geprüft
    if not expected:
        res.add(_t("Ausgabedateien"), None, _t("noch keine (Fall nicht abgeschlossen)"))
        return
    changed, missing = [], []
    for name, sha in expected.items():
        cancel.raise_if_cancelled()
        p = case.root / name
        if not p.exists():
            missing.append(name)
        elif hash_file(p)["sha256"] != sha:
            changed.append(name)
    if changed:
        res.add(_t("Ausgabedateien"), False, _t("verändert: {files}", files=", ".join(changed)))
    else:
        res.add(_t("Ausgabedateien"), True, _t("{n} Dateien identisch", n=len(expected) - len(missing)))
    if missing:  # z. B. verschlüsselte Kopie weitergegeben und entfernt – Hinweis, kein Fehler
        res.add(_t("Ausgabedateien fehlen"), None, ", ".join(missing))


def _zip_sidecars(case: Case, res: CaseCheck) -> None:
    bad = []
    zips = [r[0] for r in case.conn.execute("SELECT DISTINCT file_name FROM output WHERE file_name LIKE '%.zip'")]
    for name in zips:
        p, side = case.root / name, case.root / (name + ".sha256")
        if p.exists() and side.exists() and side.read_text(encoding="utf-8").split()[:1] != [hash_file(p)["sha256"]]:
            bad.append(side.name)
    if zips:
        res.add(_t("ZIP-Prüfsummendateien"), not bad, _t("passen nicht: {files}", files=", ".join(bad)) if bad
                else _t("{n} passend", n=len(zips)))


def _mails(case: Case, res: CaseCheck, cancel: CancelToken) -> None:
    from fed.core.finalize import verify_case_zips

    n_valid = case.conn.execute("SELECT COUNT(*) FROM message WHERE status = 'valid'").fetchone()[0]
    if not n_valid:
        res.add(_t("Mails"), None, _t("keine gesicherten Mails"))
        return
    zips = verify_case_zips(case, cancel)
    if zips:
        problems = [describe(p) for z in zips for p in z.problems]
        res.add(_t("Mails im ZIP"), all(z.verified for z in zips),
                "; ".join(problems[:10]) + (" …" if len(problems) > 10 else "") if problems
                else _t("{n} .eml erneut gehasht, alle identisch", n=sum(z.files for z in zips)))
        return
    if not case.work_dir.exists():
        res.add(_t("Mails"), False, _t("weder ZIP noch Arbeitsordner vorhanden"))
        return
    bad = []
    for zip_path, sha in case.conn.execute("SELECT zip_path, sha256 FROM message WHERE status = 'valid'"):
        cancel.raise_if_cancelled()
        p = case.work_dir / zip_path
        if not p.exists() or hash_file(p)["sha256"] != sha:
            bad.append(zip_path)
    res.add(_t("Mails im Arbeitsordner"), not bad,
            _t("{n} von {total} abweichend oder fehlend: {files}", n=len(bad), total=n_valid,
               files=", ".join(bad[:10]) + (" …" if len(bad) > 10 else "")) if bad
            else _t("{n} .eml erneut gehasht, alle identisch", n=n_valid))


def _timestamps(case: Case, res: CaseCheck) -> None:
    from fed.core.timestamp import TimestampError, parse_response

    tsrs = sorted(p for p in case.root.glob("*.tsr"))
    if not tsrs:
        return
    bad, ok = [], 0
    for tsr in tsrs:
        target = tsr.with_name(tsr.name.removesuffix(".tsr"))
        try:
            info = parse_response(tsr.read_bytes())
        except (TimestampError, ValueError, IndexError) as e:
            bad.append(f"{tsr.name}: {e}")
            continue
        if not target.exists():
            bad.append(_t("{file}: Datei fehlt", file=target.name))
        elif info.hashed_message != hash_file(target)["sha256"]:
            bad.append(_t("{file}: Hash passt nicht zum Zeitstempel", file=target.name))
        else:
            ok += 1
    res.add(_t("Zeitstempel (RFC 3161)"), not bad, "; ".join(bad) if bad else _t(
        "{n} passend zum Datei-Hash (Signatur: openssl ts -verify, Befehl im Bericht)", n=ok))


def check_case(case: Case, cancel: CancelToken | None = None) -> CaseCheck:
    """Ganze Fallakte prüfen und das Ergebnis protokollieren."""
    cancel = cancel or CancelToken()
    res = CaseCheck()
    _log_chain(case, res)
    manifest = _manifest(case, res)
    _outputs(case, manifest, res, cancel)
    _zip_sidecars(case, res)
    _mails(case, res, cancel)
    _timestamps(case, res)
    case.log("INFO" if res.ok else "ERROR", "case_check",
             {"ok": res.ok, "items": [{"name": i.name, "ok": i.ok, "detail": i.detail} for i in res.items]})
    return res


def check_text(res: CaseCheck) -> str:
    sym = {True: "✓", False: "✗", None: "–"}
    return "\n".join(f"{sym[i.ok]} {i.name}: {i.detail}" for i in res.items)


__all__ = ["CaseCheck", "CheckItem", "check_case", "check_text"]
