# Translating FED into a new language

FED is built so that a new language needs **no programming** – only two JSON files.
This guide uses French (`fr`) as the example; replace it with your language code.

| What | File | Contains |
| --- | --- | --- |
| Interface | `src/fed/i18n/<code>.json` | all texts of windows, buttons, dialogs (≈ 280) |
| Report | `src/fed/i18n/report_<code>.json` | all texts of the TXT/PDF report (≈ 80) |
| Built file | `src/fed/i18n/fed_<code>.qm` | generated from the JSON – never edit by hand |

The program's source language is **German**: the keys in the JSON files are the German texts,
the values are your translation. New files are pre-filled with the **English** texts, so you can
translate from English and only look at the German key when something is unclear.

## 1. Set up

```bash
git clone https://github.com/BrunoFischerGermany/FED-ForensicEmailDownloader.git
cd FED-ForensicEmailDownloader
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Python 3.14 is required. `pip` also installs PySide6, which contains the Qt tools
`pyside6-lupdate` and `pyside6-lrelease` used below.

## 2. Create the language files

```bash
python packaging/i18n.py --new fr
```

Language codes follow ISO 639-1 (`fr`, `it`, `es`, `pl`, `nl` …); add a region only where it
matters (`pt_BR`). This creates

- `src/fed/i18n/fr.json` – interface texts, values = English
- `src/fed/i18n/report_fr.json` – report texts, values = English

## 3. Translate

Open both files in any text editor (UTF-8) and replace every **value** – never change a key.

```json
"Sicherung starten": "Démarrer l'acquisition",
"{0} Mails · {1}": "{0} messages · {1}",
"Bisherigen Stand nach „discarded/“ verschieben (empfohlen)?\n\nNein = endgültig löschen.": "Déplacer l'état précédent vers « discarded/ » (recommandé) ?\n\nNon = supprimer définitivement."
```

Rules:

- **Placeholders** `{0}`, `{1}` … must appear in the translation exactly as in the original
  (order may change). The check in step 4 reports mistakes.
- **Keep** `\n` (line break), leading/trailing spaces (`" Mails"`, `"OK · "`) and symbols such as
  `✓ ✗ · …` – the program glues these texts to numbers or other texts.
- **Do not translate** fixed technical names: folder and file names (`work/`, `discarded/`,
  `report.pdf`, `case.sqlite`, `config.toml`, `log.csv`), IMAP terms (`EXAMINE`, `BODY.PEEK`,
  `INTERNALDATE`, `\Noselect`), `SHA-256`, `MD5`, `RFC 3161`, `OAuth`, `NTP`.
- Use the forensic terminology customary in your country (acquisition, evidence number, examiner,
  chain of custody). "Case", "Examiner" and "Evidence" may stay in English if that is common for you.
- Same text, different meaning: if a German word needs different translations in different places,
  add a key `"ClassName|German text"`. Example from `en.json`:
  `"Ordner": "Folder"` in general, `"AccountPage|Ordner": "Folders"` on the account page.
  The class names are listed by `python packaging/i18n.py --check` when a text is missing.
- An empty value (`""`) counts as missing – the program then shows the German text.

Report texts in `report_fr.json` work the same way: the keys there are internal names
(`"case_number"`, `"received"` …); translate only the values. `report_de.json` and
`report_en.json` show the German and English versions side by side. Some values contain
named placeholders such as `{n}`, `{tsr}`, `{zip}`, `{files}`, `{size}` – keep them as well.

## 4. Build and check

```bash
python packaging/i18n.py --check   # lists missing texts and wrong placeholders, changes nothing
python packaging/i18n.py           # writes fed_fr.ts and fed_fr.qm
```

The command handles all languages at once. Exit code 0 means complete.

## 5. Try it

```bash
fed-gui
```

**Settings → Interface language → Français**, save, restart FED. On a computer whose system
language is French, FED also starts in French the first time (before a `config.toml` exists).
Check every page: create a test case, look at the history, open the settings, create a report
(the free test mailbox in `validation/` can be used with a local Dovecot, see
[validation/README.md](../validation/README.md)). Texts that are too long for a button or label
are easiest to spot in the running program.

The report language can be chosen separately (**Settings → Report language**); without
`report_<code>.json` the report is written in English.

## 6. Submit

Commit **all four files** and open a pull request:

```
src/fed/i18n/fr.json
src/fed/i18n/report_fr.json
src/fed/i18n/fed_fr.ts
src/fed/i18n/fed_fr.qm
```

The automatic tests check that every language is complete (`tests/test_gui.py`).

## Keeping a translation up to date

When developers add or change texts, `python packaging/i18n.py --check` lists the new ones as
`missing` for every language. Add them to the JSON file and run `python packaging/i18n.py` again.
Keys that no longer exist in the program are simply ignored.

## Alternative: Qt Linguist

If you prefer a graphical editor, open `src/fed/i18n/fed_fr.ts` with `pyside6-linguist`. Note that
the next run of `packaging/i18n.py` rebuilds the `.ts` file from the JSON, so copy finished
translations back into `fr.json` (the JSON file is the master copy).

## What is not translated (yet)

- Messages from the program core (e.g. server errors, the acquisition log in the case file) –
  they are German, because the log is evidence and must not depend on the interface language.
- The validation report (`validation/run.py`) – German and English only.
- The command-line program `fed` – German.
