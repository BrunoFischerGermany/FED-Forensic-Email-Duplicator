# Changelog

## 1.0.0 (in Entwicklung)

- Neuer Name: **FED – Forensic Email Duplicator** (vorher „Forensic Email Downloader“)
- Neuaufbau als Python-3.14-Paket mit PySide6-Oberfläche und Kommandozeile
- Lizenz GPL-3.0-only (v0.4 unter `legacy/` bleibt MIT)
- Config-Datei `config.toml` für Agentur, Logo, Examiner, OAuth, Proxy
- Case-Datei (SQLite) mit nur anhängendem, per Hash-Kette gesichertem Log
- Dateinamen `<Eingangszeit UTC>_UID<n>.eml`, SHA-256 + MD5 + SHA-512
- Blockade schreibender IMAP-Befehle
- Meilenstein 2: nur lesender IMAP-Zugriff (EXAMINE, BODY.PEEK, stückweises Laden, TLS-Fingerprint),
  Autoerkennung in 6 Stufen, Erkennung externer Datenträger, Scan, Download in den Arbeitsordner
  mit Hashes, Eingangszeit als Dateizeit, Empfänger/Anlagen, Vorher/Nachher-Status,
  Versuche mit Fortsetzen, Neubeginn (verschieben/löschen) und Abbrechen, `fed acquire`
- Testpostfach (Dovecot) mit erwarteten Hashes, Integrationstests in der CI

## 0.4-beta

Kommandozeilen-Skript, siehe `legacy/`.
