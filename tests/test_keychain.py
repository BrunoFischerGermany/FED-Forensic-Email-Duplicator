"""macOS-Schlüsselbund-Zugriff (ohne echtes `security`, per Monkeypatch)."""

import subprocess

from fed.core import keychain


def test_internet_password_reads_stdout(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="geheim123\n", stderr="")

    monkeypatch.setattr(keychain, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert keychain.internet_password("imap.web.de", "a@web.de", 993) == "geheim123"
    assert calls[0][:2] == ["security", "find-internet-password"]
    assert "imap.web.de" in calls[0] and "a@web.de" in calls[0]


def test_retries_without_port_then_fails(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append("-P" in cmd)
        return subprocess.CompletedProcess(cmd, 44, stdout="", stderr="not found")

    monkeypatch.setattr(keychain, "available", lambda: True)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert keychain.internet_password("h", "a@b.de", 993) is None
    assert seen == [True, False]  # erst mit Port, dann ohne


def test_not_on_other_os(monkeypatch):
    monkeypatch.setattr(keychain, "available", lambda: False)
    assert keychain.internet_password("h", "a@b.de") is None
