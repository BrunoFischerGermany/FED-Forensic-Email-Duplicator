<p align="center"><img src="assets/logo/fed-logo.svg" width="140" alt="FED logo"></p>

# FED – Forensic Email Duplicator

*Forensic acquisition of email mailboxes*

[Deutsch](README.md) · **English**

> **Branch `v1.0` – in development.** Rebuilt with a GUI. The previous command-line script v0.4-beta is kept in [`legacy/`](legacy/) (MIT license).

FED acquires email mailboxes via IMAP, forensically and strictly read-only:

- every message unchanged as `.eml`, named by receipt time and UID (`20261106T143012Z_UID00004711.eml`)
- SHA-256 (plus MD5 and SHA-512) per message and for the ZIP archive
- ZIP with the mailbox folder structure, first level = email address
- case file (SQLite) with a tamper-evident log, history, multiple attempts, resume/restart
- TXT and PDF report with agency logo, optional RFC 3161 timestamp
- Gmail/Google Workspace and Microsoft 365 via OAuth2
- German and English, Windows/macOS/Linux, portable without installation

## Status

| Milestone | Content | Status |
| --- | --- | --- |
| 1 | Project structure, config, case file with hash chain, file names, hashes, GUI skeleton | ✅ |
| 2 | System check, autodetection, read-only IMAP, scan, download to work folder, test mailbox | ✅ |
| 3 | OAuth2 Google / Microsoft 365 (browser + PKCE, device code, token refresh) | ✅ |
| 4 | ZIP with timestamps, verification, TXT/PDF reports, GUI (assistant, history, settings, light/dark) | ✅ |
| 5 | Ready-to-run programs (Nuitka) for Windows, macOS, Linux | ✅ |
| 6 | RFC 3161 timestamps, validation report, English user interface | ⏳ |

The user interface is currently German only; reports can already be generated in German or English (`bericht_sprache = "en"`).

## Development

Python 3.14 is required.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
fed-gui                    # graphical interface
fed --help                 # command line
```

Command-line example:

```bash
fed new-case --target E:/FED-Cases --case 2026-0042 --examiner "Jane Doe"
fed acquire --case-dir E:/FED-Cases/2026-0042 --account jane@example.com --evidence A-01
#   password is prompted for (or environment variable FED_PASSWORD)
#   run again = resume; --force --reason "…" = restart from scratch
#   Gmail / Microsoft 365: sign-in via OAuth in the browser
#   (setting up client IDs: docs/oauth-einrichtung.md, German)
fed finish --case-dir E:/FED-Cases/2026-0042   # ZIP + hashes + report.pdf/.txt, close the case
fed verify --case-dir E:/FED-Cases/2026-0042
```

Tests against a real IMAP server: `apt install dovecot-imapd`, then `pytest`
(automatically starts a local Dovecot with the test mailbox from `validation/`).

Configuration: copy `config.example.toml` to `config.toml` next to the program and adjust it –
or just start the program: if no base data is set, the GUI asks for it on first start.

## Building ready-to-run programs

| Way | How |
|---|---|
| One click | GitHub → **Actions** → **Build** → **Run workflow**; the files are then under the run's *Artifacts* |
| Release | `git tag v1.0.0 && git push origin v1.0.0` – builds all platforms and creates the release with the files and `SHA256SUMS.txt` |
| Local | `pip install -e .[build]` and `python packaging/build.py` (builds for your own system into `dist/`) |

Output: `FED-<version>-windows-x64.zip`, `-macos-arm64.zip`, `-macos-x64.zip`, `-linux-x64.tar.gz`.
Put `config.toml` next to `FED.exe`, `FED`, or `FED.app`.
The programs are not signed: macOS reports an "unidentified developer"
(right-click → Open, or `xattr -cr FED.app`); Windows SmartScreen: "More info → Run anyway".

## Case folder

```
<case>/
├─ work/<address>/<folder>/*.eml    messages during acquisition
├─ discarded/attempt_<n>/           previous state after a restart
├─ case.sqlite                      case file with log and hash chain
├─ <case>_<time>.zip + .zip.sha256  final archive
├─ log.csv
└─ report.txt / report.pdf
```

These names are fixed and do not depend on the language setting.

## Layout

```
src/fed/
├─ core/            core without GUI (testable, also used by the CLI)
│  ├─ config.py     load/validate/save config.toml
│  ├─ case.py       case file, log with hash chain
│  ├─ syscheck.py   user, time/NTP, internet, drives, free space
│  ├─ autoconfig.py Thunderbird-style autodetection
│  ├─ oauth.py      Google / Microsoft 365
│  ├─ imap_reader.py read-only IMAP access (write commands blocked)
│  ├─ acquisition.py attempts, download, verification
│  ├─ finalize.py   ZIP, verification, log.csv, closing the case
│  ├─ naming.py     file names, paths, modified UTF-7
│  ├─ hashing.py    SHA-256 + MD5/SHA-512
│  ├─ cancel.py     cancel, progress, speed, remaining time
│  └─ report.py     TXT/PDF reports
├─ gui/             PySide6: assistant, history, settings, first-start setup
├─ i18n/            translations (Qt Linguist)
├─ resources/       SQL schema, icons, fonts
└─ cli.py           command-line mode
packaging/          Nuitka build script
docs/               architecture, OAuth setup (German)
validation/         test mailbox + expected hashes
tests/
```

## License

GPL-3.0-only, see [LICENSE](LICENSE). The code in `legacy/` remains under MIT ([legacy/LICENSE-MIT](legacy/LICENSE-MIT)).

## Authors

- [@BrunoFischerGermany](https://github.com/BrunoFischerGermany)
- [@ma-fox](https://github.com/ma-fox) (v0.4)
