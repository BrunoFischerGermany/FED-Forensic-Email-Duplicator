"""Baut FED als eigenständiges Programm mit Nuitka (Windows, macOS, Linux).

Lokal:   pip install -e .[build]  &&  python packaging/build.py
In CI:   .github/workflows/build.yml ruft dasselbe Skript auf.

Ergebnis in ``dist/``:
  Windows  FED-<version>-windows-x64.exe      (eine einzelne Datei, startet ohne Installation)
  macOS    FED-<version>-macos-<arch>.zip      (FED.app)
  Linux    FED-<version>-linux-x64.tar.gz      (Ordner mit FED)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logo"
BUILD = ROOT / "build" / "nuitka"
DIST = ROOT / "dist"
EXTRAS = ["config.example.toml", "LICENSE", "README.md", "README.de.md", "CHANGELOG.md"]


def version() -> str:
    v = os.environ.get("FED_VERSION", "").removeprefix("v")
    if v:
        return v
    sys.path.insert(0, str(ROOT / "src"))
    from fed import __version__

    return __version__


def numeric_version(v: str) -> str:
    """Windows verlangt a.b.c.d aus Zahlen bis 65535: ``1.2.3`` -> ``1.2.3.0``, sonst ``0.0.0.0``."""
    import re

    m = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", v)
    parts = [min(int(x), 65535) for x in m.groups(default="0")] if m else [0, 0, 0]
    return ".".join(map(str, [*parts, 0]))


def target() -> tuple[str, str]:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
    return system, arch


def nuitka_args(system: str, v: str) -> list[str]:
    args = [
        sys.executable, "-m", "nuitka",
        # Ordner mit Programm und Bibliotheken – auch unter Windows keine selbstentpackende Einzeldatei:
        # die entpackt sich beim Start in den Temp-Ordner und löst bei Virenscannern (z. B. Symantec
        # „Heur.AdvML.B“) Fehlalarme aus
        "--standalone",
        "--enable-plugin=pyside6",
        "--include-package=fed",
        "--include-package-data=fed",
        "--include-package-data=fpdf",
        "--include-package=dns",
        "--include-package=dkim",
        "--include-package=uharfbuzz",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tkinter",
        "--assume-yes-for-downloads",
        f"--output-dir={BUILD}",
        # macOS ist nicht case-sensitiv: "FED" würde mit dem Paketordner "fed/" kollidieren
        f"--output-filename={'FED-Duplicator' if system == 'macos' else 'FED'}",
        "--product-name=FED - Forensic Email Duplicator",
        "--company-name=FED Project",
        f"--file-version={numeric_version(v)}",
        f"--product-version={numeric_version(v)}",
        "--file-description=FED - Forensic Email Duplicator",
        "--copyright=GPL-3.0-only",
    ]
    if system == "windows":
        args += ["--msvc=latest", "--windows-console-mode=disable", f"--windows-icon-from-ico={LOGO / 'fed.ico'}"]
    elif system == "macos":
        args += ["--macos-create-app-bundle", "--macos-app-name=FED", f"--macos-app-version={v}",
                 f"--macos-app-icon={LOGO / 'fed.icns'}"]
    return [*args, str(ROOT / "packaging" / "fed_launcher.py")]


def build_windows_launcher(v: str) -> Path:
    """Kleines Startprogramm FED.exe (packaging/windows/launcher.c) mit MSVC bauen.

    Es liegt oben im Ordner FED\\ und startet app\\FED.exe – so sieht man im Ordner nur FED.exe,
    config.toml und die Dokumente, ohne dass sich das Programm beim Start entpacken muss.
    """
    src = ROOT / "packaging" / "windows"
    work = BUILD / "launcher"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    for f in ("launcher.c", "launcher.rc"):
        shutil.copy2(src / f, work / f)
    shutil.copy2(LOGO / "fed.ico", work / "fed.ico")
    num = numeric_version(v)
    (work / "version.h").write_text(f'#define FED_VER_COMMA {num.replace(".", ",")}\n'
                                    f'#define FED_VER_STR "{v}"\n', encoding="ascii")
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    vs = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                        capture_output=True, text=True, check=True).stdout.strip()
    vcvars = Path(vs) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    # statische C-Laufzeit (/MT): das Startprogramm braucht keine DLL neben sich
    # als .bat, weil cmd.exe die von Python gesetzten Anführungszeichen sonst falsch liest
    (work / "build.bat").write_text(
        "@echo off\r\n"
        f'call "{vcvars}" >nul || exit /b 1\r\n'
        "rc /nologo /fo launcher.res launcher.rc || exit /b 1\r\n"
        "cl /nologo /O2 /W4 /MT /DUNICODE /D_UNICODE launcher.c launcher.res /Fe:FED.exe "
        "/link /SUBSYSTEM:WINDOWS user32.lib || exit /b 1\r\n", encoding="utf-8")
    subprocess.run(["cmd", "/d", "/c", "build.bat"], cwd=work, check=True)
    return work / "FED.exe"


def package(system: str, arch: str, v: str) -> Path:
    DIST.mkdir(exist_ok=True)
    name = f"FED-{v}-{system}-{arch}"
    stage = BUILD / name
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    if system == "macos":
        app = next(BUILD.glob("*.app"))
        shutil.copytree(app, stage / "FED.app", symlinks=True)
    elif system == "windows":  # FED\\FED.exe (Startprogramm) + FED\\app\\ (Programm)
        shutil.copytree(BUILD / "fed_launcher.dist", stage / "FED" / "app")
        shutil.copy2(BUILD / "launcher" / "FED.exe", stage / "FED" / "FED.exe")
    else:
        shutil.copytree(BUILD / "fed_launcher.dist", stage / "FED")
    target_dir = stage if system == "macos" else stage / "FED"
    for f in EXTRAS:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, target_dir / f)
    if system == "windows":
        out = DIST / f"{name}.zip"
        root = stage / "FED"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for f in sorted(root.rglob("*")):
                zf.write(f, Path("FED") / f.relative_to(root))
    elif system == "linux":
        out = DIST / f"{name}.tar.gz"
        with tarfile.open(out, "w:gz") as tf:
            tf.add(stage / "FED", arcname="FED")
    elif system == "macos":
        out = DIST / f"{name}.zip"
        # ditto erhält Symlinks und Bundle-Attribute
        subprocess.run(["ditto", "-c", "-k", "--keepParent", "FED.app", str(out)], cwd=stage, check=True)
        for f in EXTRAS:
            if (target_dir / f).exists():
                subprocess.run(["zip", "-j", str(out), str(target_dir / f)], check=True)
    return out


def main() -> int:
    system, arch = target()
    v = version()
    print(f"FED {v} für {system}-{arch} bauen …", flush=True)
    for old in BUILD.glob("*.app"):
        shutil.rmtree(old)
    subprocess.run(nuitka_args(system, v), check=True, cwd=ROOT)
    if system == "windows":
        build_windows_launcher(v)
    out = package(system, arch, v)
    print(f"fertig: {out}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"archive={out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
