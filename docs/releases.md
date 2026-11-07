# Releases erstellen

Releases entstehen automatisch: Du schreibst auf GitHub die Release-Notizen und klickst
„Publish“ – der Workflow **Build** baut danach alle Programme, prüft sie und hängt sie an.
(Vorbild: [crush-forensics](https://github.com/kalink0/crush-forensics/releases).)

## Ein Release veröffentlichen

1. Auf `main` ist alles drin, die CI ist grün.
2. Optional: Versionsnummer in `src/fed/__init__.py` und Eintrag in `CHANGELOG.md` anpassen.
   (Nicht zwingend – der Build setzt die Version aus dem Tag.)
3. GitHub → **Releases** → **Draft a new release**
   - **Choose a tag** → neuen Tag eintippen, z. B. `v1.0.0` → „Create new tag on publish“, Ziel `main`
   - **Release title**: z. B. `v1.0.0`
   - **Beschreibung**: Neuerungen (z. B. aus `CHANGELOG.md`; „Generate release notes“ listet die PRs auf)
   - Vorabversion? Haken bei **Set as a pre-release** (z. B. für `v1.0.0-rc1`)
4. **Publish release** klicken.

Danach unter **Actions → Build** zusehen (ca. 25–35 Minuten). Am Ende hängen am Release:

| Datei | Inhalt |
| --- | --- |
| `FED-1.0.0-windows-x64.exe` | Windows: **eine einzelne Datei**, Doppelklick startet FED |
| `FED-1.0.0-macos-arm64.zip` | macOS Apple Silicon (M1–M4): `FED.app` |
| `FED-1.0.0-macos-x64.zip` | macOS Intel: `FED.app` |
| `FED-1.0.0-linux-x64.tar.gz` | Linux: Ordner `FED/`, Programm `FED/FED` |
| `FED-1.0.0-validation-report-de.pdf` / `-en.pdf` | Validierungsprotokoll (plus `.txt`, `.json`) |
| `SHA256SUMS.txt` | Prüfsummen aller Dateien |

Zusätzlich wird an die Release-Notizen eine Tabelle mit dem Ergebnis der 13 Validierungsprüfungen
und den SHA-256-Werten angefügt.

Per Kommandozeile geht dasselbe mit der GitHub-CLI:

```bash
gh release create v1.0.0 --target main --title "v1.0.0" --notes-file notes.md
```

## Was der Workflow prüft – und wann er nichts anhängt

- **Bauen** auf Windows, macOS (Apple Silicon und Intel) und Linux mit Nuitka.
- **Selbsttest des Pakets** (`packaging/selftest.py`): Die heruntergeladene Datei wird entpackt und
  gestartet. Geprüft werden: Programm startet, findet seinen Ordner (für `config.toml`), der
  Programm-Hash im Bericht ist der SHA-256 der Programmdatei, englische Übersetzung, Qt, PDF mit
  Schriften, Case-Datei mit Log-Kette.
- **Validierung** (`python -m validation.run`) gegen das Testpostfach.

Schlägt einer dieser Schritte fehl, wird **nichts** an das Release angehängt; der Lauf ist rot.
Dann den Fehler beheben, das Release (und den Tag) löschen und neu veröffentlichen – oder ein
neues Release mit höherer Nummer anlegen.

## Nightly

`.github/workflows/nightly.yml` baut jede Nacht (01:23 UTC) den Stand von `main` als
Vorabversion **Nightly** – aber nur, wenn es seit dem letzten Nightly oder Release neue Commits
gibt. Das vorige Nightly wird ersetzt. Sofort auslösen: **Actions → Nightly → Run workflow**.
Nightlies sind zum Testen gedacht und als „Pre-release“ markiert.

## Programme nur bauen (ohne Release)

**Actions → Build → Run workflow** – die Dateien liegen danach im Lauf unter *Artifacts*
(90 Tage). Lokal für das eigene System: `pip install -e .[build]` und `python packaging/build.py`.

## Hinweise für Nutzer der Programme

- **Windows**: Die `.exe` ist nicht signiert. SmartScreen meldet „Der Computer wurde durch
  Windows geschützt“ → **Weitere Informationen → Trotzdem ausführen**. Beim Start entpackt sich
  das Programm in den Temp-Ordner (erster Start einige Sekunden) und räumt beim Beenden auf.
  `config.toml` und `FED.ini` liegen neben der `.exe`.
- **macOS**: nicht notarisiert. Erststart per Rechtsklick → **Öffnen**, oder
  `xattr -cr FED.app` im Terminal. `config.toml` gehört neben `FED.app`.
- **Linux**: `tar xzf FED-…-linux-x64.tar.gz && ./FED/FED`.
- Prüfsumme kontrollieren: Windows `certutil -hashfile FED-…exe SHA256`, macOS/Linux
  `shasum -a 256 <datei>` – Vergleich mit `SHA256SUMS.txt`.

Signieren (Windows-Zertifikat, Apple-Developer-ID mit Notarisierung) lässt sich später als
weiterer Schritt in `build.yml` ergänzen; die Zertifikate werden dann als GitHub-Secrets hinterlegt.
