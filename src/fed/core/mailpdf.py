"""Einzelne gesicherte Mails als PDF (PDF/A-3b) – Darstellung zur Lesbarkeit.

Beweismittel bleibt die ``.eml`` im ZIP. Das PDF verweist deshalb eindeutig auf genau diese Datei
(Pfad, SHA-256, IMAP-UID), sagt, was es nicht wiedergibt, und trägt die Original-``.eml`` als
eingebettete Datei (PDF/A-3, Beziehung „Source“) mit. Jeder Export wird mit dem SHA-256 des PDF
im Log des Falls festgehalten; eine Kopie liegt in ``exports/`` im Fall-Ordner.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from importlib import resources
from pathlib import Path
from typing import ClassVar

from fed import APP_NAME, __version__
from fed.core import viewer
from fed.core.case import Case
from fed.core.report import PDFA, _fmt_bytes, labels

EXPORT_DIR = "exports"
_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif"}


class _HtmlText(HTMLParser):
    """HTML einer Mail als lesbarer Text (Absätze, Zeilenumbrüche, Listen, Tabellenzeilen; keine Skripte)."""

    BLOCK: ClassVar[set[str]] = {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "pre", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n• ")
        elif tag in ("td", "th"):
            self.out.append("\t")
        elif tag == "img":
            alt = dict(attrs).get("alt") or ""
            src = dict(attrs).get("src") or ""
            if alt or src.startswith("cid:"):
                self.out.append(f"[{alt or 'Bild'}]")
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(re.sub(r"[ \r\n\t\f]+", " ", data))


def html_to_text(value: str) -> str:
    p = _HtmlText()
    p.feed(value)
    text = "".join(p.out).replace("\t", "  ")
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _json_list(value: str | None) -> str:
    if not value:
        return ""
    try:
        v = json.loads(value)
    except ValueError:
        return value
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def _utc(date_header: str) -> str:
    try:
        d = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError):
        return ""
    if d.tzinfo is None:
        return ""
    return d.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _snapshot(case: Case) -> dict:
    import tomllib

    row = case.conn.execute("SELECT toml_text FROM config_snapshot WHERE id = 1").fetchone()
    try:
        return tomllib.loads(row[0]) if row and row[0] else {}
    except tomllib.TOMLDecodeError:
        return {}


def agency(case: Case) -> dict:
    """Agentur aus der Konfigurations-Kopie des Falls (wie im Bericht)."""
    return _snapshot(case).get("agentur", {})


def language(case: Case) -> str:
    """Sprache wie beim Bericht des Falls: Berichtssprache, sonst Oberflächensprache der Fall-Konfiguration."""
    from fed.core.languages import report_language_for

    p = _snapshot(case).get("programm", {})
    return report_language_for(p.get("bericht_sprache") or p.get("sprache") or "de")


def body_text(view: viewer.MailView) -> tuple[str, str]:
    """(Text, Quelle) – Textteil der Mail, sonst Textfassung des HTML-Teils."""
    if view.text.strip():
        return view.text.replace("\r\n", "\n"), "text"
    if view.html.strip():
        return html_to_text(view.html), "html"
    return "", "none"


def export_name(view: viewer.MailView) -> str:
    """Dateiname des Einzel-Exports, z. B. ``DF001_INBOX_uid123.pdf``."""
    from fed.core.naming import file_name

    r = view.db
    return file_name(f"{r['evidence']}_{r['folder']}_uid{r['uid']}", True) + ".pdf"


def write(case: Case, message_ids: list[int], path: Path, lang: str | None = None, *, full_headers: bool = True,
          received: bool = True, embed_eml: bool = True, keep_copy: bool = True) -> str:
    """Mails als ein PDF/A-3b schreiben (eine Mail je Abschnitt, neue Seite je Mail).

    Rückgabe: SHA-256 des PDF. Protokolliert ``mail_pdf_exported``; legt eine Kopie in ``exports/`` ab
    (nicht bei abgeschlossenem Fall – der Fall-Ordner wird dann nicht mehr verändert)."""
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    from fed.core import pdftext

    if not message_ids:
        raise ValueError("keine Mail gewählt")
    lang = lang or language(case)
    L = labels(lang)
    info = case.info()
    ag = agency(case)
    logo = case.logo()
    views = [viewer.open_message(case, mid, log=False) for mid in message_ids]
    fonts = resources.files("fed.resources") / "fonts"
    stamp = datetime.now(UTC)

    class MailPdf(FPDF):
        def header(self):
            if logo:
                self.image(io.BytesIO(logo[0]), x=15, y=10, h=14, keep_aspect_ratio=True, w=40)
            self.set_font("DejaVu", "B", 10)
            self.set_xy(110, 10)
            self.cell(85, 5, ag.get("name", ""), align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_font("DejaVu", "", 8)
            self.set_x(110)
            self.cell(85, 4, f"{L['case_number']} {info['case_number']}", align="R", new_x="LMARGIN", new_y="NEXT")
            self.set_x(110)
            self.cell(85, 4, f"{L['examiner']} {info['examiner']}", align="R")
            self.set_draw_color(200, 205, 214)
            self.line(15, 27, 195, 27)
            self.set_y(31)

        def footer(self):
            self.set_y(-14)
            self.set_font("DejaVu", "", 6.5)
            self.set_text_color(90, 98, 112)
            self.multi_cell(0, 3.2, L["mail_footer"], new_x="LMARGIN", new_y="NEXT")
            self.cell(0, 4, f"{APP_NAME} {__version__} · {info['case_number']}", align="L")
            self.set_x(15)
            self.cell(0, 4, f"{L['page']} {self.page_no()} {L['of']} {{nb}}", align="R")
            self.set_text_color(0, 0, 0)

    pdf = MailPdf(format="A4", enforce_compliance=PDFA)
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 20)
    pdf.add_font("DejaVu", "", str(fonts / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(fonts / "DejaVuSans-Bold.ttf"))
    pdf.add_font("DejaVu", "I", str(fonts / "DejaVuSans-Oblique.ttf"))
    pdf.add_font("Mono", "", str(fonts / "DejaVuSansMono.ttf"))
    sample = " ".join(v.subject + v.from_ + v.to + v.text[:2000] for v in views)
    pdftext.setup(pdf, shaping=pdftext.needs_shaping(sample))
    title = ((views[0].subject or L["mail_no_subject"]) if len(views) == 1
             else L["mail_title_multi"].format(n=len(views)))
    pdf.set_title(f"{title} – {info['case_number']}")
    pdf.set_author(info["examiner"] or "")
    pdf.set_subject(L["mail_title"])
    pdf.set_creator(f"{APP_NAME} {__version__}")
    pdf.set_lang("de-DE" if lang == "de" else "en")
    head = FontFace(emphasis="BOLD", fill_color=(234, 240, 252))

    def h2(text):
        pdf.ln(2.5)
        if pdf.get_y() > 245:
            pdf.add_page()
        pdf.set_font("DejaVu", "B", 10.5)
        pdf.cell(0, 6.5, text, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 8.5)

    def kv(items):
        with pdf.table(col_widths=(45, 135), first_row_as_headings=False, line_height=4.4, text_align="LEFT",
                       borders_layout="HORIZONTAL_LINES") as t:
            for k, v in items:
                if v in (None, ""):
                    continue
                r = t.row()
                r.cell(k)
                r.cell(str(v))

    exported = []
    for n, v in enumerate(views, start=1):
        r = v.db
        pdf.add_page()
        if len(views) > 1:
            pdf.set_font("DejaVu", "", 8)
            pdf.cell(0, 4, L["mail_n_of"].format(n=n, total=len(views)), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "B", 14)
        pdf.multi_cell(0, 7, v.subject or L["mail_no_subject"], new_x="LMARGIN", new_y="NEXT")

        h2(L["mail_identity"])
        sha_state = L["mail_hash_ok"] if v.sha256_ok else L["mail_hash_bad"]
        kv([
            (L["account"], f"{r['evidence']} · {r['account']}"),
            (L["mail_folder"], r["folder"]),
            (L["mail_file"], r["zip_path"]),
            ("SHA-256", f"{r['sha256']}\n{sha_state}"),
            (L["mail_size"], f"{_fmt_bytes(r['size'])} ({r['size']:,} Bytes)".replace(",", ".")),
            ("Message-ID", r.get("message_id") or ""),
            ("IMAP UID / UIDVALIDITY", f"{r['uid']} / {r.get('uidvalidity') or '–'}"),
            (L["mail_internaldate"], r.get("internaldate") or ""),
            (L["mail_saved"], r.get("saved_utc") or ""),
            ("Gmail-Labels", _json_list(r.get("gm_labels"))),
            (L["mail_source"], L["mail_source_work"] if v.source == "work" else v.source),
            (L["mail_embedded"], L["mail_embedded_yes"] if embed_eml else ""),
        ])

        h2(L["mail_headers"])
        reply_to = next((val for k, val in v.headers if k.lower() == "reply-to"), "")
        kv([
            (L["from"], v.from_), (L["to"], v.to), ("Cc", v.cc), ("Bcc", _json_list(r.get("msg_bcc"))),
            (L["mail_reply_to"], reply_to), ("Delivered-To", _json_list(r.get("delivered_to"))),
            (L["mail_date"], f"{v.date}\n{_utc(v.date)}".strip()), (L["subject"], v.subject),
        ])

        auth = viewer.auth_summary(v)
        prov = auth["provider"] or {}
        h2(L["mail_auth"])
        kv([
            ("DKIM (FED)", " · ".join(x for x in (auth["dkim"] or L["mail_not_checked"], auth["domain"] or "",
                                                  f"s={auth['selector']}" if auth["selector"] else "") if x)),
            (L["mail_auth_provider"], ", ".join(f"{k}={val}" for k, val in prov.items()) if prov else ""),
        ])

        h2(L["mail_body"])
        text, src = body_text(v)
        pdf.set_font("DejaVu", "I", 7.5)
        pdf.set_text_color(90, 98, 112)
        pdf.multi_cell(0, 3.8, {"text": L["mail_body_text"], "html": L["mail_body_html"],
                                "none": L["mail_body_none"]}[src], new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.ln(1)
        pdf.set_font("DejaVu", "", 9)
        if text:
            pdf.multi_cell(0, 4.6, text, new_x="LMARGIN", new_y="NEXT")

        images = [a for a in v.attachments if a.inline and a.mime in _IMAGE_TYPES]
        if images:
            h2(L["mail_inline_images"])
            for a in images:
                data = viewer.attachment_data(v, a.index)
                try:
                    if pdf.get_y() > 200:
                        pdf.add_page()
                    pdf.image(io.BytesIO(data), w=90, keep_aspect_ratio=True, h=70)
                    pdf.set_font("DejaVu", "", 7)
                    pdf.cell(0, 4, f"{a.name} · SHA-256 {a.sha256}", new_x="LMARGIN", new_y="NEXT")
                except Exception:  # Bild nicht darstellbar – im Original weiterhin enthalten
                    pdf.set_font("DejaVu", "", 7)
                    pdf.cell(0, 4, f"{a.name}: {L['mail_image_failed']}", new_x="LMARGIN", new_y="NEXT")

        files = [a for a in v.attachments if not (a.inline and a.mime in _IMAGE_TYPES)]
        h2(f"{L['mail_attachments']} ({len(files)})")
        if files:
            from fed.core.filecheck import check

            with pdf.table(col_widths=(48, 30, 18, 50, 34), headings_style=head, line_height=4.2,
                           text_align=("LEFT", "LEFT", "RIGHT", "LEFT", "LEFT")) as t:
                t.row([L["mail_att_name"], L["mail_att_format"], L["mail_size"], "SHA-256", L["mail_att_findings"]])
                for a in files:
                    fc = check(a.name, viewer.attachment_data(v, a.index), a.mime)
                    s = fc.summary()
                    t.row([a.name, s.get("format") or a.mime, _fmt_bytes(a.size), a.sha256,
                           ", ".join(s.get("findings") or []) or "–"])
        else:
            pdf.cell(0, 5, L["none"], new_x="LMARGIN", new_y="NEXT")

        if received and v.hops:
            h2(L["mail_received"])
            with pdf.table(col_widths=(8, 52, 52, 22, 46), headings_style=head, line_height=4.0,
                           text_align="LEFT") as t:
                t.row(["#", L["mail_hop_from"], L["mail_hop_by"], L["mail_hop_with"], L["mail_hop_time"]])
                for hop in v.hops:
                    when = hop.time.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if hop.time and hop.time.tzinfo \
                        else (hop.time.isoformat() if hop.time else "")
                    t.row([str(hop.nr), hop.from_, hop.by, hop.with_, when])

        if full_headers:
            h2(L["mail_all_headers"])
            pdf.set_font("Mono", "", 6.8)
            for k, val in v.headers:
                pdf.multi_cell(0, 3.3, f"{k}: {val}", new_x="LMARGIN", new_y="NEXT")

        if embed_eml:
            pdf.embed_file(bytes=v.raw, basename=Path(r["zip_path"]).name or f"mail-{r['uid']}.eml",
                           mime_type="message/rfc822", associated_file_relationship="Source",
                           desc=f"{r['zip_path']} · SHA-256 {r['sha256']}")
        exported.append({"message_id": v.message_id, "file": r["zip_path"], "sha256": r["sha256"],
                         "sha256_ok": v.sha256_ok})

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    copy = None
    if keep_copy and not case.closed:
        exp = case.root / EXPORT_DIR
        exp.mkdir(exist_ok=True)
        target = exp / path.name
        if target.resolve() != path.resolve():
            i = 2
            while target.exists():
                target = exp / f"{path.stem}_{i}{path.suffix}"
                i += 1
            shutil.copy2(path, target)
        copy = str(target.relative_to(case.root))
    case.log("INFO", "mail_pdf_exported", {
        "target": str(path), "copy": copy, "sha256": sha, "size": path.stat().st_size, "format": PDFA,
        "language": lang, "full_headers": full_headers, "received": received, "embedded_eml": embed_eml,
        "messages": exported, "created_utc": stamp.isoformat(timespec="seconds")})
    return sha
