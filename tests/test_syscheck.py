

def test_powershell_without_console_window(monkeypatch):
    """Windows: PowerShell für die USB-Prüfung ohne eigenes Konsolenfenster starten."""
    import subprocess

    from fed.core import syscheck

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="USB\n", stderr="")

    monkeypatch.setattr(syscheck.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert syscheck._windows_bus_is_usb("E:\\") is True
    assert seen["creationflags"] == 0x08000000
    assert "-NonInteractive" in seen["cmd"]
