# Übersetzungen

Die Quelltexte in `self.tr(...)` sind Deutsch. Die englischen Texte stehen in `en.json`
(`"deutscher Text": "English text"`; `"Klasse|Text"` gilt nur für eine Klasse).

```bash
python packaging/i18n.py          # Texte einsammeln, aus en.json füllen, fed_en.ts + fed_en.qm erzeugen
python packaging/i18n.py --check  # fehlt eine Übersetzung? (läuft auch in den Tests)
```

`fed_en.qm` ist eingecheckt, damit das Programm ohne Zusatzschritt läuft. Die Sprache wird
beim Start aus `config.toml` (`sprache`) geladen, beim ersten Start aus der Systemsprache.
Meldungen aus dem Programmkern (z. B. Server-Fehler) und das Protokoll bleiben Deutsch.
