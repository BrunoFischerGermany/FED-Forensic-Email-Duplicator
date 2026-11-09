"""Mehrsprachiges Testpostfach (eigenes Konto ``intl@example.de``).

Je Sprache ein Ordnerbaum über vier Ebenen; Mails mit Absendername, Betreff, Text und Anlagenname
in der jeweiligen Schrift (RFC 2047/2231, UTF-8) – dazu je eine Mail in einer alten Zeichenkodierung,
wie sie in der Praxis vorkommt (KOI8-R, ISO-2022-JP, GB18030, windows-1256, windows-1255).
Alle Inhalte sind fest, die SHA-256 also bekannt.

Schriften: Russisch, Serbisch (kyrillisch), Kroatisch/Serbokroatisch (lateinisch), Vietnamesisch,
Chinesisch, Japanisch, Myanmar (Birmanisch), Arabisch, Hebräisch – Arabisch und Hebräisch
werden von rechts nach links geschrieben, Arabisch und Myanmar brauchen Textformung.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.header import Header
from email.utils import formataddr
from urllib.parse import quote

try:
    from validation.testmailbox import TestMail, _dt, _encode_mutf7
except ImportError:  # als Skript neben testmailbox.py (validation/run.py, dovecot_server.py)
    from testmailbox import TestMail, _dt, _encode_mutf7

USER = "intl@example.de"
PASSWORD = "intl-2026"


@dataclass(frozen=True)
class Lang:
    code: str
    name: str  # deutsch, für Berichte
    path: tuple[str, str, str, str]  # Ordner Ebene 1–4
    sender: str
    subject: str
    body: str
    attachment: str  # Dateiname
    rtl: bool = False
    legacy: str | None = None  # alte Kodierung für eine zweite Mail (Ebene 2)
    search: str = ""  # Suchbegriff für den Inhaltsfilter (im Betreff)


LANGS = [
    Lang("ru", "Russisch", ("Русский", "Работа", "Проекты", "Отчёты"),
         "Пётр Иванов", "Отчёт за ноябрь – проверка данных",
         "Здравствуйте!\nВо вложении отчёт за ноябрь. Ёлка, щука, съезд.\n", "Отчёт_ноябрь.txt",
         legacy="koi8-r", search="Отчёт"),
    Lang("sr", "Serbisch (kyrillisch)", ("Српски", "Посао", "Пројекти", "Извештаји"),
         "Ђорђе Јовановић", "Извештај о преносу података – љубазно, њива, џеп",
         "Поштовани,\nу прилогу је извештај. Ћирилица: ђ ј љ њ ћ џ.\n", "Извештај.txt", search="Извештај"),
    Lang("hr", "Kroatisch / Serbokroatisch (lateinisch)", ("Hrvatski", "Računi", "Plaćeno", "Đurđevdan"),
         "Željko Čačić", "Račun br. 17 – plaćeno, šećer, žuta đurđica",
         "Poštovani,\nu prilogu je račun. Č ć Đ đ Š š Ž ž, dž lj nj.\n", "Račun_17.txt", search="Račun"),
    Lang("vi", "Vietnamesisch", ("Tiếng Việt", "Công việc", "Dự án", "Báo cáo"),
         "Nguyễn Văn Ánh", "Báo cáo tháng mười một – Hà Nội, Đà Nẵng",
         "Kính gửi anh chị,\nđính kèm là báo cáo. Ơ ư ặ ữ ỹ ỗ.\n", "Báo_cáo.txt", search="Báo cáo"),
    Lang("zh", "Chinesisch", ("中文", "工作", "项目", "报告"),
         "王小明", "关于项目进度的报告（第三季度）",
         "您好！\n附件是第三季度的项目报告。繁體字：資料、報告。\n", "项目报告.txt",
         legacy="gb18030", search="报告"),
    Lang("ja", "Japanisch", ("日本語", "仕事", "プロジェクト", "報告書"),
         "山田太郎", "報告書の送付について（ご確認ください）",
         "お世話になっております。\n報告書を添付いたします。ひらがな・カタカナ・漢字。\n", "報告書.txt",
         legacy="iso-2022-jp", search="報告書"),
    Lang("my", "Myanmar (Birmanisch)", ("မြန်မာ", "အလုပ်", "စီမံကိန်း", "အစီရင်ခံစာ"),
         "မောင်မောင်", "စီမံကိန်း အစီရင်ခံစာ နိုဝင်ဘာလ",
         "မင်္ဂလာပါ။\nအစီရင်ခံစာကို ပူးတွဲပေးပို့ပါသည်။\n", "အစီရင်ခံစာ.txt", search="အစီရင်ခံစာ"),
    Lang("ar", "Arabisch", ("العربية", "العمل", "المشاريع", "التقارير"),
         "محمد أحمد", "تقرير المشروع – الربع الثالث 2026 (PDF)",
         "مرحباً،\nمرفق تقرير المشروع للربع الثالث 2026.\n", "تقرير_المشروع.txt", rtl=True,
         legacy="windows-1256", search="تقرير"),
    Lang("he", "Hebräisch", ("עברית", "עבודה", "פרויקטים", "דוחות"),
         "דוד כהן", "דוח פרויקט – רבעון שלישי 2026 (PDF)",
         "שלום,\nמצורף דוח הפרויקט לרבעון השלישי 2026.\n", "דוח_פרויקט.txt", rtl=True,
         legacy="windows-1255", search="דוח"),
]


def _b64(data: bytes) -> str:
    s = base64.b64encode(data).decode()
    return "\n".join(s[i:i + 76] for i in range(0, len(s), 76))


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")


def folder(lang: Lang, level: int = 4) -> str:
    return "/".join(lang.path[:level])


def _utf8_mail(i: int, lang: Lang) -> bytes:
    sender = formataddr((str(Header(lang.sender, "utf-8")), f"absender{i}@{lang.code}.example"))
    subject = Header(lang.subject, "utf-8").encode()
    body = lang.body.encode("utf-8")
    att = f"Anlage {lang.code}: {lang.subject}\n".encode()
    fn = lang.attachment
    html = (f'<html><body dir="{"rtl" if lang.rtl else "ltr"}"><p>{lang.body.splitlines()[0]}</p>'
            f"<p>{lang.subject}</p></body></html>\n").encode()
    return _crlf(
        f"From: {sender}\nTo: Ermittlung <{USER}>\nSubject: {subject}\n"
        f"Date: Mon, 0{(i % 9) + 1} Nov 2026 10:{i:02d}:00 +0100\nMessage-ID: <intl-{lang.code}@example.de>\n"
        "MIME-Version: 1.0\nContent-Type: multipart/mixed; boundary=\"intl\"\n\n"
        "--intl\nContent-Type: multipart/alternative; boundary=\"alt\"\n\n"
        f"--alt\nContent-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: base64\n\n{_b64(body)}\n"
        f"--alt\nContent-Type: text/html; charset=utf-8\nContent-Transfer-Encoding: base64\n\n{_b64(html)}\n--alt--\n"
        f"--intl\nContent-Type: text/plain; charset=utf-8; name*=UTF-8''{quote(fn)}\n"
        f"Content-Disposition: attachment; filename*=UTF-8''{quote(fn)}\nContent-Transfer-Encoding: base64\n\n"
        f"{_b64(att)}\n--intl--\n")


def _legacy_mail(i: int, lang: Lang) -> bytes:
    """Zweite Mail in einer alten Kodierung: Kopfzeilen und Text als Bytes dieser Kodierung."""
    cs = lang.legacy
    subject = Header(lang.subject.replace("–", "-"), cs).encode()  # Gedankenstrich fehlt in KOI8-R
    sender = formataddr((str(Header(lang.sender, cs)), f"alt{i}@{lang.code}.example"))
    head = (f"From: {sender}\r\nTo: {USER}\r\nSubject: {subject}\r\n"
            f"Date: Tue, 1{(i % 9)} Nov 2026 09:{i:02d}:00 +0100\r\nMIME-Version: 1.0\r\n"
            f"Content-Type: text/plain; charset={cs}\r\n")
    if cs == "iso-2022-jp":  # 7bit-Kodierung, wird so übertragen
        return (head + "Content-Transfer-Encoding: 7bit\r\n\r\n").encode("ascii") + \
            lang.body.replace("\n", "\r\n").encode(cs)
    return (head + "Content-Transfer-Encoding: 8bit\r\n\r\n").encode("ascii") + \
        lang.body.replace("\n", "\r\n").encode(cs)


def mails() -> list[TestMail]:
    out = []
    for i, lang in enumerate(LANGS, start=1):
        out.append(TestMail(folder(lang), _dt(2026, 11, 1 + i % 9, 9, i, 0), _utf8_mail(i, lang),
                            note=f"{lang.name}, Ebene 4, UTF-8, Anlage mit Namen in der Schrift"))
        if lang.legacy:
            out.append(TestMail(folder(lang, 2), _dt(2026, 11, 10 + i % 9, 8, i, 0), _legacy_mail(i, lang),
                                note=f"{lang.name}, Ebene 2, {lang.legacy}"))
    return out


def folders() -> set[str]:
    """Alle Ordner (auch die Ebenen ohne eigene Mails)."""
    out = {"INBOX"}
    for lang in LANGS:
        out |= {folder(lang, n) for n in range(1, 5)}
    return out


def build(maildir) -> list[TestMail]:
    import os

    folders_ = folders()
    for f in sorted(folders_):
        d = maildir if f == "INBOX" else maildir / ("." + ".".join(_encode_mutf7(p) for p in f.split("/")))
        for sub in ("cur", "new", "tmp"):
            (d / sub).mkdir(parents=True, exist_ok=True)
    (maildir / "subscriptions").write_text("\n".join(sorted(folders_ - {"INBOX"})) + "\n", encoding="utf-8")
    for i, m in enumerate(mails(), start=1):
        d = maildir / ("." + ".".join(_encode_mutf7(p) for p in m.folder.split("/")))
        ts = int(m.received.timestamp())
        p = d / "cur" / f"{ts}.M{i}P1.fedintl,S={len(m.raw)}:2,"
        p.write_bytes(m.raw)
        os.utime(p, (ts, ts))
    return mails()
