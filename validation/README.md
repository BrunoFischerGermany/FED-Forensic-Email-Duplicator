# Validierung

Hier entsteht das Validierungs-Postfach (Meilenstein 5):

- `docker/` – Dovecot-Image mit festem Testpostfach (Umlaute in Ordnern, Anhänge, große Mails, kaputte Header, leere Ordner)
- `expected_hashes.csv` – erwartete SHA-256/MD5/SHA-512 je Mail
- Menüpunkt „Validierung“ bzw. `fed validate` sichert das Postfach und erzeugt ein Validierungs-PDF (Grundlage für ISO 17025)
