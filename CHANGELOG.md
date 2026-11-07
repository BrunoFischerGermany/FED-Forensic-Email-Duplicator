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
- Meilenstein 3: OAuth2 für Google und Microsoft 365 (Authorization Code + PKCE mit
  Loopback-Redirect, Gerätecode für Microsoft, automatische Token-Erneuerung bei
  Reconnect, Protokoll des zustimmenden Kontos, Warnung bei Abweichung), XOAUTH2-Login,
  `fed acquire --auth/--device-code/--login-as`, Anleitung docs/oauth-einrichtung.md;
  Tests mit nachgebautem OAuth-Anbieter und echtem XOAUTH2 gegen Dovecot
- Meilenstein 4: ZIP (nur gültige Mails, leere Ordner, ZIP-Zeit UTC + Extended Timestamp,
  ZIP64) mit Verifikation und Hashes, log.csv, Berichte TXT/PDF mit Logo aus der Case-Datei,
  `fed finish`; Oberfläche nach Mockup: Assistent (Case/Systemprüfung, Konto mit
  Autoerkennung/Passwort/OAuth/Verbindungstest, Ordnerbaum mit Zeitraum, Sicherung mit
  Fortschritt/Abbrechen/Fortsetzen/Neubeginn, Abschluss), Historie, Einstellungen,
  Hell/Dunkel; Case-Datei mit einer SQLite-Verbindung je Thread
- Rechner bleibt während Sicherung und Abschluss wach (keine Leerlauf-Sperre, kein Ruhezustand):
  Windows SetThreadExecutionState, macOS caffeinate, Linux gnome-session-inhibit/systemd-inhibit;
  abschaltbar (`wach_halten`), Verfahren wird im Log festgehalten
- Schnellerer Download: mehrere gleichzeitige IMAP-Verbindungen (`verbindungen`, Standard 3),
  große Ordner in Pakete geteilt, kleine Mails gebündelt (bis 50 Mails/8 MB je FETCH);
  lehnt der Server weitere Verbindungen ab, geht es mit weniger weiter

## 0.4-beta

Kommandozeilen-Skript, siehe `legacy/`.
