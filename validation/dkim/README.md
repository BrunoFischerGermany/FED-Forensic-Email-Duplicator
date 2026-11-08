# DKIM-Testdaten (nur für Tests und Validierung)

- `test-key.pem` – **Test-Schlüssel**, nur für dieses Testpostfach; niemals produktiv verwenden.
- `fed2026._domainkey.dkim.example.txt` – zugehöriger öffentlicher Schlüssel (DNS-TXT-Inhalt).
- `signed-pass.eml` – gültig signiert (mit `Authentication-Results` des „Empfängers“).
- `signed-tampered.eml` – nach dem Signieren verändert (Betrag), DKIM muss fehlschlagen.

`dkim.example` ist eine reservierte Domain (RFC 6761/2606); Tests beantworten die DNS-Abfrage lokal.
