"""set_version.py: Commit-Kurz-ID nur bei reinen Nicht-Release-Versionen anhängen."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "packaging" / "set_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("fed_set_version", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sha_suffix_only_for_plain_nonrelease_version(tmp_path, monkeypatch):
    sv = _load()
    init = tmp_path / "__init__.py"
    monkeypatch.setattr(sv, "INIT", init)

    # manueller Build (SHA gesetzt, reine Version) -> Kurz-ID angehängt
    monkeypatch.setenv("FED_BUILD_SHA", "2bc802f1234567deadbeef")
    init.write_text('__version__ = "x"\n', encoding="utf-8")
    assert sv.main(["1.0.3"]) == 0
    assert '__version__ = "1.0.3-2bc802f"' in init.read_text(encoding="utf-8")

    # Nightly (Version trägt bereits Suffix) -> unberührt
    init.write_text('__version__ = "x"\n', encoding="utf-8")
    assert sv.main(["1.0.3-nightly-20261117-aab8dd2"]) == 0
    assert '__version__ = "1.0.3-nightly-20261117-aab8dd2"' in init.read_text(encoding="utf-8")

    # Release (kein SHA) -> reine Version
    monkeypatch.delenv("FED_BUILD_SHA", raising=False)
    init.write_text('__version__ = "x"\n', encoding="utf-8")
    assert sv.main(["1.0.3"]) == 0
    assert '__version__ = "1.0.3"' in init.read_text(encoding="utf-8")
