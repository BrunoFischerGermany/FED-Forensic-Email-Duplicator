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
        # Windows: eine einzelne .exe (entpackt sich beim Start in den Temp-Ordner und räumt danach auf)
        "--onefile" if system == "windows" else "--standalone",
        "--enable-plugin=pyside6",
        "--include-package=fed",
        "--include-package-data=fed",
        "--include-package-data=fpdf",
        "--include-package=dns",
        "--include-package=dkim",
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


def package(system: str, arch: str, v: str) -> Path:
    DIST.mkdir(exist_ok=True)
    name = f"FED-{v}-{system}-{arch}"
    if system == "windows":
        out = DIST / f"{name}.exe"
        shutil.copy2(BUILD / "FED.exe", out)
        return out
    stage = BUILD / name
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    if system == "macos":
        app = next(BUILD.glob("*.app"))
        shutil.copytree(app, stage / "FED.app", symlinks=True)
    else:
        shutil.copytree(BUILD / "fed_launcher.dist", stage / "FED")
    target_dir = stage if system == "macos" else stage / "FED"
    for f in EXTRAS:
        if (ROOT / f).exists():
            shutil.copy2(ROOT / f, target_dir / f)
    if system == "linux":
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
    out = package(system, arch, v)
    print(f"fertig: {out}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"archive={out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
