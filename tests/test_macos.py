"""macOS App Translocation: Einstellungen nicht in der schreibgeschützten Kopie speichern."""

from pathlib import Path

import pytest

from fed.core import macos
from fed.gui import app as appmod

TRANS = Path("/private/var/folders/5b/xyz/T/AppTranslocation/BFB19BEC-2ACB-4655-880C-7928004E1337/d/FED.app")


@pytest.fixture(autouse=True)
def _reset():
    appmod.TRANSLOCATION = None
    yield
    appmod.TRANSLOCATION = None


def test_is_translocated(monkeypatch):
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    assert macos.is_translocated(TRANS)
    assert not macos.is_translocated(Path("/Users/max/Downloads/FED.app"))
    monkeypatch.setattr(macos.sys, "platform", "linux")
    assert not macos.is_translocated(TRANS)


def test_normal_bundle_uses_folder_of_app(tmp_path):
    exe = tmp_path / "FED.app" / "Contents" / "MacOS" / "FED-Duplicator"
    assert appmod.resolve_program_dir(exe) == tmp_path
    assert appmod.TRANSLOCATION is None


def test_translocated_uses_original_location(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr(macos, "_original_via_security", lambda b: downloads / "FED.app")
    exe = TRANS / "Contents" / "MacOS" / "FED-Duplicator"
    assert appmod.resolve_program_dir(exe) == downloads
    assert appmod.TRANSLOCATION["original"] == downloads / "FED.app"


def test_translocated_without_api_falls_back_to_user_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    monkeypatch.setattr(macos, "_original_via_security", lambda b: None)
    monkeypatch.setattr(macos, "user_data_dir", lambda: tmp_path / "Application Support" / "FED")
    exe = TRANS / "Contents" / "MacOS" / "FED-Duplicator"
    d = appmod.resolve_program_dir(exe)
    assert d == tmp_path / "Application Support" / "FED" and d.is_dir()
    assert appmod.TRANSLOCATION["original"] is None


def test_translocated_api_error_is_handled(monkeypatch, tmp_path):
    monkeypatch.setattr(macos.sys, "platform", "darwin")

    def boom(_b):
        raise OSError("Security.framework fehlt")

    monkeypatch.setattr(macos, "_original_via_security", boom)
    assert macos.original_bundle(TRANS) is None


def test_hint_text_mentions_fix(tmp_path):
    text = appmod.translocation_text({"running": TRANS, "original": tmp_path / "FED.app", "config_dir": tmp_path})
    assert "xattr -dr com.apple.quarantine" in text and str(tmp_path) in text


@pytest.mark.skipif(macos.sys.platform != "darwin", reason="nur macOS")
def test_security_api_callable_on_macos(tmp_path):
    """Die echte Schnittstelle lässt sich laden und aufrufen (ohne Translocation: Pfad oder None)."""
    bundle = tmp_path / "FED.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    res = macos._original_via_security(bundle)
    assert res is None or res.name == "FED.app"


def test_windows_layout_uses_folder_above_app(tmp_path):
    """Windows: FED\\FED.exe (Startprogramm) + FED\\app\\FED.exe -> Einstellungen in FED\\."""
    (tmp_path / "FED" / "app").mkdir(parents=True)
    (tmp_path / "FED" / "FED.exe").write_bytes(b"MZ")
    assert appmod.resolve_program_dir(tmp_path / "FED" / "app" / "FED.exe") == tmp_path / "FED"
    # ohne Startprogramm darüber bleibt es der Ordner der Programmdatei
    (tmp_path / "X" / "app").mkdir(parents=True)
    assert appmod.resolve_program_dir(tmp_path / "X" / "app" / "FED.exe") == tmp_path / "X" / "app"
