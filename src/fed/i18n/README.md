# Übersetzungen

Qt Linguist: Quelltexte in `self.tr(...)` sind Deutsch.

```bash
pyside6-lupdate src/fed/gui/*.py -ts src/fed/i18n/fed_en.ts
pyside6-linguist src/fed/i18n/fed_en.ts
pyside6-lrelease src/fed/i18n/fed_en.ts -qm src/fed/i18n/fed_en.qm
```

Die `.qm`-Dateien werden im Build erzeugt und nicht eingecheckt.
