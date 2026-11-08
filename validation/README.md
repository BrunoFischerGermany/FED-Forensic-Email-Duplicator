# Validierung

Festes Testpostfach mit bekannten Hashes – Grundlage für die automatischen Tests und
für das Validierungsprotokoll (ISO 17025).

| Datei | Inhalt |
| --- | --- |
| `testmailbox.py` | erzeugt das Postfach als Maildir: 14 Mails in 11 Ordnern (Umlaute in Ordnern, Anlagen, DKIM gültig/verändert, eingebettetes Bild, nur BCC, per BCC erhalten, ohne Date-Header, 8-bit Latin-1, 4 MB) |
| `expected_hashes.csv` | erwartete SHA-256/MD5 je Mail (`python validation/testmailbox.py --expected`) |
| `dovecot_server.py` | startet einen lokalen Dovecot mit diesem Postfach (`python validation/dovecot_server.py`) |

Die Tests unter `tests/test_imap_reader.py` und `tests/test_acquisition.py` laufen
automatisch gegen diesen Server, wenn `dovecot` installiert ist (`apt install dovecot-imapd`),
sonst werden sie übersprungen. In der CI laufen sie unter Linux.

## Validierungsprotokoll

```bash
pip install -e .
python -m validation.run --out validation-report --lang de,en
```

Startet einen lokalen Dovecot mit dem Testpostfach (und einen Test-Zeitstempeldienst mit
eigener Test-CA), sichert das Postfach mit FED wie im echten Einsatz und prüft 14 Punkte:

| Nr. | Prüfung |
| --- | --- |
| V01 | nur lesender Zugriff – Maildir-Dateien und STATUS vorher = nachher |
| V02 | Sperre schreibender IMAP-Befehle (STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, RENAME, CREATE, SELECT) |
| V03 | Vollständigkeit – alle Ordner, Anzahl je Ordner |
| V04 | bitgenaue Sicherung – SHA-256/MD5/Größe gegen `expected_hashes.csv` |
| V05 | Eingangszeit als Dateizeit und im Dateinamen |
| V06 | Abbrechen und Fortsetzen |
| V07 | Erkennung veränderter Arbeitsdateien |
| V08 | ZIP – Verifikation, Inhalt, Ordnerstruktur, `.sha256` |
| V09 | Erkennung veränderter ZIP-Inhalte |
| V10 | Manipulationsschutz des Logs (nur anhängend, Hash-Kette) |
| V11 | Zeitstempel nach RFC 3161, Signatur mit `openssl ts -verify` |
| V12 | Berichte und Ausgabe-Hashes |
| V13 | Umlaute in Adresse, Domain und Passwort (`bärbel.böhmer@ärzte.de`) |
| V14 | DKIM-Prüfung: gültig / verändert / nicht signiert, Schlüssel aufbewahrt, Authentication-Results |

Ergebnis: `validation-report-de.pdf/.txt`, `validation-report-en.pdf/.txt`, `validation-report.json`
mit Datum, Programmversion, SHA-256 des Programms, Git-Stand, Python-, Betriebssystem-,
Dovecot- und OpenSSL-Version. Rückgabewert 1, wenn eine Prüfung nicht bestanden ist.
`tests/test_validation.py` enthält zusätzlich eine Gegenprobe (ohne Befehlssperre muss V02 scheitern).

Der Workflow „Build“ führt die Validierung bei jedem Lauf aus und hängt die Berichte an jedes
Release an (`FED-<version>-validation-report-de.pdf` usw.). Geprüft wird der Quelltext-Stand
(Git-Commit im Bericht), aus dem auch die Programme desselben Laufs gebaut werden.
