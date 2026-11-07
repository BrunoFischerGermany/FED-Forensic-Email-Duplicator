"""Zeitstempel nach RFC 3161 für den SHA-256 des ZIP-Archivs.

Ablauf: ``TimeStampReq`` (SHA-256, Zufalls-Nonce, ``certReq``) per HTTP an den
Zeitstempeldienst (TSA), Antwort prüfen (Status, Hash, Nonce) und Anfrage sowie
Antwort neben dem ZIP ablegen (``.tsq``/``.tsr``). Die Signatur der Antwort lässt
sich unabhängig mit OpenSSL prüfen::

    openssl ts -verify -in FALL.zip.tsr -data FALL.zip -CAfile tsa-kette.pem

Ohne Fremdpakete: der kleine DER-Kodierer/-Parser unten reicht für die
benötigten Strukturen (definite Längen, wie von RFC 3161 für TSTInfo verlangt).
"""

from __future__ import annotations

import secrets
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SHA256_OID = "2.16.840.1.101.3.4.2.1"
SIGNED_DATA_OID = "1.2.840.113549.1.7.2"
TST_INFO_OID = "1.2.840.113549.1.9.16.1.4"

STATUS_TEXT = {0: "granted", 1: "grantedWithMods", 2: "rejection", 3: "waiting", 4: "revocationWarning",
               5: "revocationNotification"}


class TimestampError(RuntimeError):
    """Zeitstempel nicht erhalten oder Antwort passt nicht zur Anfrage."""


# ---------------------------------------------------------------- DER kodieren
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _len(len(content)) + content


def _int(v: int) -> bytes:
    b = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, b)


def _oid(dotted: str) -> bytes:
    parts = [int(p) for p in dotted.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def build_request(digest: bytes, nonce: int, cert_req: bool = True) -> bytes:
    """``TimeStampReq`` (RFC 3161, Abschnitt 2.4.1) für einen SHA-256-Wert."""
    if len(digest) != 32:
        raise ValueError("SHA-256 erwartet (32 Byte)")
    imprint = _seq(_seq(_oid(SHA256_OID), b"\x05\x00"), _tlv(0x04, digest))
    return _seq(_int(1), imprint, _int(nonce), _tlv(0x01, b"\xff" if cert_req else b"\x00"))


# ---------------------------------------------------------------- DER lesen
@dataclass
class _El:
    tag: int
    content: bytes

    def children(self) -> list[_El]:
        return _parse_all(self.content)

    def as_int(self) -> int:
        return int.from_bytes(self.content, "big", signed=True)

    def as_oid(self) -> str:
        c = self.content
        parts = [c[0] // 40, c[0] % 40]
        v = 0
        for b in c[1:]:
            v = (v << 7) | (b & 0x7F)
            if not b & 0x80:
                parts.append(v)
                v = 0
        return ".".join(map(str, parts))


def _parse_one(data: bytes, pos: int = 0) -> tuple[_El, int]:
    if pos + 2 > len(data):
        raise TimestampError("DER: unerwartetes Ende")
    tag = data[pos]
    if tag & 0x1F == 0x1F:
        raise TimestampError("DER: lange Tag-Nummern werden nicht unterstützt")
    n = data[pos + 1]
    pos += 2
    if n == 0x80:
        raise TimestampError("DER: unbestimmte Länge (BER) wird nicht unterstützt")
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(data[pos:pos + k], "big")
        pos += k
    if pos + n > len(data):
        raise TimestampError("DER: Länge über das Ende hinaus")
    return _El(tag, data[pos:pos + n]), pos + n


def _parse_all(data: bytes) -> list[_El]:
    out, pos = [], 0
    while pos < len(data):
        el, pos = _parse_one(data, pos)
        out.append(el)
    return out


@dataclass
class TimestampInfo:
    status: int
    status_text: str
    gen_time: str = ""  # ISO 8601 UTC
    serial: str = ""
    policy: str = ""
    hash_oid: str = ""
    hashed_message: str = ""
    nonce: int | None = None
    accuracy: str = ""
    tsa_url: str = ""
    failure: list[str] = field(default_factory=list)


def _gen_time(raw: bytes) -> str:
    s = raw.decode("ascii").rstrip("Z")
    frac = ""
    if "." in s:
        s, frac = s.split(".", 1)
    dt = datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{iso}.{frac}Z" if frac else f"{iso}Z"


def parse_response(der: bytes) -> TimestampInfo:
    """Liest ``TimeStampResp`` und daraus ``TSTInfo`` (ohne Signaturprüfung)."""
    resp, _ = _parse_one(der)
    if resp.tag != 0x30:
        raise TimestampError("keine TimeStampResp")
    parts = resp.children()
    st = parts[0].children()
    status = st[0].as_int()
    info = TimestampInfo(status, STATUS_TEXT.get(status, str(status)))
    for el in st[1:]:
        if el.tag == 0x30:
            info.failure = [c.content.decode("utf-8", "replace") for c in el.children()]
    if status not in (0, 1):
        return info
    if len(parts) < 2:
        raise TimestampError("Antwort ohne Zeitstempel-Token")
    ci = parts[1].children()
    if ci[0].as_oid() != SIGNED_DATA_OID:
        raise TimestampError("Token ist kein SignedData")
    signed = ci[1].children()[0].children()
    encap = signed[2].children()
    if encap[0].as_oid() != TST_INFO_OID:
        raise TimestampError("Token enthält kein TSTInfo")
    tst = _parse_one(encap[1].children()[0].content)[0].children()
    info.policy = tst[1].as_oid()
    mi = tst[2].children()
    info.hash_oid = mi[0].children()[0].as_oid()
    info.hashed_message = mi[1].content.hex()
    info.serial = format(tst[3].as_int(), "x")
    info.gen_time = _gen_time(tst[4].content)
    for el in tst[5:]:
        if el.tag == 0x30:  # accuracy
            vals = []
            for a in el.children():
                unit = {0x02: "s", 0x80: "ms", 0x81: "µs"}.get(a.tag, "?")
                vals.append(f"{int.from_bytes(a.content, 'big')} {unit}")
            info.accuracy = " ".join(vals)
        elif el.tag == 0x02:
            info.nonce = el.as_int()
    return info


# ---------------------------------------------------------------- Anfrage senden
def _post(url: str, body: bytes, proxy: str, timeout: float) -> bytes:
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply",
        "User-Agent": "FED-Forensic-Email-Duplicator"})
    with opener.open(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        data = r.read()
    if "timestamp-reply" not in ctype and not data.startswith(b"\x30"):
        raise TimestampError(f"unerwartete Antwort des Zeitstempeldienstes ({ctype or 'ohne Content-Type'})")
    return data


def request_timestamp(sha256_hex: str, tsa_url: str, out: Path, proxy: str = "", timeout: float = 30) -> TimestampInfo:
    """Holt einen Zeitstempel für ``sha256_hex`` und speichert ``out`` (.tsr) und ``out.with_suffix('.tsq')``.

    Prüft Status, Hash-Verfahren, Hashwert und Nonce; wirft :class:`TimestampError` bei Abweichung.
    """
    digest = bytes.fromhex(sha256_hex)
    nonce = secrets.randbits(63)
    query = build_request(digest, nonce)
    try:
        answer = _post(tsa_url, query, proxy, timeout)
    except OSError as e:
        raise TimestampError(f"Zeitstempeldienst nicht erreichbar: {e}") from e
    info = parse_response(answer)
    info.tsa_url = tsa_url
    if info.status not in (0, 1):
        raise TimestampError(f"Zeitstempel abgelehnt: {info.status_text} {' '.join(info.failure)}".strip())
    if info.hash_oid != SHA256_OID or info.hashed_message != sha256_hex.lower():
        raise TimestampError("Zeitstempel bezieht sich nicht auf den angefragten Hash")
    if info.nonce != nonce:
        raise TimestampError("Nonce der Antwort passt nicht zur Anfrage")
    out.with_suffix(".tsq").write_bytes(query)
    out.write_bytes(answer)
    return info
