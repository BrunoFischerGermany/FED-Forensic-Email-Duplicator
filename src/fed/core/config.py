"""Laden, Prüfen und Speichern der ``config.toml``.

Die Config enthält feste Grunddaten der Agentur und Programmeinstellungen.
Sie enthält nie Passwörter oder Tokens. Beim Anlegen eines Case wird eine
Kopie (Text + Logo-Hash) in die Case-Datei übernommen (``config_snapshot``).
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from fed.core.msg import t
from fed.core.naming import DEFAULT_ZIP_PATTERN, zip_basename

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
    bericht_dkimliste: str = "auto"  # auto | liste | ordner – Liste der DKIM-Prüfungen
    bericht_dkimliste_max: int = 500  # bei "auto": DKIM-Liste nur bis zu so vielen Einträgen
    bericht_dns: str = "csv"  # csv | tabelle – DNS-Abfragen (Schnappschuss + aufbewahrte Schlüssel)
    design: str = "system"  # system | hell | dunkel
    zielpfad: str = ""
    ntp: bool = True
    ntp_server: str = "ptbtime1.ptb.de"
    hash_extra: list[str] = field(default_factory=lambda: ["md5", "sha512"])
    zip_zeit_quelle: str = "internaldate"
    socket_timeout_s: int = 30
    chunk_mb: int = 10
    verbindungen: int = 3  # gleichzeitige IMAP-Verbindungen beim Download (1–10)
    wach_halten: bool = True
    dkim_pruefen: bool = True  # DKIM-Signaturen bei der Sicherung prüfen (braucht DNS)
    leerzeichen_ersetzen: bool = True  # Leerzeichen in Case-Ordner- und Dateinamen durch _ ersetzen
    verschluesselung: str = "fragen"  # Dialog beim Abschluss: fragen | aus (Checkbox im Dialog ist standardmäßig aus)
    zip_aufteilung: str = "fragen"  # bei mehreren Konten: fragen | gesamt | je_konto
    zip_name_muster: str = DEFAULT_ZIP_PATTERN  # Muster für den ZIP-Dateinamen (Platzhalter siehe naming.zip_basename)
    arbeitsordner_loeschen: str = "fragen"  # nach verifiziertem ZIP: fragen | ja | nein  # Ruhezustand/Bildschirmsperre während der Sicherung verhindern


@dataclass
class OAuthGoogle:
    client_id: str = ""
    client_secret: str = ""


# App-Registrierung des FED-Projekts in Microsoft Entra (öffentlicher Client, kein Secret; mehrinstanzenfähig
# und für persönliche Microsoft-Konten). Die Client-ID ist nicht geheim. Eine eigene ID in den Einstellungen
# (z. B. aus dem Tenant der Behörde) hat Vorrang.
FED_MICROSOFT_CLIENT_ID = "8de90383-c0ec-4221-9ed6-bd9300cd455c"


@dataclass
class OAuthMicrosoft:
    client_id: str = ""  # leer = App-Registrierung von FED
    tenant: str = "organizations"

    @property
    def effective_client_id(self) -> str:
        return self.client_id.strip() or FED_MICROSOFT_CLIENT_ID

    @property
    def builtin(self) -> bool:
        return not self.client_id.strip()


@dataclass
class Config:
    agentur: Agentur = field(default_factory=Agentur)
    examiner_name: str = ""
    case_nummer_muster: str = "{lfd:05}/{jahr:2}"
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
        raise ConfigError(t("Unbekannte Hash-Verfahren in hash_extra: {values}", values=sorted(unknown)))
    if cfg.programm.zip_zeit_quelle not in ALLOWED_ZIP_TIME_SOURCES:
        raise ConfigError(t("{key} muss eins von {values} sein", key="zip_zeit_quelle", values=sorted(ALLOWED_ZIP_TIME_SOURCES)))
    from fed.core.languages import report_languages, ui_languages

    if cfg.programm.sprache not in ui_languages():
        raise ConfigError(t("{key} muss eins von {values} sein", key="sprache", values=ui_languages()))
    if cfg.programm.bericht_sprache not in {"", *report_languages()}:
        raise ConfigError(t("{key} muss leer oder eins von {values} sein", key="bericht_sprache", values=report_languages()))
    if cfg.programm.verschluesselung not in {"fragen", "aus"}:
        raise ConfigError(t("{key} muss eins von {values} sein", key="verschluesselung", values=["fragen", "aus"]))
    if cfg.programm.zip_aufteilung not in {"fragen", "gesamt", "je_konto"}:
        raise ConfigError(t("{key} muss eins von {values} sein", key="zip_aufteilung", values=["fragen", "gesamt", "je_konto"]))
    if not zip_basename(cfg.programm.zip_name_muster, case_number="2026-0001", evidence="A-01",
                        email="max@example.de").strip():
        raise ConfigError(t("{key} ergibt keinen gültigen Dateinamen", key="zip_name_muster"))
    if cfg.programm.arbeitsordner_loeschen not in {"fragen", "ja", "nein"}:
        raise ConfigError(t("{key} muss eins von {values} sein", key="arbeitsordner_loeschen", values=["fragen", "ja", "nein"]))
    if not 1 <= cfg.programm.verbindungen <= 10:
        raise ConfigError(t("{key} muss zwischen {lo} und {hi} liegen", key="verbindungen", lo=1, hi=10))
    for key in ("bericht_mailliste", "bericht_dkimliste"):
        val = {"immer": "liste", "nie": "ordner"}.get(getattr(cfg.programm, key), getattr(cfg.programm, key))
        setattr(cfg.programm, key, val)
        if val not in {"auto", "liste", "ordner"}:
            raise ConfigError(t("{key} muss eins von {values} sein", key=key, values=["auto", "liste", "ordner"]))
    if cfg.programm.bericht_dns not in {"csv", "tabelle"}:
        raise ConfigError(t("{key} muss eins von {values} sein", key="bericht_dns", values=["csv", "tabelle"]))
    if cfg.programm.design not in {"system", "hell", "dunkel"}:
        raise ConfigError(t("{key} muss eins von {values} sein", key="design", values=["system", "hell", "dunkel"]))


def save(cfg: Config, path: Path) -> None:
    """Schreibt die Config (Kommentare der Vorlage gehen dabei verloren)."""
    import tomli_w

    path.write_text(tomli_w.dumps(_to_dict(cfg)), encoding="utf-8")


def _from_dict(d: dict) -> Config:
    oauth = d.get("oauth", {})
    return Config(
        agentur=Agentur(**d.get("agentur", {})),
        examiner_name=d.get("examiner", {}).get("name", ""),
        case_nummer_muster=d.get("case", {}).get("nummer_muster", "{lfd:05}/{jahr:2}"),
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
