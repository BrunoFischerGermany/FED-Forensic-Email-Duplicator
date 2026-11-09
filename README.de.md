<p align="center"><img src="assets/logo/fed-logo.svg" width="140" alt="FED-Logo"></p>

# FED – Forensic Email Duplicator

*Forensische Sicherung von E-Mail-Postfächern*

[English](README.md) · **Deutsch**

> **Branch `v1.0` – in Entwicklung.** Neuaufbau mit GUI. Das bisherige Kommandozeilen-Skript v0.4-beta liegt unter [`legacy/`](legacy/) (MIT-Lizenz).

FED sichert E-Mail-Postfächer per IMAP forensisch und nur lesend:

- jede Mail unverändert als `.eml`, benannt nach Eingangszeit und UID (`20261106T143012Z_UID00004711.eml`)
- SHA-256 (zusätzlich MD5 und SHA-512) pro Mail und für das ZIP-Archiv
- optional verschlüsselte Kopie für die Weitergabe (AES-256, Passwort wird nie gespeichert)
- ZIP mit der Ordnerstruktur des Postfachs, erste Ebene = E-Mail-Adresse; bei mehreren Konten wählbar: ein ZIP für alle oder eines je Konto
- Case-Datei (SQLite) mit manipulationsgeschütztem Log, Historie, mehreren Versuchen, Fortsetzen/Neubeginn
- mehrere Konten je Case mit Übersicht, was schon gesichert ist; Evidence-Nummer wird vorgeschlagen (`A-01` → `A-02`); Konten können parallel im Hintergrund gesichert werden
- Passwörter nur im Arbeitsspeicher: nach erfolgreichem Test für die Sitzung gemerkt (die betroffene Person gibt es oft nur einmal ein), nie auf der Festplatte oder im Log, beim Beenden von FED verworfen
- TXT- und PDF-Bericht mit Agentur-Logo, Anmerkungen zum Case und Notiz je Konto – gesamt und je Konto; optional RFC-3161-Zeitstempel
- Gmail/Google Workspace und Microsoft 365 per OAuth2
- Deutsch und Englisch, weitere Sprachen ohne Programmierung ergänzbar ([docs/translating.md](docs/translating.md), englisch); Windows/macOS/Linux, portabel ohne Installation

## Stand

| Meilenstein | Inhalt | Status |
| --- | --- | --- |
| 1 | Projektstruktur, Config, Case-Datei mit Hash-Kette, Dateinamen, Hashes, GUI-Gerüst | ✅ |
| 2 | Systemprüfung, Autoerkennung, IMAP nur lesend, Scan, Download in Arbeitsordner, Testpostfach | ✅ |
| 3 | OAuth2 Google / Microsoft 365 (Browser + PKCE, Gerätecode, Token-Erneuerung) | ✅ |
| 4 | ZIP mit Zeitstempeln, Verifikation, Berichte TXT/PDF, Oberfläche (Assistent, Historie, Einstellungen, Hell/Dunkel) | ✅ |
| 5 | Fertige Programme (Nuitka) für Windows, macOS, Linux | ✅ |
| 6 | RFC-3161-Zeitstempel, Validierungsprotokoll, englische Oberfläche | ✅ |

## Entwicklung

Python 3.14 wird vorausgesetzt.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
fed-gui                    # Oberfläche
fed --help                 # Kommandozeile
```

Beispiel Kommandozeile:

```bash
fed new-case --target E:/FED-Cases --case 2026-0042 --examiner "Max Mustermann"
fed acquire --case-dir E:/FED-Cases/2026-0042 --account max@example.de --evidence A-01
#   Passwort wird abgefragt (oder Umgebungsvariable FED_PASSWORD)
#   erneut aufrufen = Fortsetzen; --force --reason "…" = Neubeginn
#   Gmail / Microsoft 365: Anmeldung automatisch per OAuth im Browser
#   (Einrichtung der Client-IDs: docs/oauth-einrichtung.md)
fed finish --case-dir E:/FED-Cases/2026-0042   # ZIP + Hashes + report.pdf/.txt, Case abschließen
fed verify --case-dir E:/FED-Cases/2026-0042
```

Validierungsprotokoll (PDF/TXT, de/en) gegen das Testpostfach: `python -m validation.run`
(siehe [validation/README.md](validation/README.md); hängt auch an jedem Release).

Tests gegen einen echten IMAP-Server: `apt install dovecot-imapd`, dann `pytest`
(startet automatisch einen lokalen Dovecot mit dem Testpostfach aus `validation/`).

Konfiguration: `config.example.toml` nach `config.toml` neben das Programm kopieren und anpassen –
oder einfach starten: ohne Grunddaten fragt die Oberfläche sie beim ersten Start ab.

## Übersetzen

Neue Sprachen brauchen keine Programmierung: je eine JSON-Datei für Oberfläche und Bericht.
Anleitung (englisch): [docs/translating.md](docs/translating.md).

```bash
python packaging/i18n.py --new fr   # legt src/fed/i18n/fr.json + report_fr.json an
python packaging/i18n.py            # erzeugt fed_fr.qm – die Sprache erscheint in den Einstellungen
```

## Releases und fertige Programme

Releases entstehen automatisch: auf GitHub **Releases → Draft a new release**, Tag anlegen
(z. B. `v1.0.0`), **Publish release** klicken. Der Workflow *Build* baut alle Plattformen, testet
jedes Paket, validiert und hängt alles an das Release:

| Datei | |
|---|---|
| `FED-<version>-windows-x64.exe` | Windows – eine einzelne Datei, Doppelklick startet |
| `FED-<version>-macos-arm64.zip` / `-macos-x64.zip` | macOS Apple Silicon / Intel (`FED.app`) |
| `FED-<version>-linux-x64.tar.gz` | Linux (`FED/FED`) |
| `FED-<version>-validation-report-*.pdf` | Validierungsprotokoll (13 Prüfungen) |
| `SHA256SUMS.txt` | Prüfsummen |

Jede Nacht entsteht bei neuen Commits eine Vorabversion **Nightly** von `main`.
Ohne Release: **Actions → Build → Run workflow** (Dateien unter *Artifacts*) oder lokal
`pip install -e .[build]` und `python packaging/build.py`. Anleitung: [docs/releases.md](docs/releases.md).

Die `config.toml` gehört neben `FED.exe`, `FED` bzw. `FED.app` – oder einfach starten, FED fragt die Grunddaten ab.
Die Programme sind nicht signiert: macOS meldet „nicht verifizierter Entwickler“
(Rechtsklick → Öffnen, oder `xattr -cr FED.app`), Windows SmartScreen „Weitere Informationen → Trotzdem ausführen“.

## Aufbau

```
src/fed/
├─ core/            Kern ohne GUI (testbar, auch für CLI)
│  ├─ config.py     config.toml laden/prüfen/speichern
│  ├─ case.py       Case-Datei, Log mit Hash-Kette
│  ├─ syscheck.py   Benutzer, Zeit/NTP, Internet, Datenträger, Speicher
│  ├─ autoconfig.py Thunderbird-Autoerkennung
│  ├─ oauth.py      Google / Microsoft 365
│  ├─ imap_reader.py nur lesender IMAP-Zugriff (schreibende Befehle blockiert)
│  ├─ acquisition.py Versuche, Download, ZIP, Verifikation
│  ├─ naming.py     Dateinamen, Pfade, Modified UTF-7
│  ├─ hashing.py    SHA-256 + MD5/SHA-512
│  ├─ cancel.py     Abbrechen, Fortschritt, Geschwindigkeit, Restzeit
│  ├─ report.py     TXT/PDF/log.csv
│  └─ timestamp.py  RFC 3161
├─ gui/             PySide6: Assistent, Historie, Statusleiste
├─ i18n/            Übersetzungen: <sprache>.json (Oberfläche), report_<sprache>.json (Bericht), .qm
├─ resources/       SQL-Schema
└─ cli.py           Kommandozeilen-Modus
docs/               Konzept-Zusammenfassung, OAuth-Einrichtung
validation/         Test-Postfach + erwartete Hashes
tests/
```

## Lizenz

GPL-3.0-only, siehe [LICENSE](LICENSE). Der Code unter `legacy/` bleibt unter MIT ([legacy/LICENSE-MIT](legacy/LICENSE-MIT)).

## Autoren

- [@BrunoFischerGermany](https://github.com/BrunoFischerGermany)
- Claude (Anthropic) – KI-Assistent, Code, Tests und Dokumentation ab v1.0 mitentwickelt
