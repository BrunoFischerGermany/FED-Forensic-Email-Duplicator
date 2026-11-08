# Roadmap

Stand: 8. November 2026 · Version 1.0 (Meilensteine 1–6 abgeschlossen)

Ziel der nächsten Ausbaustufe: **Beweisqualität und Praxistauglichkeit** – Echtheit der Mails
belegen, Gmail korrekt abbilden, nur das sichern, was ein Beschluss abdeckt, sicher weitergeben
und schon vor Ort sehen, was gesichert wurde.

| Nr. | Funktion | Status |
| --- | --- | --- |
| 1 | DKIM-Prüfung bei der Sicherung, Authentifizierungsergebnisse des Anbieters | erledigt (1.0) |
| 2 | Gmail: Labels statt Ordner-Kopien | erledigt (1.0) |
| 4 | Gezielte Teilsicherung (Absender, Empfänger, Betreff, Text) | erledigt (1.0) |
| 5 | Verschlüsselte Kopie für die Weitergabe (AES-256) | erledigt (1.0) |
| 6 | Mail-Betrachter (nur lesend) | erledigt (1.0) |
| – | Konten aus Thunderbird, Apple Mail, Outlook übernehmen (aus Ordner/Image) | geplant |
| – | Microsoft Graph (Exchange Online ohne IMAP) | geplant |
| – | Mehrere Konten aus einer Liste (CSV) | geplant |
| – | Offline-Sicherung lokaler Postfächer (MBOX, .emlx, PST/OST) | Idee, eigener Meilenstein |

## 1 DKIM-Prüfung bei der Sicherung

**Warum:** Anbieter tauschen ihre DKIM-Schlüssel regelmäßig aus. Monate später lässt sich eine
Signatur oft nicht mehr prüfen. Wer zum Zeitpunkt der Sicherung prüft und den öffentlichen
Schlüssel aufbewahrt, kann die Echtheit einer Mail auch später noch belegen.

**Umsetzung**
- Nach dem Laden jeder neuen Mail: DKIM-Signaturen prüfen (Paket `dkimpy`), Ergebnis je Mail
  (`pass`, `fail`, `none` = nicht signiert, `temperror` = DNS nicht erreichbar, `permerror`).
- Die abgefragten DNS-Einträge (`<selector>._domainkey.<domain>`) werden mit Abrufzeit und
  SHA-256 in der Case-Datei gespeichert – damit ist die Prüfung später nachvollziehbar.
- Zusätzlich wird der Kopf `Authentication-Results` des empfangenden Anbieters ausgewertet
  (SPF, DKIM, DMARC aus Sicht des Anbieters). Eine eigene SPF-Prüfung ist nachträglich nicht
  sinnvoll möglich (sie braucht die IP des einliefernden Servers zum Empfangszeitpunkt).
- Bericht: Übersicht je Konto (bestanden / fehlgeschlagen / nicht signiert / nicht prüfbar),
  Spalten in `log.csv`, Liste der gespeicherten Schlüssel.
- Abschaltbar (`dkim_pruefen`), z. B. ohne Internet zum DNS.
- Validierung: Testmails mit Test-Signatur (bestanden, verändert = fehlgeschlagen, unsigniert).

## 2 Gmail: Labels statt Ordner-Kopien

**Warum:** Bei Gmail sind „Ordner“ Labels. Eine Mail mit drei Labels steht heute dreimal im ZIP
(plus „Alle Nachrichten“) – unnötig groß und verwirrend bei der Auswertung.

**Umsetzung**
- Erkennung über die Server-Fähigkeit `X-GM-EXT-1`.
- Empfohlene Auswahl: „Alle Nachrichten“, „Spam“, „Papierkorb“ (über die SPECIAL-USE-Kennungen
  `\All`, `\Junk`, `\Trash`, unabhängig von der Sprache des Kontos). „Alle Nachrichten“ enthält
  Spam und Papierkorb nicht, deshalb kommen beide dazu.
- Je Mail werden `X-GM-MSGID`, `X-GM-THRID` (Konversation) und `X-GM-LABELS` gespeichert –
  in Case-Datei, `log.csv` und Bericht.
- Wer trotzdem alle Label-Ordner sichert: gleiche `X-GM-MSGID` wird als Duplikat gekennzeichnet.

## 4 Gezielte Teilsicherung

**Warum:** Ein Beschluss deckt oft nur bestimmte Kommunikation ab (Verhältnismäßigkeit).

**Umsetzung**
- Kriterien je Konto: Absender enthält, Empfänger (To/CC/BCC) enthält, Betreff enthält, Text
  enthält – mehrere Werte je Feld mit Komma = „oder“, Felder untereinander = „und“; zusammen mit
  dem vorhandenen Zeitraum.
- Suche auf dem Server per IMAP `SEARCH` (mit `CHARSET UTF-8` für Umlaute).
- Kriterien werden in der Case-Datei gespeichert, protokolliert und im Bericht als
  **Teilsicherung** mit Kriterien ausgewiesen. Hinweis im Bericht: Die Treffer bestimmt der
  Server; Suchlogik und Genauigkeit unterscheiden sich je Anbieter.

## 5 Verschlüsselte Kopie für die Weitergabe

**Warum:** Ein unverschlüsseltes ZIP mit Mails auf einem USB-Stick ist ein Datenschutzrisiko.

**Umsetzung**
- Das geprüfte Original-ZIP bleibt unverändert (sein Hash ist der Beweiswert).
- Zusätzlich auf Wunsch: `<Name>_verschluesselt.zip` mit AES-256 (WinZip-AES, öffnet mit 7-Zip,
  WinRAR, Keka, The Unarchiver), darin Original-ZIP, `.sha256` und ggf. Zeitstempel. Der Empfänger
  entpackt und prüft den Hash des Originals.
- Passwort: selbst eingeben (zweimal) oder sicheres Passwort erzeugen lassen. **Es wird nirgends
  gespeichert oder protokolliert** – protokolliert werden nur Verfahren, Dateiname und Hash.
- Einstellung `verschluesselung` (aus | fragen), CLI `fed finish --encrypt`.

## 6 Mail-Betrachter

**Warum:** Schon vor Ort prüfen, ob das Richtige gesichert wurde, ohne ein Mailprogramm zu
benutzen (das Dateien verändern oder externe Inhalte nachladen könnte).

**Umsetzung**
- Aus der Historie (Doppelklick auf eine Mail): Kopfdaten, Text (HTML ohne Nachladen externer
  Inhalte), Anlagen mit Größe und SHA-256, vollständige Kopfzeilen, Received-Kette als Tabelle
  (Stationen mit Zeitstempeln), DKIM-/Authentifizierungsergebnis.
- Liest aus `work/` oder – nach dem Löschen des Arbeitsordners – direkt aus dem ZIP; nichts wird
  verändert.
- Anlage speichern: Kopie an einen gewählten Ort, mit Hash im Log.

## Spätere Ideen

- **Konten übernehmen** aus Thunderbird (`prefs.js`), Apple Mail (`Accounts.plist`) und Outlook
  (Registry/`NTUSER.DAT`) – aus einem Ordner oder Image, nicht live auf dem Rechner der betroffenen
  Person; Passwörter werden bewusst nicht entschlüsselt.
- **Microsoft Graph** für Exchange Online, wenn IMAP abgeschaltet ist; optional Kalender/Kontakte.
- **Konten aus CSV** für Fälle mit vielen Postfächern.
- **Offline-Sicherung** lokaler Postfächer (MBOX, Apple `.emlx`, Outlook PST/OST über libpff) in
  dasselbe Format (`.eml` + Hash + ZIP + Bericht).
