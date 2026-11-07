import json

import pytest

from validation import dovecot_server

pytestmark = pytest.mark.skipif(not dovecot_server.available(), reason="dovecot/openssl nicht installiert")


def test_validation_passes_and_writes_reports(tmp_path):
    from validation import run

    rc = run.main(["--out", str(tmp_path / "out"), "--lang", "de,en"])
    data = json.loads((tmp_path / "out" / "validation-report.json").read_text(encoding="utf-8"))
    failed = [r for r in data["results"] if r["status"] == "fail"]
    assert rc == 0 and data["ok"] and not failed, failed
    assert len(data["results"]) == len(run.CHECKS)
    for lang in ("de", "en"):
        assert (tmp_path / "out" / f"validation-report-{lang}.pdf").read_bytes().startswith(b"%PDF")
    assert "PASSED" in (tmp_path / "out" / "validation-report-en.txt").read_text(encoding="utf-8")


def test_validation_detects_missing_write_guard(tmp_path, monkeypatch):
    """Gegenprobe: ohne Sperre schreibender Befehle muss V02 fehlschlagen."""
    from fed.core import imap_reader
    from validation import run

    monkeypatch.setattr(imap_reader, "FORBIDDEN_COMMANDS", frozenset())
    rep = run.run(tmp_path)
    status = {r.id: r.status for r in rep.results}
    assert status["V02"] == run.FAIL and not rep.ok
