"""Laden, Prüfen und Speichern der ``config.toml``.

Die Config enthält feste Grunddaten der Agentur und Programmeinstellungen.
Sie enthält nie Passwörter oder Tokens. Beim Anlegen eines Case wird eine
Kopie (Text + Logo-Hash) in die Case-Datei übernommen (``config_snapshot``).
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILENAME = "config.toml"
ALLOWED_EXTRA_HASHES = {"md5", "sha1", "sha512"}
ALLOWED_ZIP_TIME_SOURCES = {"internaldate", "date_header"}


@dataclass
class Agentur:
    name: str = ""
    adresse: str = ""
    logo: str = ""


@dataclass
class Programm:
    sprache: str = "de"
    bericht_sprache: str = ""  # leer = wie Oberfläche
    bericht_mailliste: str = "auto"  # auto | liste | ordner
    bericht_mailliste_max: int = 500  # bei "auto": Liste nur bis zu so vielen Mails
    design: str = "system"  # system | hell | dunkel
    zielpfad: str = ""
    ntp: bool = True
    ntp_server: str = "ptbtime1.ptb.de"
    hash_extra: list[str] = field(default_factory=lambda: ["md5", "sha512"])
    zip_zeit_quelle: str = "internaldate"
    socket_timeout_s: int = 30
    chunk_mb: int = 10
    verbindungen: int = 3  # gleichzeitige IMAP-Verbindungen beim Download (1–10)
    wach_halten: bool = True  # Ruhezustand/Bildschirmsperre während der Sicherung verhindern


@dataclass
class OAuthGoogle:
    client_id: str = ""
    client_secret: str = ""


@dataclass
class OAuthMicrosoft:
    client_id: str = ""
    tenant: str = "organizations"


@dataclass
class Config:
    agentur: Agentur = field(default_factory=Agentur)
    examiner_name: str = ""
    case_nummer_muster: str = "{jahr}-{lfd:04}"
    programm: Programm = field(default_factory=Programm)
    proxy_url: str = ""
    tsa_url: str = ""
    oauth_google: OAuthGoogle = field(default_factory=OAuthGoogle)
    oauth_microsoft: OAuthMicrosoft = field(default_factory=OAuthMicrosoft)
    source_path: Path | None = None
    raw_text: str = ""

    def logo_path(self) -> Path | None:
        """Absoluter Pfad zum Logo oder ``None``."""
        if not self.agentur.logo:
            return None
        base = self.source_path.parent if self.source_path else Path.cwd()
        return (base / self.agentur.logo).resolve()


class ConfigError(ValueError):
    """Ungültige Konfiguration."""


def load(path: Path) -> Config:
    """Liest und prüft eine Config-Datei. Fehlende Werte erhalten Standards."""
    raw_text = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw_text)
    cfg = _from_dict(data)
    cfg.source_path = path.resolve()
    cfg.raw_text = raw_text
    validate(cfg)
    return cfg


def load_default(program_dir: Path) -> Config:
    """Lädt ``config.toml`` aus dem Programmordner, sonst Standardwerte."""
    path = program_dir / CONFIG_FILENAME
    if path.exists():
        return load(path)
    return Config()


def validate(cfg: Config) -> None:
    unknown = set(cfg.programm.hash_extra) - ALLOWED_EXTRA_HASHES
    if unknown:
        raise ConfigError(f"Unbekannte Hash-Verfahren in hash_extra: {sorted(unknown)}")
    if cfg.programm.zip_zeit_quelle not in ALLOWED_ZIP_TIME_SOURCES:
        raise ConfigError(f"zip_zeit_quelle muss eins von {sorted(ALLOWED_ZIP_TIME_SOURCES)} sein")
    if cfg.programm.sprache not in {"de", "en"}:
        raise ConfigError("sprache muss 'de' oder 'en' sein")
    if cfg.programm.bericht_sprache not in {"", "de", "en"}:
        raise ConfigError("bericht_sprache muss leer, 'de' oder 'en' sein")
    if not 1 <= cfg.programm.verbindungen <= 10:
        raise ConfigError("verbindungen muss zwischen 1 und 10 liegen")
    cfg.programm.bericht_mailliste = {"immer": "liste", "nie": "ordner"}.get(cfg.programm.bericht_mailliste,
                                                                            cfg.programm.bericht_mailliste)
    if cfg.programm.bericht_mailliste not in {"auto", "liste", "ordner"}:
        raise ConfigError("bericht_mailliste muss 'auto', 'liste' oder 'ordner' sein")
    if cfg.programm.design not in {"system", "hell", "dunkel"}:
        raise ConfigError("design muss 'system', 'hell' oder 'dunkel' sein")


def save(cfg: Config, path: Path) -> None:
    """Schreibt die Config (Kommentare der Vorlage gehen dabei verloren)."""
    import tomli_w

    path.write_text(tomli_w.dumps(_to_dict(cfg)), encoding="utf-8")


def _from_dict(d: dict) -> Config:
    oauth = d.get("oauth", {})
    return Config(
        agentur=Agentur(**d.get("agentur", {})),
        examiner_name=d.get("examiner", {}).get("name", ""),
        case_nummer_muster=d.get("case", {}).get("nummer_muster", "{jahr}-{lfd:04}"),
        programm=Programm(**d.get("programm", {})),
        proxy_url=d.get("proxy", {}).get("url", ""),
        tsa_url=d.get("zeitstempel", {}).get("tsa_url", ""),
        oauth_google=OAuthGoogle(**oauth.get("google", {})),
        oauth_microsoft=OAuthMicrosoft(**oauth.get("microsoft", {})),
    )


def _to_dict(cfg: Config) -> dict:
    return {
        "agentur": asdict(cfg.agentur),
        "examiner": {"name": cfg.examiner_name},
        "case": {"nummer_muster": cfg.case_nummer_muster},
        "programm": asdict(cfg.programm),
        "proxy": {"url": cfg.proxy_url},
        "zeitstempel": {"tsa_url": cfg.tsa_url},
        "oauth": {
            "google": asdict(cfg.oauth_google),
            "microsoft": asdict(cfg.oauth_microsoft),
        },
    }
