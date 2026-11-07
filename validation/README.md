# Validierung

Festes Testpostfach mit bekannten Hashes – Grundlage für die automatischen Tests und
für das Validierungsprotokoll (ISO 17025).

| Datei | Inhalt |
| --- | --- |
| `testmailbox.py` | erzeugt das Postfach als Maildir: 12 Mails in 10 Ordnern (Umlaute in Ordnern, Anlagen, eingebettetes Bild, nur BCC, per BCC erhalten, ohne Date-Header, 8-bit Latin-1, 4 MB) |
| `expected_hashes.csv` | erwartete SHA-256/MD5 je Mail (`python validation/testmailbox.py --expected`) |
| `dovecot_server.py` | startet einen lokalen Dovecot mit diesem Postfach (`python validation/dovecot_server.py`) |

Die Tests unter `tests/test_imap_reader.py` und `tests/test_acquisition.py` laufen
automatisch gegen diesen Server, wenn `dovecot` installiert ist (`apt install dovecot-imapd`),
sonst werden sie übersprungen. In der CI laufen sie unter Linux.

Folgt in Meilenstein 5: Menüpunkt „Validierung“ bzw. `fed validate` mit Validierungs-PDF.
