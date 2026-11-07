# Übersetzungen / Translations

| Datei | Inhalt |
| --- | --- |
| `<sprache>.json` | Oberfläche: deutscher Quelltext → Übersetzung (`"Klasse|Text"` gilt nur für eine Klasse) |
| `report_<sprache>.json` | Texte des TXT/PDF-Berichts |
| `fed_<sprache>.ts` / `.qm` | daraus erzeugt (`python packaging/i18n.py`), eingecheckt |

Neue Sprache: `python packaging/i18n.py --new fr` – Anleitung: [docs/translating.md](../../../docs/translating.md).
