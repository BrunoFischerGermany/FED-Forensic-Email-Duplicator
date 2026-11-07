# Architektur und Grundregeln (Kurzfassung des Konzepts)

## Ablauf

1. Case anlegen (Casenumber, Examiner, Notes; Evidence-Nummer je Konto)
2. Systemprüfung (Benutzer, Zeit + NTP, Internet, Datenträger, freier Speicher)
3. Konto: Adresse, Autoerkennung oder manuell, Passwort oder OAuth, Verbindungstest
4. Postfach scannen (Ordner, Anzahl, Größe, Namespaces)
5. Ordner wählen (Baum mit Häkchen, optional Zeitraum)
6. Sicherung (Versuch: Fortsetzen oder Neubeginn; jederzeit abbrechbar)
7. Case abschließen: ZIP, Verifikation, Hashes, Zeitstempel, Berichte

## Forensische Regeln

- Ordner nur mit `EXAMINE`, Mails nur mit `BODY.PEEK[]`; schreibende Befehle werden in `imap_reader.guard()` blockiert
- TLS-Zertifikat wird geprüft, Fingerprint protokolliert
- Rohdaten werden nie dekodiert oder umgeschrieben; Hash über exakt die empfangenen Bytes
- Eine Mail gilt erst als gesichert nach: Datei geschrieben → Hashes → Commit in SQLite
- Log ist nur anhängend (SQLite-Trigger) und per Hash-Kette gesichert
- Passwörter und Tokens nie auf Datenträger oder ins Log
- Zeitstempel der `.eml` = Eingangszeit (`INTERNALDATE`), Ersatz `Date:`-Header
- Dateiname: `<Eingangszeit UTC>_UID<8 Stellen>.eml`, kein Betreff
- Vorher/Nachher-`STATUS` je Ordner belegt, dass nichts verändert wurde

## Ablage

```
<Casenumber>/
├─ case.sqlite
├─ work/<adresse>/<ordner>/*.eml
├─ discarded/attempt_<n>/
├─ <Casenumber>_<zeit>.zip  (+ .sha256, .tsr)
├─ log.csv
└─ report.txt / report.pdf
```

Im ZIP: `<adresse>/<IMAP-Ordner>/...` je Konto, dazu `log.csv`.
