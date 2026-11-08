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


def collect(case: Case, account_id: int | None = None) -> dict:
    """Alle Berichtsdaten aus der Case-Datei; mit ``account_id`` nur die eines Kontos."""
    from fed.core.acquisition import accounts_overview
    from fed.core.finalize import message_rows

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
    accounts = _q(case, "SELECT * FROM account ORDER BY id")
    overview = accounts_overview(case)
    attempts = _q(case, "SELECT t.*, a.email FROM attempt t JOIN account a ON a.id = t.account_id ORDER BY t.id")
    folders = _q(case, "SELECT f.*, a.email FROM folder f JOIN account a ON a.id = f.account_id "
                       "ORDER BY f.account_id, f.id")
    issues = _q(case, "SELECT l.ts_utc, l.level, l.event, l.details, t.account_id FROM log l "
                      "LEFT JOIN attempt t ON t.id = l.attempt_id WHERE l.level IN ('WARNING','ERROR') ORDER BY l.id")
    messages = message_rows(case)
    outputs = _q(case, "SELECT * FROM output ORDER BY id")
    dkim_counts = {}
    for acc, res, n in case.conn.execute(
            "SELECT f.account_id, m.dkim_result, COUNT(*) FROM message m JOIN folder f ON f.id = m.folder_id "
            "WHERE m.status = 'valid' GROUP BY f.account_id, m.dkim_result"):
        dkim_counts.setdefault(acc, {})[res or "unchecked"] = n
    dkim_issues = _q(case, "SELECT f.account_id, a.evidence_number, f.display_name, m.uid, m.subject, m.dkim_result, "
                           "m.dkim_domain, m.dkim_detail FROM message m JOIN folder f ON f.id = m.folder_id "
                           "JOIN account a ON a.id = f.account_id WHERE m.status = 'valid' "
                           "AND m.dkim_result IN ('fail', 'permerror', 'temperror') ORDER BY a.id, m.zip_path")
    dns_rows = _q(case, "SELECT * FROM dns_record ORDER BY name, id")
    gmail = {}
    for acc, total, dup_ids, dups in case.conn.execute(
            "SELECT account_id, SUM(n), SUM(n > 1), SUM(CASE WHEN n > 1 THEN n - 1 ELSE 0 END) FROM ("
            " SELECT f.account_id, m.gm_msgid, COUNT(*) n FROM message m JOIN folder f ON f.id = m.folder_id "
            " WHERE m.status = 'valid' AND m.gm_msgid IS NOT NULL GROUP BY f.account_id, m.gm_msgid) GROUP BY account_id"):
        gmail[acc] = {"messages": total, "dup_ids": dup_ids, "dups": dups}
    timestamps = _timestamp_state(case)
    all_accounts = len(accounts)
    if account_id is not None:
        email = next(a["email"] for a in accounts if a["id"] == account_id)
        accounts = [a for a in accounts if a["id"] == account_id]
        overview = [o for o in overview if o["id"] == account_id]
        attempts = [a for a in attempts if a["account_id"] == account_id]
        folders = [f for f in folders if f["account_id"] == account_id]
        issues = [x for x in issues if x["account_id"] == account_id or _details_account(x["details"]) == account_id]
        messages = [m for m in messages if m["Konto"] == email]
        outputs = [o for o in outputs if o.get("account_id") in (None, account_id)]
        timestamps = [x for x in timestamps if x.get("account_id") in (None, account_id)]
        dkim_issues = [x for x in dkim_issues if x["account_id"] == account_id]
        used = {r[0] for r in case.conn.execute(
            "SELECT DISTINCT LOWER(m.dkim_selector || '._domainkey.' || m.dkim_domain) FROM message m "
            "JOIN folder f ON f.id = m.folder_id WHERE f.account_id = ? AND m.dkim_domain IS NOT NULL", (account_id,))}
        dns_rows = [r for r in dns_rows if r["name"] in used]
    return {
        "info": info,
        "agency": agency,
        "logo": snap[0] if snap else {},
        "system": _q(case, "SELECT * FROM system_check ORDER BY id DESC LIMIT 1"),
        "accounts": accounts,
        "overview": overview,
        "account_id": account_id,
        "all_accounts": all_accounts,
        "attempts": attempts,
        "folders": folders,
        "issues": issues,
        "outputs": outputs,
        "dkim_counts": dkim_counts,
        "gmail": gmail,
        "dkim_issues": dkim_issues,
        "dns": dns_rows,
        "zip_mode": case.zip_mode(),
        "log": {"ok": ok, "bad": bad, "count": log_count, "head": head},
        "messages": messages,
        "work_dir": _work_dir_state(case),
        "timestamps": timestamps,
    }


def _details_account(details: str) -> int | None:
    try:
        d = json.loads(details)
    except (ValueError, TypeError):
        return None
    return d.get("account_id") if isinstance(d, dict) else None


def _timestamp_state(case: Case) -> list[dict]:
    """Zeitstempel je ZIP (Ergebnis der letzten Anfrage je ZIP-Datei)."""
    out: dict[str, dict] = {}
    for event, details in case.conn.execute("SELECT event, details FROM log WHERE event IN "
                                            "('timestamp_done', 'timestamp_failed') ORDER BY id"):
        d = {"ok": event == "timestamp_done", **json.loads(details)}
        key = d.get("file", "").removesuffix(".tsr") or d.get("zip", "") or str(len(out))
        out[key] = d
    return list(out.values())


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


def _head_sections(d: dict, L: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """Case und System (Anfang des Berichts)."""
    i = d["info"]
    yn = {1: L["yes"], 0: L["no"], None: L["unknown"]}
    case_items = [
        (L["case_number"], i["case_number"]), (L["examiner"], i["examiner"]),
        (L["created"], tf.both(i["created_utc"])), (L["closed"], tf.both(i["closed_utc"]) if i["closed_utc"] else "–"),
        (L["tool"], f"{APP_NAME} {i['tool_version']}"), (L["tool_sha"], i["tool_sha256"] or "–"),
        (L["accounts_count"], str(d["all_accounts"])),
    ]
    if d["all_accounts"] > 1:
        mode = d.get("zip_mode") or "gesamt"
        case_items.append((L["zip_mode"], L[f"zip_mode_{mode}"]))
    if d.get("work_dir"):
        case_items.append((L["work_dir"], _work_dir_text(d["work_dir"], L)))
    secs = [(L["case"], case_items)]
    if d["system"]:
        s = d["system"][0]
        secs.append((L["system"], [
            (L["user"], s["user"]), (L["host"], s["hostname"]), (L["os"], s["os"]),
            (L["time"], f"{tf.utc_str(s['utc_time'])} / {s['local_time'].replace('T', ' ')} ({s['tz']})"),
            (L["ntp"], f"{s['ntp_offset_s']:+.3f} s" if s["ntp_offset_s"] is not None else L["unknown"]),
            (L["online"], yn.get(s["online"], "?")), (L["target"], s["target_path"]),
            (L["removable"], yn.get(s["target_removable"], "?")), (L["free"], _fmt_bytes(s["free_bytes"])),
        ]))
    return secs


def _state_text(o: dict, L: dict) -> str:
    text = L.get(f"st_{o['state']}", o["state"])
    return text + (f" ({L['partial']})" if o["date_filter"] else "")


def _overview_rows(d: dict, L: dict) -> list[list[str]]:
    return [[o["evidence"], o["email"], _state_text(o, L), f"{o['saved']} / {o['expected']}", _fmt_bytes(o["bytes"]),
             str(o["attempts"])] for o in d["overview"]]


def _account_items(a: dict, L: dict, dkim_counts: dict | None = None) -> list[tuple[str, str]]:
    rng = "–"
    if a["date_from"] or a["date_to"]:
        rng = f"{a['date_from'] or '…'} – {a['date_to'] or '…'} ({L['partial']})"
    return [
        (L["evidence"], a["evidence_number"]), (L["server"], f"{a['server']}:{a['port']} ({a['security']})"),
        (L["username"], a["username"]),
        (L["auth"], L.get(f"auth_{a['auth_method']}", a["auth_method"])), (L["source"], a["autoconfig_source"] or "–"),
        (L["cert"], a["tls_cert_sha256"] or "–"), (L["server_id"], a["server_id"] or "–"), (L["range"], rng),
        (L["dkim"], _dkim_summary((dkim_counts or {}).get(a["id"], {}), L)),
    ]


def _gmail_items(a: dict, d: dict, L: dict) -> list[tuple[str, str]]:
    g = d.get("gmail", {}).get(a["id"])
    if not g:
        return []
    text = L["gmail_text"].format(n=g["messages"])
    if g["dups"]:
        text += " · " + L["gmail_dups"].format(n=g["dups"], ids=g["dup_ids"])
    return [(L["gmail"], text)]


def _dkim_summary(counts: dict, L: dict) -> str:
    if not counts:
        return "–"
    order = ("pass", "fail", "permerror", "temperror", "none", "unchecked")
    return " · ".join(f"{counts[k]} {L['dkim_' + k]}" for k in order if counts.get(k))


def _tail_sections(d: dict, L: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """Zeitstempel und Log-Kette (Ende des Berichts)."""
    secs = []
    many = len(d.get("timestamps", [])) > 1
    for ts in d.get("timestamps", []):
        title = L["ts"] + (f" – {ts.get('file', '').removesuffix('.tsr') or ts.get('zip', '')}" if many else "")
        if not ts["ok"]:
            secs.append((title, [(L["status"], L["ts_failed"]), (L["ts_tsa"], ts["tsa"]), ("", ts["error"])]))
            continue
        zip_name = ts["file"].removesuffix(".tsr")
        secs.append((title, [
            (L["ts_file"], ts["file"]), (L["ts_tsa"], ts["tsa"]), (L["ts_time"], tf.both(ts["gen_time"])),
            (L["ts_serial"], ts["serial"]), (L["ts_policy"], ts["policy"]), (L["ts_acc"], ts["accuracy"] or "–"),
            (L["ts_hash"], ts["sha256"]), (L["ts_offset"], f"{ts['local_minus_tsa_s']:+.3f} s"),
            (L["ts_verify"], L["ts_verify_cmd"].format(tsr=ts["file"], zip=zip_name)),
        ]))
    lg = d["log"]
    secs.append((L["logchain"], [
        (L["status"], L["ok"] if lg["ok"] else f"{L['broken']} (#{lg['bad']})"),
        (L["entries"], str(lg["count"])), (L["head"], lg["head"] or "–"),
    ]))
    return secs


def _dkim_issue_rows(d: dict, L: dict) -> list[list[str]]:
    return [[x["evidence_number"], f"{x['display_name']} · UID {x['uid']}", x["subject"] or "–",
             L["dkim_" + x["dkim_result"]], f"{x['dkim_domain'] or ''} {x['dkim_detail'] or ''}".strip()]
            for x in d["dkim_issues"]]


def _dns_rows(d: dict, L: dict) -> list[list[str]]:
    return [[r["name"], {"ok": L["dns_ok"], "nxdomain": L["dns_missing"], "error": L["dns_error"]}.get(r["status"],
                                                                                                    r["status"]),
             tf.both_short(r["fetched_utc"]), r["sha256"] or "–"] for r in d["dns"]]


def _title(d: dict, L: dict) -> str:
    if d["account_id"] is None:
        return L["title"]
    a = d["accounts"][0]
    return f"{L['title_account']} {a['evidence_number']} · {a['email']}"


# ---------------------------------------------------------------- TXT
def _kv_txt(items: list[tuple[str, str]], indent: str = "") -> list[str]:
    w = max((len(k) for k, _ in items), default=0)
    return [f"{indent}{k:<{w}}  {v}" for k, v in items]


def _folder_lines(d: dict, L: dict, email: str) -> list[str]:
    out = []
    for f in (x for x in folder_tree(d["folders"]) if x["email"] == email):
        diff = (f["count_saved"] or 0) - (f["count_server"] or 0) if f["selected"] else ""
        name = "  " + "  " * f["depth"] + ("└ " if f["depth"] else "") + f["display_name"].split(f["delimiter"] or "\0")[-1]
        out.append(f"{name:<36} {'[x]' if f['selected'] else '[ ]'}  {L['server_count']} {f['count_server'] or 0:>6}  "
                   f"{L['saved']} {f['count_saved'] or 0:>6}  {L['diff']} {diff!s:>4}  "
                   f"{_fmt_bytes(f['size_server']):>9}  {_status_pair(f, L)}")
    return out


def render_txt(case: Case, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500,
               account_id: int | None = None) -> str:
    L = labels(language)
    d = collect(case, account_id)
    out = []
    ag = d["agency"]
    if ag.get("name"):
        out += [ag["name"], ag.get("adresse", ""), ""]
    title = _title(d, L)
    out += [title, "=" * len(title), ""]
    if account_id is not None:
        out += [L["part_of"].format(case=d["info"]["case_number"], n=d["all_accounts"]), ""]
    if d["info"]["notes"]:
        out += [L["case_notes"], "-" * len(L["case_notes"]), d["info"]["notes"].strip(), ""]
    for title, items in _head_sections(d, L):
        out += [title, "-" * len(title), *_kv_txt(items), ""]
    if account_id is None:
        out += [L["overview"], "-" * len(L["overview"])]
        rows = [[L["evidence"], L["account"], L["status"], f"{L['saved']} / {L['server_count']}", L["size"],
                 L["attempts"]], *_overview_rows(d, L)]
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        out += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
        tot = d["overview"]
        out += ["", L["overview_sum"].format(n=len(tot), done=sum(o["state"] == "done" for o in tot),
                                             mails=sum(o["saved"] for o in tot),
                                             size=_fmt_bytes(sum(o["bytes"] for o in tot))), ""]
    for a in d["accounts"]:
        head = f"{L['account']} {a['evidence_number']} · {a['email']}"
        out += [head, "=" * len(head), *_kv_txt(_account_items(a, L, d["dkim_counts"]) + _gmail_items(a, d, L))]
        if a.get("notes"):
            out += ["", f"{L['account_notes']}:", *("  " + x for x in a["notes"].splitlines())]
        out += ["", f"{L['attempts']}:"]
        for x in (x for x in d["attempts"] if x["account_id"] == a["id"]):
            out.append(f"  #{x['nr']}  {x['mode']:<8} {x['status']:<10} {tf.both(x['started_utc'])} – "
                       f"{tf.both(x['ended_utc']) if x['ended_utc'] else '…'}  {x['reason'] or ''}"
                       f"{'  -> ' + x['moved_to'] if x['moved_to'] else ''}")
        out += ["", f"{L['folders']}:", *_folder_lines(d, L, a["email"]), ""]
    out += [L["dkim_section"], "-" * len(L["dkim_section"]), L["dkim_intro"], ""]
    out += [f"  {r[0]}  {r[1]}  {r[2]}  [{r[3]}]  {r[4]}" for r in _dkim_issue_rows(d, L)] or [f"  {L['dkim_none_issues']}"]
    if d["dns"]:
        out += ["", f"{L['dns_section']}:", f"  {L['dns_intro']}"]
        out += [f"  {r[0]}  {r[1]}  {r[2].replace(chr(10), ' / ')}\n    SHA-256 {r[3]}" for r in _dns_rows(d, L)]
    out.append("")
    for title, items in _tail_sections(d, L):
        out += [title, "-" * len(title), *_kv_txt(items), ""]
    out += [L["issues"], "-" * len(L["issues"])]
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
        acc = f"{m['Evidence']}  " if d["all_accounts"] > 1 and account_id is None else ""
        out.append(f"{n:>5}  {acc}{tf.both(m['Eingangszeit_UTC'])}  {m['Ordner']}  UID {m['UID']}  {m['Absender']}  "
                   f"{L['to']}: {m['An'] or m['CC'] or m['Empfaenger_Hinweis']}  {L['att']} {m['Anlagen']}  "
                   f"DKIM {L.get('dkim_' + m['DKIM'], '–') if m['DKIM'] else '–'}  "
                   + (f"Labels: {m['Gmail_Labels']}  " if m["Gmail_Labels"] else "")
                   + f"{m['Betreff']}\n       {m['Datei']}\n       SHA-256 {m['SHA256']}")
    out.append("")
    return "\n".join(out)


def write_txt(case: Case, path: Path, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500,
              account_id: int | None = None) -> None:
    path.write_text(render_txt(case, language, mail_list, mail_list_max, account_id), encoding="utf-8")


# ---------------------------------------------------------------- PDF
def write_pdf(case: Case, path: Path, language: str = "de", mail_list: str = "auto", mail_list_max: int = 500,
              account_id: int | None = None) -> None:
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    L = labels(language)
    d = collect(case, account_id)
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
            self.cell(85, 4, ag.get("adresse", "").replace("\n", " · "), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_x(110)
            sub = f"{L['case_number']} {info['case_number']}"
            if account_id is not None:
                sub += f" · {L['evidence']} {d['accounts'][0]['evidence_number']}"
            self.cell(85, 4, sub, align="R")
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
    pdf.set_title(f"{_title(d, L)} – {info['case_number']}")
    pdf.set_creator(f"{APP_NAME} {__version__}")
    pdf.add_page()
    pdf.set_font("DejaVu", "B", 16)
    pdf.multi_cell(0, 9, _title(d, L), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    head = FontFace(emphasis="BOLD", fill_color=(234, 240, 252))
    note_bg = (247, 248, 250)

    def h2(text):
        pdf.ln(3)
        if pdf.get_y() > 250:
            pdf.add_page()
        pdf.set_font("DejaVu", "B", 11)
        pdf.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)

    def h3(text):
        pdf.ln(1.5)
        pdf.set_font("DejaVu", "B", 9)
        pdf.cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)

    def kv(items):
        with pdf.table(col_widths=(55, 125), first_row_as_headings=False, line_height=4.6,
                       borders_layout="HORIZONTAL_LINES") as t:
            for k, v in items:
                r = t.row()
                r.cell(k)
                r.cell(str(v))

    def note(text):
        pdf.set_font("DejaVu", "", 9)
        pdf.set_fill_color(*note_bg)
        pdf.multi_cell(0, 5, text.strip(), fill=True, padding=2, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)

    if account_id is not None:
        pdf.set_font("DejaVu", "", 9)
        pdf.multi_cell(0, 5, L["part_of"].format(case=info["case_number"], n=d["all_accounts"]),
                       new_x="LMARGIN", new_y="NEXT")
    if info["notes"]:
        h2(L["case_notes"])
        note(info["notes"])

    for title, items in _head_sections(d, L):
        h2(title)
        kv(items)

    if account_id is None:
        h2(L["overview"])
        with pdf.table(col_widths=(22, 50, 42, 28, 18, 20), headings_style=head, line_height=4.6,
                       text_align=("LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT")) as t:
            t.row([L["evidence"], L["account"], L["status"], f"{L['saved']} / {L['server_count']}", L["size"],
                   L["attempts"]])
            for r in _overview_rows(d, L):
                t.row(r)
        tot = d["overview"]
        pdf.ln(1)
        pdf.multi_cell(0, 4.6, L["overview_sum"].format(n=len(tot), done=sum(o["state"] == "done" for o in tot),
                                                        mails=sum(o["saved"] for o in tot),
                                                        size=_fmt_bytes(sum(o["bytes"] for o in tot))),
                       new_x="LMARGIN", new_y="NEXT")

    tree = folder_tree(d["folders"])
    for a in d["accounts"]:
        if account_id is None:
            pdf.add_page()
        h2(f"{L['account']} {a['evidence_number']} · {a['email']}")
        kv(_account_items(a, L, d["dkim_counts"]) + _gmail_items(a, d, L))
        if a.get("notes"):
            h3(L["account_notes"])
            note(a["notes"])
        h3(L["attempts"])
        with pdf.table(col_widths=(10, 20, 18, 44, 44, 44), headings_style=head, line_height=4.4) as t:
            t.row([L["nr"], L["mode"], L["status"], L["start"], L["end"], L["reason"]])
            for x in (x for x in d["attempts"] if x["account_id"] == a["id"]):
                t.row([str(x["nr"]), x["mode"], x["status"], tf.both_short(x["started_utc"]),
                       tf.both_short(x["ended_utc"]) if x["ended_utc"] else "…",
                       (x["reason"] or "") + (f" -> {x['moved_to']}" if x["moved_to"] else "")])
        h3(L["folders"])
        with pdf.table(col_widths=(46, 17, 15, 18, 19, 18, 47), headings_style=head, line_height=4.4,
                       text_align=("LEFT", "CENTER", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT")) as t:
            t.row([L["folders"], L["selected"], L["server_count"], L["saved"], L["diff"], L["size"],
                   L["before_after"]])
            for f in (x for x in tree if x["email"] == a["email"]):
                diff = str((f["count_saved"] or 0) - (f["count_server"] or 0)) if f["selected"] else ""
                t.row([f["label"], "✓" if f["selected"] else "–", str(f["count_server"] or 0),
                       str(f["count_saved"] or 0), diff, _fmt_bytes(f["size_server"]), _status_pair(f, L)])

    h2(L["dkim_section"])
    pdf.set_font("DejaVu", "", 8)
    pdf.multi_cell(0, 4.2, L["dkim_intro"], new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    issues = _dkim_issue_rows(d, L)
    if issues:
        with pdf.table(col_widths=(18, 40, 46, 28, 48), headings_style=head, line_height=4.2) as t:
            t.row([L["evidence"], L["mail"], L["subject"], L["result"], L["detail"]])
            for r in issues:
                t.row([r[0], r[1], r[2], r[3], r[4][:300]])
    else:
        pdf.cell(0, 5, L["dkim_none_issues"], new_x="LMARGIN", new_y="NEXT")
    if d["dns"]:
        h2(L["dns_section"])
        pdf.set_font("DejaVu", "", 8)
        pdf.multi_cell(0, 4.2, L["dns_intro"], new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        with pdf.table(col_widths=(55, 25, 40, 60), headings_style=head, line_height=4.2) as t:
            t.row([L["dns_name"], L["status"], L["fetched"], "SHA-256"])
            for r in _dns_rows(d, L):
                t.row(r)

    for title, items in _tail_sections(d, L):
        h2(title)
        kv(items)

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
    multi = d["all_accounts"] > 1 and account_id is None
    with pdf.table(col_widths=(8, 30, 40, 102), headings_style=head, line_height=3.3) as t:
        t.row([L["nr"], f"{L['received']} / UID", (f"{L['evidence']} / " if multi else "") + f"{L['folders']} / {L['from']}",
               f"{L['subject']} · {L['to']} · {L['att']} / {L['file']} / SHA-256"])
        for n, m in enumerate(d["messages"], 1):
            to = m["An"] or (f"CC: {m['CC']}" if m["CC"] else m["Empfaenger_Hinweis"] or "–")
            t.row([str(n), f"{tf.both_short(m['Eingangszeit_UTC'])}\nUID {m['UID']}",
                   (f"{m['Evidence']} · " if multi else "") + f"{m['Ordner']}\n{m['Absender']}",
                   f"{m['Betreff']} · {to} · {L['att']} {m['Anlagen']}"
                   + (f" · DKIM {L.get('dkim_' + m['DKIM'], m['DKIM'])}" if m["DKIM"] else "")
                   + (f" · Labels: {m['Gmail_Labels']}" if m["Gmail_Labels"] else "")
                   + f"\n{m['Datei']}\n{m['SHA256']}"])
    pdf.output(str(path))


def account_report_name(evidence: str, email: str, suffix: str) -> str:
    """Dateiname des Einzelberichts, z. B. ``report_A-01.pdf``."""
    from fed.core.naming import sanitize_component

    return f"report_{sanitize_component(evidence or email)}{suffix}"


def write_reports(case: Case, language: str = "de", mail_list: str = "auto",
                  mail_list_max: int = 500) -> tuple[Path, Path]:
    """Gesamtbericht ``report.txt``/``report.pdf`` und – bei mehreren Konten – je Konto
    ``report_<Evidence>.txt``/``.pdf``. Rückgabe: der Gesamtbericht."""
    txt, pdfp = case.root / "report.txt", case.root / "report.pdf"
    write_txt(case, txt, language, mail_list, mail_list_max)
    write_pdf(case, pdfp, language, mail_list, mail_list_max)
    accounts = case.conn.execute("SELECT id, evidence_number, email FROM account ORDER BY id").fetchall()
    if len(accounts) > 1:
        for acc_id, evidence, email in accounts:
            write_txt(case, case.root / account_report_name(evidence, email, ".txt"), language, mail_list,
                      mail_list_max, acc_id)
            write_pdf(case, case.root / account_report_name(evidence, email, ".pdf"), language, mail_list,
                      mail_list_max, acc_id)
    return txt, pdfp


def account_reports(case: Case) -> list[Path]:
    """Vorhandene Einzelberichte je Konto."""
    return sorted(p for p in case.root.glob("report_*.*") if p.suffix in (".pdf", ".txt"))
