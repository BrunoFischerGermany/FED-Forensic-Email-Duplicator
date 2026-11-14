# Changelog

## 1.0.7 (in Entwicklung)

- Apple-Mail-Import (erster Baustein, nur Lesen): `fed.core.applemail` liest ein macOS-`~/Library/Mail/V#`-
  Verzeichnis – Konten (UUID-Ordner), verschachtelte `.mbox`-Ordner, `.emlx`/`.partial.emlx` byte-genau
  (Längenzeile + N Bytes rohe Mail), Flags/Datum aus dem Apple-Plist. Konto→Adresse maßgeblich aus
  `~/Library/Accounts/Accounts4.sqlite` (inkl. `IdentityEmailAddress`, Aliase, Name), sonst Heuristik aus
  den Kopfzeilen zur Bestätigung. Noch ohne GUI/Sicherungs-Anbindung (folgt)
- Yahoo: wird erkannt; FED stellt den von Yahoo für Sicherungen genannten Server `export.imap.mail.yahoo.com`
  ein (steht im Protokoll der Autoerkennung) und zeigt unter dem Passwortfeld den Hinweis auf das Yahoo-App-Passwort
  mit Link zur Kontosicherheit und zur Anleitung (neuer Abschnitt „Yahoo“)
- Google: „Google (OAuth)“ erscheint nur noch, wenn in den Einstellungen eine eigene Google-Client-ID eingetragen
  ist (FED hat keine eigene Google-App); Standard für Gmail ist das App-Passwort. Anleitung entsprechend umgebaut
  (App-Passwort zuerst, eigenes Google-Projekt optional), Links im Google-Hinweis anklickbar
- Outlook.com/Exchange: Sicherung brach mit „UID …: … Bytes geladen, Server meldet …“ ab, weil Exchange als
  RFC822.SIZE nur einen Schätzwert liefert. Die Vollständigkeit wird jetzt über die Längenangabe der
  IMAP-Antwort selbst geprüft (unvollständige Übertragung bleibt ein Fehler); weicht RFC822.SIZE ab, steht
  einmal je Versuch eine Warnung `size_differs` mit Anzahl und Beispielen im Log, beide Werte je Mail in der
  Fall-Datei. Stückweises Laden großer Mails endet am ersten kurzen Stück statt an RFC822.SIZE (eine zu
  kleine Angabe hätte sonst abgeschnitten)
- Microsoft ohne Einrichtung: FED bringt eine eigene App-Registrierung mit (mehrinstanzenfähig und für
  private Konten, öffentlicher Client ohne Secret). Bleibt *Microsoft Client-ID* in den Einstellungen leer,
  wird sie genutzt; eine eigene Client-ID hat weiter Vorrang. Das Log (`oauth_login`) nennt die verwendete
  Client-ID. Anleitung: Freigabe-Link für Administratoren von Organisationen, die nur freigegebene Apps zulassen
- Verbindungstest Microsoft: bei „User is authenticated but not connected“ (Anmeldung ok, Postfach abgelehnt)
  konkrete Hinweise (IMAP erlauben, Anmeldung bestätigen, andere Programme schließen); meldet sich jemand
  mit einem anderen Konto an als dem zu sichernden, steht das direkt bei der Fehlermeldung
- Gerätecode: Code groß angezeigt und automatisch in der Zwischenablage („Erneut kopieren“), Adresse anklickbar;
  Webadressen in Hinweisen öffnen den Browser
- Mail als PDF: im Mail-Betrachter „Als PDF exportieren …“, in der Historie „Markierte Mails als PDF …“
  (mehrere Mails in einem PDF, je Mail eine neue Seite). Inhalt: Zuordnung (Konto, Ordner, Datei im Archiv,
  SHA-256 mit Prüfung, UID/UIDVALIDITY, INTERNALDATE), Kopfdaten, Echtheit (DKIM/SPF/DMARC), Text (bei reinen
  HTML-Mails die Textfassung, ohne externe Inhalte), eingebettete Bilder, Anlagen mit erkanntem Format und
  Auffälligkeiten, Weg (Received) und vollständige Kopfzeilen. Die Original-Mail (.eml) ist im PDF eingebettet
  (PDF/A-3, Beziehung „Source“); jeder Export steht mit SHA-256 des PDF im Log, eine Kopie liegt im Fall-Ordner
  unter `exports/` (nicht bei abgeschlossenem Fall). Sprache wie der Bericht des Falls
- Alle PDFs von FED (Bericht, Mail-Export, Validierungsbericht) sind PDF/A-3b; CI prüft sie mit veraPDF.
  Hinweis: PDF/A ist ein Archivformat (eingebettete Schriften, festgelegte Farben, Metadaten) – kein
  Manipulationsschutz; maßgeblich bleiben die SHA-256-Werte im Log
- Historie: die Aktualisierung alle 3 Sekunden setzt Markierung und Scrollposition nicht mehr zurück
- fpdf2 ≥ 2.9 erforderlich

## 1.0.6 (in Entwicklung)

- Testpostfach (Tests und Validierung) läuft mit Dovecot 2.3 **und** 2.4 – die Version wird erkannt und das
  passende Konfigurationsformat geschrieben; CI testet auf Ubuntu 24.04 (2.3) und 26.04 (2.4), die Validierung
  hängt nicht mehr an Ubuntu 24.04. `FED_DOVECOT` wählt ein anderes Dovecot-Programm
- Linux-Build im Container `ubuntu:22.04` (glibc 2.35) auf einem aktuellen Runner – gleiche Kompatibilität wie
  bisher, aber unabhängig davon, wie lange GitHub den Runner ubuntu-22.04 anbietet (ca. April 2027)
- Linux: alle Bibliotheken, gegen die Python gebaut ist (u. a. libsqlite3), werden ins Paket übernommen – vorher
  startete FED auf Systemen ohne installiertes SQLite nicht

## 1.0.5 (in Entwicklung)

- Schritt „Fall anlegen“ bei offenem Fall: „Weiter“ statt „Fall anlegen und weiter“, Felder gesperrt – kein
  „Fall existiert bereits“ mehr; gibt es den Fall im Zielordner schon, fragt FED, ob er geöffnet werden soll
- „+ Weiteres Konto“ in der linken Leiste und neben „Fall abschließen“
- „Postfach scannen“ ist immer klickbar und testet die Verbindung bei Bedarf selbst (auch für ein schon
  gesichertes Konto, z. B. zum Nachsichern)
- Notizen (Fall, Konto, Abschlussvermerk, Übersicht): Knöpfe „Datum“ und „Uhrzeit“ fügen die aktuelle
  Gerätezeit an der Cursorposition ein (11.11.2026 / 11:50 Uhr)
- Die Gerätefrage kommt unmittelbar nach dem Programmstart (Ja / Nein / FED beenden; Enter und Esc beenden)
  und gilt für alle Fälle der Sitzung; im Log steht zusätzlich der Zeitpunkt der Antwort. Systemprüfung
  zeigt „Ausführendes Gerät“. Nur wenn beim Start nicht gefragt wurde, fragt FED beim Fall.
- Beim Anlegen (und Fortsetzen) eines Falls fragt FED, ob es auf einem eigenen Gerät läuft: Ja = eigenes
  Gerät, Nein = Gerät der betroffenen Person, Abbrechen = nicht fortfahren, „FED beenden“ schließt das Programm. Die Antwort steht mit Rechnername
  im Log und im Bericht („Ausführendes Gerät“); CLI `fed new-case --device own|subject`
- Kontoseite: „Verbindung testen“ und „Postfach scannen“ oben im Kasten „Verbindungstest“, Fehlermeldungen
  direkt darunter; „Zurück“, „+ Weiteres Konto“ und „Fall abschließen“ bleiben beim Scrollen unten sichtbar
- Notizen: nach einem Klick auf B/I/U, Farbe, Marker, Liste oder Format bleibt der Cursor im Textfeld
- Ersteinrichtung: Anschrift einzeilig wie in den Einstellungen
- Qt-eigene Knöpfe (Ja/Nein, Abbrechen, Schließen, Dateidialog) werden im gebauten Programm sicher übersetzt;
  der Selbsttest prüft das

## 1.0.3 (in Entwicklung)

- macOS: Startet FED aus einer von macOS verschobenen Kopie („App Translocation“, z. B. direkt aus
  „Downloads“), werden config.toml und FED.ini neben dem echten FED.app gespeichert (sonst in
  ~/Library/Application Support/FED) statt im schreibgeschützten Temp-Ordner; Hinweis zur dauerhaften Lösung
- Windows: Auslieferung als ZIP statt selbstentpackender Einzeldatei – die löste Virenscanner-Fehlalarme aus
  (Symantec „Heur.AdvML.B“). Im Ordner `FED\` liegen nur `FED.exe` (kleines Startprogramm), `config.toml`
  und die Dokumente; das Programm mit allen Bibliotheken liegt in `FED\app\`. Startet außerdem schneller
- Windows: beim ersten Start (USB-Prüfung des Zielordners per PowerShell) öffnet sich kein
  Kommandozeilenfenster mehr

## 1.0.2

- Microsoft: Warnung, wenn bei einem Microsoft-Server Passwort/App-Passwort gewählt ist (dort nur OAuth);
  private Konten (outlook.*, hotmail.*, live.*, msn.com) melden sich automatisch am Endpunkt `consumers` an;
  verständliche Hinweise zu typischen Microsoft-Anmeldefehlern (AADSTS…), auch in der Kommandozeile
- Anleitung zur Google-/Microsoft-Anmeldung im Programm (DE/EN): Einstellungen → „Anleitung Microsoft/Google“,
  verlinkt im Assistenten bei fehlender Client-ID, Microsoft-Passwortwarnung und Anmeldefehlern
- Release per Klick: Actions → Build → „Run workflow“ mit Version und Haken „Release erstellen“ legt
  Tag und Release erst nach erfolgreichem Build/Validierung an und prüft, dass alle Programme angehängt sind
  (Release über die GitHub-CLI; ohne PR-Liste, falls deren Erzeugung scheitert)
- Deutsche Begriffe: „Fall“ statt „Case“, „Aktenzeichen“ statt „Casenumber“, „Asservat“/„Asservatennummer“
  statt „Evidence“ – in Oberfläche, Kommandozeile, Bericht und Validierungsprotokoll (englisch unverändert)
- Logo: auch SVG – wird beim Anlegen eines Falls in ein PNG umgewandelt (Hash des Original-SVG im Log)
- Muster Aktenzeichen: `{jahr:2}` = zweistellige Jahreszahl (26), dazu `{monat}` und `{tag}`; Beispiel und
  Platzhalter direkt unter dem Feld
- Einstellungen: Hinweis, dass ein Sprachwechsel der Oberfläche erst nach einem Neustart wirkt

## 1.0.0

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
- Echtheitsprüfung (DKIM) bei der Sicherung: Ergebnis je Mail (gültig, ungültig, nicht prüfbar,
  nicht signiert), Begründung, Authentication-Results des Anbieters (SPF/DKIM/DMARC); abgefragte
  DNS-Schlüssel werden mit Abrufzeit und SHA-256 in der Case-Datei aufbewahrt; Spalten in `log.csv`,
  Abschnitte im Bericht; offene DNS-Fehler werden beim Fortsetzen erneut geprüft; abschaltbar
  (`dkim_pruefen`); Testpostfach um zwei signierte Mails erweitert, Validierung V14
- Gmail: Labels statt Ordner-Kopien – bei Gmail-Konten (`X-GM-EXT-1`) sind „Alle Nachrichten“,
  „Spam“ und „Papierkorb“ vorausgewählt (über SPECIAL-USE, sprachunabhängig); je Mail werden
  `X-GM-MSGID`, `X-GM-THRID` und `X-GM-LABELS` gespeichert (Labels mit Leerzeichen/Umlauten korrekt
  gelesen), Mehrfachkopien gekennzeichnet; Hinweis mit Umschalter auf der Ordnerseite, CLI
  `--gmail-all-folders`
- Gezielte Teilsicherung: Inhaltsfilter je Konto (Absender, Empfänger To/CC/BCC, Betreff, Text);
  Werte mit Komma = oder, Felder = und; Suche per IMAP SEARCH, Umlaute als UTF-8-Literal (jeder Begriff
  einzeln gesucht, Treffer in FED verknüpft); protokolliert, im Bericht als Teilsicherung mit Kriterien;
  Karte „Inhalt (optional)“ auf der Ordnerseite, CLI `--from-contains/--to-contains/--subject-contains/
  --text-contains`; Validierung V15
- Verschlüsselte Kopie für die Weitergabe: `<Casenumber>_encrypted.zip` (AES-256, WinZip-AES – öffnet
  mit 7-Zip, WinRAR, Keka) mit Original-ZIP(s), Hash-/Zeitstempeldateien, log.csv und Berichten;
  Original bleibt unverändert; Kopie wird nach dem Schreiben entschlüsselt und Datei für Datei geprüft;
  Passwort selbst wählen oder erzeugen lassen (≈140 Bit), wird nie gespeichert oder protokolliert;
  `.sha256` daneben, Hash im Log; Einstellung `verschluesselung` (fragen | aus), CLI
  `fed finish --encrypt | --encrypt-generate`
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
- Mail-Betrachter (nur lesend): Doppelklick in der Historie „Gesicherte Mails“ öffnet Kopfdaten,
  Text (HTML ohne Nachladen externer Inhalte), Anlagen mit Größe/SHA-256 und „Speichern unter …“,
  vollständige Kopfzeilen, Received-Kette mit Zeiten und Dauer, DKIM/Authentication-Results und
  Gmail-Labels; liest aus `work/` oder aus dem ZIP, prüft dabei den SHA-256 gegen die Case-Datei
  und protokolliert Ansehen (`message_viewed`) und Speichern (`attachment_exported`)
- Betrachter: Anlagen-Vorschau im Speicher (Bilder, PDF über QtPdf, Text) mit Reitern „Vorschau“,
  „Prüfung“ und „Hex“; eingebettete Bilder (`cid:`) in HTML-Mails werden angezeigt – nur aus der
  Mail selbst, externe Inhalte bleiben gesperrt
- Dateiprüfung je Anlage (`fed.core.filecheck`, angelehnt an Crush Forensics): Format an den
  Magic Bytes erkannt und mit Endung und angegebenem MIME-Typ verglichen; MD5/SHA-1/SHA-256,
  Entropie; Warnungen bei ausführbaren Inhalten, getarnten Endungen (doppelt, Leerzeichen,
  Schreibrichtungs-Zeichen), Makros, nachgeladenen Vorlagen, aktiven PDF-Inhalten (JavaScript,
  OpenAction, Launch …), eingebetteten Dateien, verschlüsselten Archiven, Programmen in Archiven,
  Datenträger-Abbildern und Skripten; keine Vorschau für ausführbare Inhalte, Rückfrage vor dem
  Speichern auffälliger Anlagen; jede Vorschau wird mit Hash und Befunden protokolliert
  (`attachment_previewed`)
- **Sicherung im Hintergrund, mehrere Konten gleichzeitig:** Während ein Konto gesichert wird,
  kann das nächste vorbereitet und gestartet werden. Die linke Leiste zeigt je laufendem Konto
  einen Ladebalken mit Prozent, Mails und Tempo; ein Klick öffnet dessen Live-Ansicht.
  Abschluss erst, wenn alle Sicherungen beendet sind; Beenden bricht alle sauber ab.
  Dasselbe Konto kann nie zweimal gleichzeitig laufen.
- Nach einem Absturz auf „läuft“ stehen gebliebene Versuche werden beim Öffnen des Case als
  abgebrochen erkannt und protokolliert (`attempt_interrupted`) – der Case bleibt abschließbar
- Beenden während einer Sicherung wartete immer 15 s – jetzt sofort
- Ordnernamen: `/` und `\` werden durch `_` ersetzt (eine Casenumber wie `VG-1234/26` legte
  einen Unterordner an); Hinweis im Case-Formular, die Casenumber selbst bleibt unverändert
- Abschluss schlug fehl, wenn die Casenumber ein `/` enthielt (ZIP-Name zeigte in einen nicht
  vorhandenen Unterordner) – behoben durch die Ersetzung oben; Test mit `VG-122234/23, EV-1`
- Leerzeichen in Case-Ordner- und Dateinamen werden standardmäßig durch `_` ersetzt
  (Einstellung `leerzeichen_ersetzen`); die Regel wird beim Anlegen im Case gespeichert und gilt
  für alle seine Dateien, ältere Cases behalten ihre Schreibweise. Mail-Ordner im ZIP bleiben
  unverändert
- Abschluss fehlgeschlagen: „Erneut versuchen“ und „Zurück zu den Konten“ (der Case bleibt offen);
  „Case abschließen …“ jetzt auch auf der Kontoseite – nach einem Neustart von FED war ein
  geöffneter Case sonst nur über eine neue Sicherung abschließbar
- PDF-Bericht: Emojis in Betreffs und Notizen werden dargestellt statt leerer Kästchen
  (Ersatzschrift Noto Emoji, schwarz-weiß, SIL OFL 1.1; Warnung „missing the following glyphs“
  entfällt)
- Qt-Warnung „missing font family Monospace“ (macOS) behoben: Festbreite über die
  Systemschrift des Designs statt über den Namen „monospace“
- **Internationale Schriften:** zweites Testpostfach (`validation/intlmailbox.py`) mit Ordnern über
  vier Ebenen und Mails in Russisch, Serbisch (kyrillisch), Kroatisch/Serbokroatisch,
  Vietnamesisch, Chinesisch, Japanisch, Myanmar, Arabisch und Hebräisch – UTF-8 und alte
  Kodierungen (KOI8-R, ISO-2022-JP, GB18030, windows-1256, windows-1255); neue Validierung V16
- PDF-Bericht: Ersatzschriften Noto Sans SC (Chinesisch/Japanisch), Myanmar, Arabic, Hebrew (SIL
  OFL 1.1) und Textformung mit HarfBuzz (`uharfbuzz`, nur wenn der Bericht solche Schriften
  enthält); Korrektur der Bidi-Reihenfolge in fpdf2 (Zahl nach arabischem/hebräischem Text stand
  auf der falschen Seite); Notizen von rechts nach links rechtsbündig in richtiger Wortfolge;
  Dateipfade behalten ihre Reihenfolge; Tabellen linksbündig statt Blocksatz
- Selbsttest der fertigen Programme prüft Textformung und alle Schriften
- Programmpakete etwa 12 MB größer (vor allem die chinesisch-japanische Schrift)
- Hintergrund-Threads werden im GUI-Thread geordnet freigegeben: Das Aufräumen hing an einer
  Funktion ohne Bezugsobjekt am Thread-Ende und lief im endenden Thread – selten stürzte danach die
  Speicherbereinigung ab
- Abschlussvermerk: vor dem Abschluss optional ein formatierbarer Vermerk für den Bericht
  (Abschnitt „Abschlussvermerk“ vor der Bestätigung; gespeichert und protokolliert, CLI
  `fed finish --closing-note`). Abbrechen lässt den Case offen
- Bericht: Beginn, Ende und Dauer der Sicherung – gesamt in den Grunddaten und je Konto; bei
  mehreren Versuchen oder parallelen Konten zusätzlich die Summe der Versuche; Dauer je Versuch
- Passwort/OAuth-Anmeldung wird nach einem erfolgreichen Verbindungstest für die Sitzung gemerkt –
  nur im Arbeitsspeicher, bis FED beendet wird (auch über weitere Konten und einen neuen Case
  hinweg). Wird das Konto erneut gewählt oder seine Adresse eingetippt, ist die Anmeldung
  vorbelegt; „Vergessen“ löscht sie sofort. Protokolliert wird nur, dass die Anmeldedaten aus der
  Sitzung kamen (`credentials_from_session`), nie der Wert
- Formatierte Notizen (Case-Anmerkungen und Notiz zum Konto): fett, kursiv, unterstrichen, vier
  Farben, Marker, Aufzählung und Absätze; Werkzeugleiste mit Tastenkürzeln, Einfügen immer als
  reiner Text. Gespeichert als bereinigtes HTML (`notes_html`), daneben weiter der reine Text für
  TXT-Bericht, CSV und Log; im PDF-Bericht formatiert gesetzt (neue Schriftschnitte DejaVu Oblique)
- Bericht: bei mehreren Konten „Sicherungsbericht von N E-Mail-Postfächern“; Examiner im
  Seitenkopf und Abschnitt „Bestätigung“ (Examiner, Zeitpunkt) am Ende vor der Mail-Liste
- Notiz zum Konto direkt in der Kontenübersicht bearbeitbar (Klick auf die Notiz, auch in der
  Historie, solange der Case offen ist); Änderungen werden mit altem und neuem Text protokolliert.
  Mehrzeilige Notizen werden vollständig und umbrochen angezeigt
- Verbindung testen: leere Pflichtfelder (auch das Passwort) werden rot markiert, der Test
  startet erst, wenn alles ausgefüllt ist
- Ordnerseite scrollt auf kleinen Bildschirmen, „Zurück“/„Sicherung starten“ bleiben fest
  unten; Eingabefelder werden nicht mehr zusammengedrückt
- Verschlüsselte Kopie: Passwort-Optionen nur sichtbar, wenn die Kopie gewählt ist; Felder für
  ein eigenes Passwort nur bei „Eigenes Passwort“
- Vollständig zweisprachig: Meldungen des Programmkerns (Fehler, Autoerkennung, Anmelde-Hinweise,
  Zeitstempel, DKIM-Begründungen, Empfänger-Hinweise, OAuth-Browserseite) und die Kommandozeile
  über `core_<sprache>.json` (`fed --lang en`, `FED_LANG`); Zonenname im Englischen `CET` statt
  `MEZ`; neuer Test durchläuft die ganze Oberfläche auf Englisch und prüft auf deutschen Text
- Case-Datei und Log speichern sprachneutrale Codes statt deutscher Texte (DKIM-Begründung,
  Empfänger-Hinweis, Herkunft der Servereinstellungen, ZIP-Prüfung, Wachhalten); Bericht und
  Oberfläche übersetzen sie, Werte älterer Case-Dateien werden weiter erkannt
- **`log.csv` mit festen englischen Spaltennamen** (`Account`, `Folder`, `Received_UTC`, `Subject`,
  `File` …), unabhängig von der Sprache – wie die Namen im Case-Ordner
- Historie „Dateien nachhashen“ prüft alle ZIPs des Case (auch je Konto), nicht nur das letzte

## 0.4-beta

Kommandozeilen-Skript, siehe `legacy/`.
