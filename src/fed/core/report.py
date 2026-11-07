"""TXT- und PDF-Bericht aus der Case-Datei.

Beide Berichte haben denselben Inhalt; das PDF trägt das Agentur-Logo aus der
Case-Datei (BLOB, nicht die aktuelle Config) und eine eingebettete Unicode-Schrift.
"""

from __future__ import annotations

import io
import json
import tomllib
from importlib import resources
from pathlib import Path

from fed import APP_NAME, __version__
from fed.core import timefmt as tf
from fed.core.case import Case

def _load_labels() -> dict[str, dict[str, str]]:
    """Berichtstexte je Sprache aus ``fed/i18n/report_<sprache>.json``."""
    out = {}
    for f in (resources.files("fed") / "i18n").iterdir():
        if f.name.startswith("report_") and f.name.endswith(".json"):
            out[f.name[7:-5]] = json.loads(f.read_text(encoding="utf-8"))
    return out


LABELS = _load_labels()


def labels(language: str) -> dict[str, str]:
    """Texte einer Berichtssprache; fehlt die Sprache oder ein Text, gilt Englisch."""
    return {**LABELS["en"], **LABELS.get(language, {})}


def _q(case: Case, sql: str, args=()) -> list[dict]:
    cur = case.conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur]


def collect(case: Case) -> dict:
    """Alle Berichtsdaten aus der Case-Datei."""
    info = case.info()
    snap = _q(case, "SELECT toml_text, logo_file, logo_sha256 FROM config_snapshot WHERE id = 1")
    agency = {}
    if snap:
        try:
            agency = tomllib.loads(snap[0]["toml_text"]).get("agentur", {})
        except tomllib.TOMLDecodeError:
            agency = {}
    ok, bad = case.verify_log()
    log_count, head = case.conn.execute(
        "SELECT COUNT(*), (SELECT entry_hash FROM log ORDER BY id DESC LIMIT 1) FROM log").fetchone()
    from fed.core.finalize import message_rows

    return {
        "info": info,
        "agency": agency,
        "logo": snap[0] if snap else {},
        "system": _q(case, "SELECT * FROM system_check ORDER BY id DESC LIMIT 1"),
        "accounts": _q(case, "SELECT * FROM account ORDER BY id"),
        "attempts": _q(case, "SELECT t.*, a.email FROM attempt t JOIN account a ON a.id = t.account_id ORDER BY t.id"),
        "folders": _q(case, "SELECT f.*, a.email FROM folder f JOIN account a ON a.id = f.account_id "
                            "ORDER BY f.account_id, f.id"),
        "issues": _q(case, "SELECT ts_utc, level, event, details FROM log WHERE level IN ('WARNING','ERROR') "
                           "ORDER BY id"),
        "outputs": _q(case, "SELECT * FROM output ORDER BY id"),
        "log": {"ok": ok, "bad": bad, "count": log_count, "head": head},
        "messages": message_rows(case),
        "work_dir": _work_dir_state(case),
        "timestamp": _timestamp_state(case),
    }


def _timestamp_state(case: Case) -> dict:
    row = case.conn.execute("SELECT event, details FROM log WHERE event IN ('timestamp_done', 'timestamp_failed') "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    return {"ok": row[0] == "timestamp_done", **json.loads(row[1])}


def _work_dir_state(case: Case) -> dict:
    row = case.conn.execute("SELECT event, details, ts_utc FROM log WHERE event IN ('work_dir_deleted', 'work_dir_kept') "
                            "ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {}
    return {"deleted": row[0] == "work_dir_deleted", "ts": row[2], **json.loads(row[1] or "{}")}


def _work_dir_text(w: dict, L: dict) -> str:
    if not w.get("deleted"):
        return L["wd_kept"]
    return L["wd_deleted"].format(files=w["files"], size=_fmt_bytes(w["bytes"])) + f" · {tf.both(w['ts'])}"


def include_mail_list(count: int, mode: str = "auto", limit: int = 500) -> bool:
    """Inhalt des Berichts: ``liste`` (Ordnerstruktur + Mail-Liste), ``ordner`` (nur Ordnerstruktur
    mit Anzahl, Liste in log.csv) oder ``auto`` (Liste nur bis ``limit`` Mails)."""
    mode = {"immer": "liste", "nie": "ordner"}.get(mode, mode)
    if mode == "liste":
        return True
    if mode == "ordner":
        return False
    return count <= limit


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "–"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def folder_tree(folders: list[dict]) -> list[dict]:
    """Ordner je Konto hierarchisch sortiert, mit eingerückter Bezeichnung (``label``) und Tiefe."""
    def key(f):
        parts = f["display_name"].split(f["delimiter"]) if f["delimiter"] else [f["display_name"]]
        return (f["email"], [(p.upper() != "INBOX" if i == 0 else True, p.lower()) for i, p in enumerate(parts)])

    out = []
    for f in sorted(folders, key=key):
        parts = f["display_name"].split(f["delimiter"]) if f["delimiter"] else [f["display_name"]]
        depth = len(parts) - 1
        out.append({**f, "depth": depth, "label": ("\u2003" * depth) + ("\u2514 " if depth else "") + parts[-1]})
    return out


def _status_pair(f: dict, L: dict) -> str:
    if not f.get("status_before"):
        return "–"
    b, a = json.loads(f["status_before"]), json.loads(f["status_after"] or "{}")
    return L["unchanged"] if b == a else f"{L['changed']}: {b} -> {a}"


def _sections(d: dict, L: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """Gemeinsame Schlüssel/Wert-Abschnitte für TXT und PDF."""
    i = d["info"]
    yn = {1: L["yes"], 0: L["no"], None: L["unknown"]}
    secs = [(L["case"], [
        (L["case_number"], i["case_number"]), (L["examiner"], i["examiner"]), (L["notes"], i["notes"] or "–"),
        (L["created"], tf.both(i["created_utc"])), (L["closed"], tf.both(i["closed_utc"]) if i["closed_utc"] else "–"),
        (L["tool"], f"{APP_NAME} {i['tool_version']}"), (L["tool_sha"], i["tool_sha256"] or "–"),
    ])]
    if d.get("work_dir"):
        secs[0][1].append((L["work_dir"], _work_dir_text(d["work_dir"], L)))
    if d["system"]:
        s = d["system"][0]
        secs.append((L["system"], [
            (L["user"], s["user"]), (L["host"], s["hostname"]), (L["os"], s["os"]),
            (L["time"], f"{tf.utc_str(s['utc_time'])} / {s['local_time'].replace('T', ' ')} ({s['tz']})"),
            (L["ntp"], f"{s['ntp_offset_s']:+.3f} s" if s["ntp_offset_s"] is not None else L["unknown"]),
            (L["online"], yn.get(s["online"], "?")), (L["target"], s["target_path"]),
            (L["removable"], yn.get(s["target_removable"], "?")), (L["free"], _fmt_bytes(s["free_bytes"])),
        ]))
    for a in d["accounts"]:
        rng = "–"
        if a["date_from"] or a["date_to"]:
            rng = f"{a['date_from'] or '…'} – {a['date_to'] or '…'} ({L['partial']})"
        secs.append((f"{L['account']} {a['email']}", [
            (L["evidence"], a["evidence_number"]), (L["server"], f"{a['server']}:{a['port']} ({a['security']})"),
            (L["auth"], L.get(f"auth_{a['auth_method']}", a["auth_method"])), (L["source"], a["autoconfig_source"] or "–"),
            (L["cert"], a["tls_cert_sha256"] or "–"), (L["server_id"], a["server_id"] or "–"), (L["range"], rng),
        ]))
    ts = d.get("timestamp")
    if ts and ts["ok"]:
        zip_name = ts["file"].removesuffix(".tsr")
        secs.append((L["ts"], [
            (L["ts_file"], ts["file"]), (L["ts_tsa"], ts["tsa"]), (L["ts_time"], tf.both(ts["gen_time"])),
            (L["ts_serial"], ts["serial"]), (L["ts_policy"], ts["policy"]), (L["ts_acc"], ts["accuracy"] or "–"),
            (L["ts_hash"], ts["sha256"]), (L["ts_offset"], f"{ts['local_minus_tsa_s']:+.3f} s"),
            (L["ts_verify"], L["ts_verify_cmd"].format(tsr=ts["file"], zip=zip_name)),
        ]))
    elif ts:
        secs.append((L["ts"], [(L["status"], L["ts_failed"]), (L["ts_tsa"], ts["tsa"]), ("", ts["error"])]))
    lg = d["log"]
    secs.append((L["logchain"], [
        (L["status"], L["ok"] if lg["ok"] else f"{L['broken']} (#{lg['bad']})"),
        (L["entries"], str(lg["count"])), (L["head"], lg["head"] or "–"),
    ]))
    return secs


# ---------------------------------------------------------------- TXT
def render_txt(case: Case, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500) -> str:
    L = labels(language)
    d = collect(case)
    out = []
    ag = d["agency"]
    if ag.get("name"):
        out += [ag["name"], ag.get("adresse", ""), ""]
    out += [L["title"], "=" * len(L["title"]), ""]
    for title, items in _sections(d, L):
        out += [title, "-" * len(title)]
        w = max(len(k) for k, _ in items)
        out += [f"{k:<{w}}  {v}" for k, v in items] + [""]
    out += [L["attempts"], "-" * len(L["attempts"])]
    for t in d["attempts"]:
        out.append(f"{t['email']} #{t['nr']}  {t['mode']:<8} {t['status']:<10} {tf.both(t['started_utc'])} – "
                   f"{tf.both(t['ended_utc']) if t['ended_utc'] else '…'}  {t['reason'] or ''}"
                   f"{'  -> ' + t['moved_to'] if t['moved_to'] else ''}")
    out += ["", L["folders"], "-" * len(L["folders"])]
    last = None
    for f in folder_tree(d["folders"]):
        if f["email"] != last:
            out.append(f["email"])
            last = f["email"]
        diff = (f["count_saved"] or 0) - (f["count_server"] or 0) if f["selected"] else ""
        name = "  " + "  " * f["depth"] + ("└ " if f["depth"] else "") + f["display_name"].split(f["delimiter"] or "\0")[-1]
        out.append(f"{name:<36} {'[x]' if f['selected'] else '[ ]'}  {L['server_count']} {f['count_server'] or 0:>6}  "
                   f"{L['saved']} {f['count_saved'] or 0:>6}  {L['diff']} {diff!s:>4}  "
                   f"{_fmt_bytes(f['size_server']):>9}  {_status_pair(f, L)}")
    out += ["", L["issues"], "-" * len(L["issues"])]
    out += [f"{tf.both(x['ts_utc'])} {x['level']:<7} {x['event']}: {x['details']}" for x in d["issues"]] or [L["none"]]
    out += ["", L["outputs"], "-" * len(L["outputs"])]
    for o in d["outputs"]:
        out += [f"{o['file_name']}  ({_fmt_bytes(o['size'])})", f"  SHA-256  {o['sha256']}"]
        if o["md5"]:
            out.append(f"  MD5      {o['md5']}")
        if o["sha512"]:
            out.append(f"  SHA-512  {o['sha512']}")
    out += ["", f"{L['mails']} ({len(d['messages'])})", "-" * 20]
    if not include_mail_list(len(d["messages"]), mail_list, mail_list_max):
        out.append(L["list_omitted"].format(n=len(d["messages"])))
        out.append("")
        return "\n".join(out)
    for n, m in enumerate(d["messages"], 1):
        out.append(f"{n:>5}  {tf.both(m['Eingangszeit_UTC'])}  {m['Ordner']}  UID {m['UID']}  {m['Absender']}  "
                   f"{L['to']}: {m['An'] or m['CC'] or m['Empfaenger_Hinweis']}  {L['att']} {m['Anlagen']}  "
                   f"{m['Betreff']}\n       {m['Datei']}\n       SHA-256 {m['SHA256']}")
    out.append("")
    return "\n".join(out)


def write_txt(case: Case, path: Path, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500) -> None:
    path.write_text(render_txt(case, language, mail_list, mail_list_max), encoding="utf-8")


# ---------------------------------------------------------------- PDF
def write_pdf(case: Case, path: Path, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500) -> None:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    L = labels(language)
    d = collect(case)
    fonts = resources.files("fed.resources") / "fonts"
    logo = case.logo()
    ag = d["agency"]
    info = d["info"]

    class Report(FPDF):
        def header(self):
            if logo:
                self.image(io.BytesIO(logo[0]), x=15, y=10, h=16, keep_aspect_ratio=True, w=40)
            self.set_font("DejaVu", "B", 10)
            self.set_xy(110, 10)
            self.cell(85, 5, ag.get("name", ""), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_font("DejaVu", "", 8)
            self.set_x(110)
            self.cell(85, 4, ag.get("adresse", ""), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_x(110)
            self.cell(85, 4, f"{L['case_number']} {info['case_number']}", align="R")
            self.set_draw_color(200, 205, 214)
            self.line(15, 29, 195, 29)
            self.set_y(33)

        def footer(self):
            self.set_y(-12)
            self.set_font("DejaVu", "", 7)
            self.set_text_color(90, 98, 112)
            self.cell(0, 5, f"{APP_NAME} {__version__} · {info['case_number']}", align="L")
            self.set_x(15)
            self.cell(0, 5, f"{L['page']} {self.page_no()} {L['of']} {{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = Report(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 18)
    pdf.add_font("DejaVu", "", str(fonts / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(fonts / "DejaVuSans-Bold.ttf"))
    pdf.add_font("Mono", "", str(fonts / "DejaVuSansMono.ttf"))
    pdf.set_title(f"{L['title']} {info['case_number']}")
    pdf.set_creator(f"{APP_NAME} {__version__}")
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 10, L["title"], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    head = FontFace(emphasis="BOLD", fill_color=(234, 240, 252))

    def h2(text):
        pdf.ln(3)
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)

    def kv(items):
        with pdf.table(col_widths=(55, 125), first_row_as_headings=False, line_height=4.6,
                       borders_layout="HORIZONTAL_LINES") as t:
            for k, v in items:
                r = t.row()
                r.cell(k)
                r.cell(str(v))

    for title, items in _sections(d, L):
        h2(title)
        kv(items)

    h2(L["attempts"])
    with pdf.table(col_widths=(10, 20, 18, 44, 44, 44), headings_style=head, line_height=4.4) as t:
        t.row([L["nr"], L["mode"], L["status"], L["start"], L["end"], L["reason"]])
        for a in d["attempts"]:
            t.row([str(a["nr"]), a["mode"], a["status"], tf.both_short(a["started_utc"]),
                   tf.both_short(a["ended_utc"]) if a["ended_utc"] else "…",
                   (a["reason"] or "") + (f" -> {a['moved_to']}" if a["moved_to"] else "")])

    h2(L["folders"])
    tree = folder_tree(d["folders"])
    for email in dict.fromkeys(f["email"] for f in tree):
        pdf.set_font("DejaVu", "B", 8.5)
        pdf.cell(0, 5, email, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)
        with pdf.table(col_widths=(46, 17, 15, 18, 19, 18, 47), headings_style=head, line_height=4.4,
                       text_align=("LEFT", "CENTER", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT")) as t:
            t.row([L["folders"], L["selected"], L["server_count"], L["saved"], L["diff"], L["size"],
                   L["before_after"]])
            for f in (x for x in tree if x["email"] == email):
                diff = str((f["count_saved"] or 0) - (f["count_server"] or 0)) if f["selected"] else ""
                t.row([f["label"], "✓" if f["selected"] else "–", str(f["count_server"] or 0),
                       str(f["count_saved"] or 0), diff, _fmt_bytes(f["size_server"]), _status_pair(f, L)])
        pdf.ln(1)

    h2(L["issues"])
    if d["issues"]:
        with pdf.table(col_widths=(42, 18, 120), headings_style=head, line_height=4.2) as t:
            t.row([L["time_col"], L["status"], ""])
            for x in d["issues"]:
                t.row([tf.both_short(x["ts_utc"]), x["level"], f"{x['event']}: {x['details']}"[:400]])
    else:
        pdf.cell(0, 6, L["none"], new_x="LMARGIN", new_y="NEXT")

    h2(L["outputs"])
    for o in d["outputs"]:
        pdf.set_font("DejaVu", "B", 8.5)
        pdf.cell(0, 5, f"{o['file_name']}  ({_fmt_bytes(o['size'])})", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Mono", "", 7)
        for label, key in (("SHA-256", "sha256"), ("MD5", "md5"), ("SHA-512", "sha512")):
            if o[key]:
                pdf.multi_cell(0, 3.6, f"{label:<8} {o[key]}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
    if logo:
        pdf.set_font("DejaVu", "", 7.5)
        pdf.cell(0, 5, f"Logo SHA-256: {logo[2]}", new_x="LMARGIN", new_y="NEXT")

    h2(f"{L['mails']} ({len(d['messages'])})")
    if not include_mail_list(len(d["messages"]), mail_list, mail_list_max):
        pdf.set_font("DejaVu", "", 8.5)
        pdf.multi_cell(0, 4.6, L["list_omitted"].format(n=len(d["messages"])), new_x="LMARGIN", new_y="NEXT")
        pdf.output(str(path))
        return

    pdf.add_page()
    pdf.set_font("DejaVu", "B", 11)
    pdf.cell(0, 7, f"{L['mails']} ({len(d['messages'])})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 6.5)
    with pdf.table(col_widths=(8, 30, 40, 102), headings_style=head, line_height=3.3) as t:
        t.row([L["nr"], f"{L['received']} / UID", f"{L['folders']} / {L['from']}",
               f"{L['subject']} · {L['to']} · {L['att']} / {L['file']} / SHA-256"])
        for n, m in enumerate(d["messages"], 1):
            to = m["An"] or (f"CC: {m['CC']}" if m["CC"] else m["Empfaenger_Hinweis"] or "–")
            t.row([str(n), f"{tf.both_short(m['Eingangszeit_UTC'])}\nUID {m['UID']}", f"{m['Ordner']}\n{m['Absender']}",
                   f"{m['Betreff']} · {to} · {L['att']} {m['Anlagen']}\n{m['Datei']}\n{m['SHA256']}"])
    pdf.output(str(path))


def write_reports(case: Case, language: str = "de", mail_list: str = "auto",
                  mail_list_max: int = 500) -> tuple[Path, Path]:
    txt, pdfp = case.root / "report.txt", case.root / "report.pdf"
    write_txt(case, txt, language, mail_list, mail_list_max)
    write_pdf(case, pdfp, language, mail_list, mail_list_max)
    return txt, pdfp
