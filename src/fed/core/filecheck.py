"""Prüfung einer Datei (Anlage) am Inhalt – ohne Qt, ohne sie auszuführen.

Erkennt das Format an den Magic Bytes (wie Crush Forensics), vergleicht es mit Dateiendung und
angegebenem MIME-Typ und meldet Auffälligkeiten: ausführbare Inhalte, Makros, aktive Inhalte in
PDFs, doppelte oder verschleierte Endungen, verschlüsselte Archive, Datenträger-Abbilder.

Die Befunde tragen einen Code und Werte; die Oberfläche übersetzt sie (``FINDING_TEXT``).
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Format:
    key: str
    name: str
    mime: str
    exts: tuple[str, ...]
    preview: str = "hex"  # image | pdf | text | hex
    alt_mimes: tuple[str, ...] = ()
    executable: bool = False


def _f(key, name, mime, exts, preview="hex", alt=(), exe=False) -> Format:
    return Format(key, name, mime, tuple(exts.split()), preview, tuple(alt), exe)


FORMATS: dict[str, Format] = {f.key: f for f in [
    _f("pdf", "PDF-Dokument", "application/pdf", "pdf", "pdf", ("application/x-pdf",)),
    _f("png", "PNG-Bild", "image/png", "png", "image"),
    _f("jpeg", "JPEG-Bild", "image/jpeg", "jpg jpeg jpe jfif", "image", ("image/jpg", "image/pjpeg")),
    _f("gif", "GIF-Bild", "image/gif", "gif", "image"),
    _f("bmp", "BMP-Bild", "image/bmp", "bmp dib", "image", ("image/x-bmp", "image/x-ms-bmp")),
    _f("tiff", "TIFF-Bild", "image/tiff", "tif tiff", "image"),
    _f("webp", "WebP-Bild", "image/webp", "webp", "image"),
    _f("ico", "Symbol (ICO)", "image/vnd.microsoft.icon", "ico", "image", ("image/x-icon",)),
    _f("heic", "HEIC-Bild", "image/heic", "heic heif", "hex", ("image/heif",)),
    _f("svg", "SVG-Grafik", "image/svg+xml", "svg", "text"),
    _f("docx", "Word-Dokument (OOXML)", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       "docx dotx", alt=("application/zip",)),
    _f("docm", "Word-Dokument mit Makros", "application/vnd.ms-word.document.macroEnabled.12", "docm dotm",
       alt=("application/zip",)),
    _f("xlsx", "Excel-Tabelle (OOXML)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
       "xlsx xltx", alt=("application/zip",)),
    _f("xlsm", "Excel-Tabelle mit Makros", "application/vnd.ms-excel.sheet.macroEnabled.12", "xlsm xltm xlsb",
       alt=("application/zip",)),
    _f("pptx", "PowerPoint (OOXML)", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
       "pptx potx ppsx", alt=("application/zip",)),
    _f("pptm", "PowerPoint mit Makros", "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
       "pptm potm ppsm", alt=("application/zip",)),
    _f("odf", "OpenDocument", "application/vnd.oasis.opendocument.text", "odt ods odp odg ott ots otp",
       alt=("application/vnd.oasis.opendocument.spreadsheet", "application/vnd.oasis.opendocument.presentation",
            "application/zip")),
    _f("ole", "Office 97–2003 / Outlook-Nachricht (OLE)", "application/msword", "doc dot xls xlt ppt pps msg",
       alt=("application/vnd.ms-excel", "application/vnd.ms-powerpoint", "application/vnd.ms-outlook",
            "application/x-msg")),
    _f("jar", "Java-Archiv", "application/java-archive", "jar", alt=("application/zip",), exe=True),
    _f("apk", "Android-App", "application/vnd.android.package-archive", "apk", alt=("application/zip",), exe=True),
    _f("zip", "ZIP-Archiv", "application/zip", "zip", alt=("application/x-zip-compressed", "application/x-zip")),
    _f("rar", "RAR-Archiv", "application/vnd.rar", "rar", alt=("application/x-rar-compressed", "application/x-rar")),
    _f("7z", "7-Zip-Archiv", "application/x-7z-compressed", "7z"),
    _f("gzip", "GZIP-Archiv", "application/gzip", "gz tgz", alt=("application/x-gzip",)),
    _f("bzip2", "BZIP2-Archiv", "application/x-bzip2", "bz2 tbz2"),
    _f("xz", "XZ-Archiv", "application/x-xz", "xz txz"),
    _f("cab", "Windows-Archiv (CAB)", "application/vnd.ms-cab-compressed", "cab"),
    _f("iso", "Datenträger-Abbild (ISO)", "application/x-iso9660-image", "iso img", alt=("application/x-cd-image",)),
    _f("exe", "Windows-Programm (PE)", "application/x-msdownload", "exe dll scr sys com cpl ocx drv efi",
       alt=("application/x-dosexec", "application/vnd.microsoft.portable-executable"), exe=True),
    _f("elf", "Linux-Programm (ELF)", "application/x-executable", "elf so bin o", alt=("application/x-sharedlib",),
       exe=True),
    _f("macho", "macOS-Programm (Mach-O)", "application/x-mach-binary", "dylib bundle", exe=True),
    _f("lnk", "Windows-Verknüpfung (LNK)", "application/x-ms-shortcut", "lnk", exe=True),
    _f("rtf", "RTF-Dokument", "application/rtf", "rtf", "text", ("text/rtf",)),
    _f("mp3", "MP3-Audio", "audio/mpeg", "mp3", alt=("audio/mp3",)),
    _f("mp4", "MP4/QuickTime-Video", "video/mp4", "mp4 m4v m4a mov 3gp", alt=("video/quicktime", "audio/mp4",
                                                                            "audio/x-m4a", "video/3gpp")),
    _f("wav", "WAV-Audio", "audio/wav", "wav", alt=("audio/x-wav", "audio/wave")),
    _f("avi", "AVI-Video", "video/x-msvideo", "avi"),
    _f("ogg", "Ogg-Audio/Video", "audio/ogg", "ogg oga ogv opus", alt=("video/ogg", "application/ogg")),
    _f("flac", "FLAC-Audio", "audio/flac", "flac", alt=("audio/x-flac",)),
    _f("eml", "E-Mail (RFC 5322)", "message/rfc822", "eml msg mht mhtml", "text"),
    _f("ics", "Kalendereintrag (iCalendar)", "text/calendar", "ics ifb vcs", "text"),
    _f("vcf", "Kontakt (vCard)", "text/vcard", "vcf vcard", "text", ("text/x-vcard",)),
    _f("html", "HTML-Seite", "text/html", "html htm xhtml shtml", "text"),
    _f("hta", "HTML-Anwendung (HTA)", "application/hta", "hta", "text", exe=True),
    _f("xml", "XML-Datei", "application/xml", "xml xsd xsl rss plist config", "text", ("text/xml",)),
    _f("ps", "PostScript", "application/postscript", "ps eps ai", "text"),
    _f("text", "Text", "text/plain", "txt csv tsv log md json ini cfg conf asc sig pem key yaml yml sql",
       "text", ("text/csv", "application/json", "text/markdown", "text/x-log")),
    _f("empty", "leere Datei", "application/x-empty", ""),
    _f("unknown", "unbekannt", "application/octet-stream", ""),
]}

# Endungen, die beim Öffnen Code ausführen können (Skripte, Programme, Verknüpfungen …)
RISKY_EXTS = frozenset(
    "exe dll scr com cpl pif msi msp msc bat cmd ps1 psm1 psd1 vbs vbe js jse wsf wsh hta lnk jar "
    "reg sys inf apk app dmg pkg sh command iso img vhd vhdx appx appxbundle msix library-ms url scf "
    "chm xll xlam ppam docm dotm xlsm xltm pptm potm".split())
SCRIPT_EXTS = frozenset("bat cmd ps1 psm1 vbs vbe js jse wsf wsh sh command reg url scf inf".split())
NEUTRAL_MIMES = frozenset({"", "application/octet-stream", "application/x-unknown", "application/unknown",
                           "binary/octet-stream", "application/force-download"})
BIDI_CHARS = "‪‫‬‭‮⁦⁧⁨⁩‎‏"


@dataclass
class Finding:
    level: str  # "warn" | "info"
    code: str
    args: tuple = ()


@dataclass
class FileCheck:
    name: str
    size: int
    md5: str
    sha1: str
    sha256: str
    fmt: Format
    detail: str  # z. B. „PDF 1.7“, „PE“, bei Archiven die Anzahl der Einträge
    ext: str
    ext_ok: bool | None  # None = nicht beurteilbar (keine Endung / Format unbekannt)
    declared_mime: str
    mime_ok: bool | None
    entropy: float
    findings: list[Finding] = field(default_factory=list)

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def verdict(self) -> str:
        """``ok`` | ``info`` | ``warn``"""
        if self.warnings:
            return "warn"
        return "info" if self.findings else "ok"

    def summary(self) -> dict:
        return {"name": self.name, "size": self.size, "sha256": self.sha256, "format": self.fmt.key,
                "ext_ok": self.ext_ok, "mime_ok": self.mime_ok, "declared_mime": self.declared_mime,
                "findings": [f.code for f in self.findings]}


# ------------------------------------------------------------------ Erkennung
def _zip_kind(data: bytes) -> tuple[str, str, list[Finding]]:
    findings: list[Finding] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return "zip", "", [Finding("info", "zip_damaged")]
    names = [i.filename for i in infos]
    lower = {n.lower() for n in names}
    if any(i.flag_bits & 0x1 for i in infos):
        findings.append(Finding("info", "zip_encrypted"))
    risky = [n for n in names if PurePosixPath(n).suffix.lower().lstrip(".") in RISKY_EXTS]
    detail = f"{len(names)}"
    kind = "zip"
    if "[content_types].xml" in lower:
        macro = any(n.endswith("vbaproject.bin") for n in lower)
        if any(n.startswith("word/") for n in lower):
            kind = "docm" if macro else "docx"
        elif any(n.startswith("xl/") for n in lower):
            kind = "xlsm" if macro else "xlsx"
        elif any(n.startswith("ppt/") for n in lower):
            kind = "pptm" if macro else "pptx"
        if macro:
            findings.append(Finding("warn", "macros"))
        if any(n.startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/")) for n in lower):
            findings.append(Finding("info", "embedded_objects"))
        ext_rel = [n for n in names if n.endswith(".rels")]
        for n in ext_rel:
            try:
                rel = zf.read(n)
            except (KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
                continue
            if b'TargetMode="External"' in rel and (b"attachedTemplate" in rel or b"oleObject" in rel):
                findings.append(Finding("warn", "remote_template"))
                break
        return kind, detail, findings
    if names and names[0] == "mimetype":
        try:
            if zf.read("mimetype").startswith(b"application/vnd.oasis.opendocument"):
                if any(n.lower().startswith("basic/") and n.lower().endswith(".xml") and "script-lc" not in n.lower()
                       for n in names):
                    findings.append(Finding("warn", "macros"))
                return "odf", detail, findings
        except (KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
            pass
    if "androidmanifest.xml" in lower and "classes.dex" in lower:
        return "apk", detail, findings
    if "meta-inf/manifest.mf" in lower:
        return "jar", detail, findings
    if risky:
        findings.append(Finding("warn", "archive_contains_executable", (", ".join(risky[:5]),)))
    return kind, detail, findings


def _ole_findings(data: bytes) -> list[Finding]:
    u16 = lambda s: s.encode("utf-16-le")  # noqa: E731
    out = []
    if u16("_VBA_PROJECT") in data or (u16("VBA") in data and u16("Macros") in data):
        out.append(Finding("warn", "macros"))
    if u16("EncryptionInfo") in data or u16("EncryptedPackage") in data:
        out.append(Finding("info", "office_encrypted"))
    return out


def _pdf_findings(data: bytes) -> tuple[str, list[Finding]]:
    m = re.match(rb"%PDF-(\d\.\d)", data[data.find(b"%PDF-"):][:8])
    version = m.group(1).decode() if m else ""
    out = []
    active = [k for k, pat in (("JavaScript", rb"/JavaScript\b|/JS\b"), ("OpenAction", rb"/OpenAction\b|/AA\b"),
                               ("Launch", rb"/Launch\b"), ("SubmitForm", rb"/SubmitForm\b"),
                               ("RichMedia", rb"/RichMedia\b"), ("XFA", rb"/XFA\b"))
              if re.search(pat, data)]
    if active:
        out.append(Finding("warn", "pdf_active", (", ".join(active),)))
    if re.search(rb"/EmbeddedFile\b", data):
        out.append(Finding("warn", "pdf_embedded_file"))
    if re.search(rb"/Encrypt\b", data):
        out.append(Finding("info", "pdf_encrypted"))
    if data.find(b"%PDF-") > 0:
        out.append(Finding("info", "pdf_offset", (data.find(b"%PDF-"),)))
    return (f"PDF {version}" if version else ""), out


def _text_kind(data: bytes) -> str | None:
    head = data[:4096]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            head = head.decode("utf-16").encode("utf-8")
        except UnicodeDecodeError:
            return None
    elif b"\x00" in head:
        return None
    try:
        s = head.decode("utf-8")
    except UnicodeDecodeError:
        s = head.decode("latin-1")
        printable = sum(c.isprintable() or c in "\r\n\t" for c in s) / max(len(s), 1)
        if printable < 0.95:
            return None
    t = s.lstrip("﻿ \r\n\t").lower()
    if t.startswith("{\\rtf"):
        return "rtf"
    if t.startswith("%!ps"):
        return "ps"
    if t.startswith("begin:vcalendar"):
        return "ics"
    if t.startswith("begin:vcard"):
        return "vcf"
    if "<hta:application" in t:
        return "hta"
    if t.startswith(("<!doctype html", "<html")) or re.search(r"<(html|head|body)[\s>]", t[:1024]):
        return "html"
    if t.startswith("<?xml") or t.startswith("<svg"):
        return "svg" if "<svg" in t else "xml"
    if re.match(r"(?:(?:received|return-path|from|to|subject|date|message-id|mime-version|delivered-to|"
                r"x-[\w-]+):[^\n]*\r?\n(?:[ \t][^\n]*\r?\n)*){3,}", t):
        return "eml"
    return "text"


def identify(data: bytes) -> tuple[Format, str, list[Finding]]:
    """Format am Inhalt erkennen. Rückgabe: Format, Detail, Befunde zum Inhalt."""
    if not data:
        return FORMATS["empty"], "", []
    h = data[:64]
    key, detail, findings = "unknown", "", []
    if b"%PDF-" in data[:1024]:
        key = "pdf"
        detail, findings = _pdf_findings(data)
    elif h.startswith(b"\x89PNG\r\n\x1a\n"):
        key = "png"
    elif h.startswith(b"\xff\xd8\xff"):
        key = "jpeg"
    elif h.startswith((b"GIF87a", b"GIF89a")):
        key = "gif"
    elif h.startswith((b"II*\x00", b"MM\x00*")):
        key = "tiff"
    elif h.startswith(b"RIFF") and h[8:12] in (b"WEBP", b"WAVE", b"AVI "):
        key = {b"WEBP": "webp", b"WAVE": "wav", b"AVI ": "avi"}[h[8:12]]
    elif h.startswith(b"BM") and len(data) > 26 and int.from_bytes(data[2:6], "little") in (len(data), 0):
        key = "bmp"
    elif h.startswith(b"\x00\x00\x01\x00") and len(data) > 22 and 0 < h[4] < 64:
        key = "ico"
    elif h.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        key, detail, findings = _zip_kind(data)  # detail = Anzahl Einträge
    elif h.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        key, findings = "ole", _ole_findings(data)
    elif h.startswith(b"Rar!\x1a\x07"):
        key = "rar"
    elif h.startswith(b"7z\xbc\xaf\x27\x1c"):
        key = "7z"
    elif h.startswith(b"\x1f\x8b"):
        key = "gzip"
    elif h.startswith(b"BZh"):
        key = "bzip2"
    elif h.startswith(b"\xfd7zXZ\x00"):
        key = "xz"
    elif h.startswith(b"MSCF"):
        key = "cab"
    elif h.startswith(b"MZ"):
        key = "exe"
        pe = int.from_bytes(data[0x3C:0x40], "little") if len(data) > 0x40 else 0
        detail = "PE" if data[pe:pe + 4] == b"PE\x00\x00" else "DOS"
    elif h.startswith(b"\x7fELF"):
        key = "elf"
    elif h[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
                   b"\xca\xfe\xba\xbe"):
        key = "macho"
    elif h.startswith(b"L\x00\x00\x00\x01\x14\x02\x00"):
        key = "lnk"
    elif data[0x8001:0x8006] == b"CD001" or data[0x8801:0x8806] == b"CD001":
        key = "iso"
    elif h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and h[1] & 0xE0 == 0xE0 and h[1] & 0x06 == 0x02):
        key = "mp3"
    elif h[4:8] == b"ftyp":
        brand = h[8:12]
        key = "heic" if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc") else "mp4"
        detail = brand.decode("latin-1").strip()
    elif h.startswith(b"OggS"):
        key = "ogg"
    elif h.startswith(b"fLaC"):
        key = "flac"
    else:
        key = _text_kind(data) or "unknown"
    return FORMATS[key], detail, findings


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data[: 4 * 1024 * 1024]:
        counts[b] += 1
    n = min(len(data), 4 * 1024 * 1024)
    return -sum(c / n * math.log2(c / n) for c in counts if c)


def _suffixes(name: str) -> list[str]:
    base = name.strip().rstrip(". ")
    parts = base.split(".")
    return [p.lower().strip() for p in parts[1:]] if len(parts) > 1 else []


def check(name: str, data: bytes, declared_mime: str = "") -> FileCheck:
    fmt, detail, findings = identify(data)
    findings = list(findings)
    sfx = _suffixes(name)
    ext = sfx[-1] if sfx else ""
    declared = (declared_mime or "").lower().strip()

    if any(c in name for c in BIDI_CHARS):
        findings.insert(0, Finding("warn", "bidi_name"))
    if len(sfx) >= 2 and ext in RISKY_EXTS and sfx[-2] in {e for f in FORMATS.values() if not f.executable
                                                          for e in f.exts}:
        findings.insert(0, Finding("warn", "double_extension", (f".{sfx[-2]}.{ext}",)))
    if re.search(r"\s{5,}\.\w+$", name):
        findings.append(Finding("warn", "padded_name"))

    if fmt.key in ("unknown", "empty") or not ext:
        ext_ok = None
    elif fmt.key == "text":
        ext_ok = ext in fmt.exts or (ext not in RISKY_EXTS and ext not in {e for f in FORMATS.values() for e in f.exts})
    else:
        ext_ok = ext in fmt.exts
    if fmt.key == "text" and ext in SCRIPT_EXTS:
        findings.append(Finding("warn", "script", (ext,)))
        ext_ok = True
    if ext_ok is False:
        findings.append(Finding("warn", "ext_mismatch", (ext, fmt.name, " ".join(fmt.exts[:3]))))

    if declared in NEUTRAL_MIMES or fmt.key in ("unknown", "empty"):
        mime_ok = None
    else:
        mime_ok = declared == fmt.mime or declared in fmt.alt_mimes
        if not mime_ok and fmt.key == "text" and declared.startswith("text/"):
            mime_ok = True
        if not mime_ok and fmt.key == "eml" and declared.startswith("text/"):
            mime_ok = True
        if not mime_ok:
            findings.append(Finding("info", "mime_mismatch", (declared, fmt.mime)))

    if fmt.executable:
        findings.insert(0, Finding("warn", "executable", (fmt.name,)))
    elif ext in RISKY_EXTS and fmt.key not in ("docm", "xlsm", "pptm", "iso") and ext not in SCRIPT_EXTS:
        findings.append(Finding("warn", "risky_ext", (ext,)))
    if fmt.key == "iso":
        findings.append(Finding("warn", "disk_image"))
    if fmt.key == "empty":
        findings.append(Finding("info", "empty"))

    ent = entropy(data)
    if fmt.key == "unknown" and ent > 7.5 and len(data) > 1024:
        findings.append(Finding("info", "high_entropy", (round(ent, 2),)))

    # Doppelte Befunde (gleicher Code) zusammenfassen
    seen, uniq = set(), []
    for f in findings:
        if f.code not in seen:
            seen.add(f.code)
            uniq.append(f)
    return FileCheck(name, len(data), hashlib.md5(data).hexdigest(), hashlib.sha1(data).hexdigest(),
                     hashlib.sha256(data).hexdigest(), fmt, detail, ext, ext_ok, declared, mime_ok, ent, uniq)


def hexdump(data: bytes, limit: int = 64 * 1024, width: int = 16) -> str:
    """Klassische Hex-Ansicht: Offset · Bytes · ASCII."""
    lines = []
    for off in range(0, min(len(data), limit), width):
        chunk = data[off:off + width]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        half = width // 2 * 3
        hexs = hexs[:half] + " " + hexs[half:] if len(chunk) > width // 2 else hexs
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:08x}  {hexs:<{width * 3}}  |{asc}|")
    return "\n".join(lines)


def decode_text(data: bytes, limit: int = 2 * 1024 * 1024) -> str:
    d = data[:limit]
    for enc in ("utf-8-sig", "utf-16") if d.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig",):
        try:
            return d.decode(enc)
        except UnicodeDecodeError:
            continue
    return d.decode("cp1252", "replace")
