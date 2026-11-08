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
- Feste Namen im Case-Ordner auf Englisch, unabhängig von der Sprache: `work/`,
  `discarded/attempt_<n>/`, `report.txt`, `report.pdf`
- Ersteinrichtung: fehlt die `config.toml` oder sind Agentur/Examiner leer, fragt die
  Oberfläche beim Start die Grunddaten ab (Agentur, Anschrift, Logo, Examiner, Sprache, Zielordner)
- Fertige Programme per GitHub Actions (`Build`): Windows x64, macOS Apple Silicon,
  macOS Intel, Linux x64; bei Tag `v*` automatisch als GitHub-Release mit SHA256SUMS
- Meilenstein 6:
  - Zeitstempel nach RFC 3161 für den ZIP-Hash (`tsa_url`), Antwort als `.tsr` neben dem ZIP,
    Prüfung von Hash und Nonce, Abschnitt im Bericht mit `openssl ts -verify`-Befehl
  - Validierungsprotokoll `python -m validation.run`: 12 Prüfungen gegen das Testpostfach,
    PDF/TXT auf Deutsch und Englisch plus JSON; hängt an jedem Release
  - Englische Oberfläche (`sprache = "en"`), beim ersten Start nach Systemsprache;
    Übersetzungen in `src/fed/i18n/en.json`, Werkzeug `packaging/i18n.py`
  - SHA-256 des Programms wird je Case festgehalten (bisher leer)
- Umlaute in E-Mail-Adressen: Anmeldung mit Umlauten in Benutzername/Passwort per
  `AUTHENTICATE PLAIN` (vorher Programmfehler bei LOGIN), Domains und Hostnamen nach
  IDNA 2008 (`straße.de` -> `xn--strae-oqa.de`, vorher fälschlich `strasse.de`),
  Adresse in Autoconfig-URLs kodiert, Eingaben in Unicode-Normalform NFC;
  Testkonto `bärbel.böhmer@ärzte.de` und Validierungspunkt V13
- README: Englisch ist Standard (`README.md`), Deutsch in `README.de.md`
- Weitere Sprachen ohne Programmierung: Sprachen werden aus `src/fed/i18n` erkannt,
  Berichtstexte in `report_<sprache>.json`, `packaging/i18n.py --new <code>` legt eine neue
  Sprache an und prüft Vollständigkeit und Platzhalter; Anleitung `docs/translating.md`
- Google per App-Passwort: eigene Anmeldeart „Google (App-Passwort)“ (CLI `--auth google-app`),
  automatisch gewählt, wenn keine Google-Client-ID eingerichtet ist; Leerzeichen der
  Google-Anzeige werden entfernt, Gmail-Anmeldefehler mit Hinweis auf App-Passwort/OAuth,
  im Bericht als „Google-App-Passwort“
- Releases automatisch: „Publish release“ auf GitHub startet den Build, die Programme
  (Windows als einzelne `.exe`), `SHA256SUMS.txt` und das Validierungsprotokoll werden
  angehängt, die Validierungstabelle an die Notizen angefügt; Selbsttest jedes Pakets
  (`packaging/selftest.py`), Version aus dem Tag; Nightly-Vorabversion bei neuen Commits;
  Anleitung `docs/releases.md`
- Mehrere Konten im Case: Übersicht „Bereits in diesem Case“ (Evidence, Konto, Status, Mails
  gesichert/Server, Größe, Ordner, Versuche, zuletzt) beim Einrichten des nächsten Kontos und nach
  jeder Sicherung, Kurzliste „Konten im Case“ in der linken Leiste; Doppelklick übernimmt ein Konto
  zum Fortsetzen; Warnung beim Abschließen, wenn ein Konto nicht vollständig gesichert ist
- Evidence-Nummer wird vorgeschlagen: letzte Zahl der zuletzt vergebenen Nummer +1
  (`A-01` -> `A-02`, `VG-2026-009` -> `VG-2026-010`), vergebene Nummern werden übersprungen,
  doppelte Vergabe für ein anderes Konto abgelehnt
- ZIP-Aufteilung bei mehreren Konten wählbar: ein ZIP für alle Konten oder eines je Konto
  (`<Case>_<Evidence>_<Zeit>.zip` mit eigener `log.csv`, `.sha256` und Zeitstempel); Frage beim
  2. Konto (änderbar bis zum Abschluss), Voreinstellung `zip_aufteilung` (fragen | gesamt | je_konto),
  CLI `fed finish --zip gesamt|je-konto`; Wahl wird im Case gespeichert und protokolliert,
  Bericht nennt sie und je Konto das eigene ZIP
- Notiz je Konto (Kontoseite, CLI `--account-notes`), Änderungen mit altem/neuem Text im Log;
  ältere Case-Dateien erhalten die Spalte beim Öffnen (protokolliert)
- Bericht: Anmerkungen zum Case als eigener Abschnitt oben, Kontenübersicht, je Konto ein Abschnitt
  mit Notiz, Versuchen und Ordnern; bei mehreren Konten zusätzlich `report_<Evidence>.txt/.pdf`
  je Konto
- Oberfläche: Konten in der linken Leiste anklickbar (übernimmt das Konto zum Fortsetzen); neues
  Konto setzt die Anmeldeart auf „Passwort“ zurück; bei abgelehnter Anmeldung Hinweis auf
  IMAP-Freischaltung/App-Passwort (Gmail: App-Passwort/OAuth); Sicherungsseite zeigt „Konto x von y“
  und die Kontenübersicht; Reiter „Konten“ in der Historie; Knopf „FED beenden“ am Ende;
  Feld „Notes“ heißt „Anmerkungen“
- Prüfung: unerwartete Fehler (z. B. Datenträger voll) beenden einen Versuch als „fehlgeschlagen“
  statt ihn auf „läuft“ stehen zu lassen; Befehlssperre blockiert zusätzlich FETCH ohne PEEK
  (würde das Gelesen-Flag setzen, Validierung V02); Kontenübersicht vergleicht mit dem
  Serverstand nach der Sicherung

## 0.4-beta

Kommandozeilen-Skript, siehe `legacy/`.
