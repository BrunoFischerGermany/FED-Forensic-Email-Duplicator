import subprocess
import sys

from fed.core import keepawake
from fed.core.keepawake import KeepAwake


class FakeProc:
    def __init__(self, *a, **k):
        self.terminated = False

    def wait(self, timeout=None):
        if not self.terminated:
            raise subprocess.TimeoutExpired("x", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


def test_nesting_and_release(monkeypatch):
    started, stopped = [], []
    ka = KeepAwake()
    monkeypatch.setattr(ka, "_start", lambda: started.append(1) or "test")
    monkeypatch.setattr(ka, "_stop", lambda: stopped.append(1))
    assert ka.acquire() == "test" and ka.acquire() == "test"
    ka.release()
    assert ka.active and not stopped
    ka.release()
    assert not ka.active and started == [1] and stopped == [1]
    ka.release()  # zusätzliches release ist harmlos
    assert stopped == [1]


def test_linux_uses_inhibitor(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keepawake.shutil, "which", lambda c: "/usr/bin/" + c if c == "systemd-inhibit" else None)
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(keepawake.subprocess, "Popen", popen)
    with KeepAwake() as ka:
        assert ka.method == "systemd-inhibit"
        assert "--what=idle:sleep:handle-lid-switch" in calls[0]
    assert ka._proc is None


def test_no_method_available(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keepawake.shutil, "which", lambda c: None)
    ka = KeepAwake()
    assert ka.acquire() is None and ka.active
    ka.release()


def test_failure_never_raises(monkeypatch):
    ka = KeepAwake()

    def boom():
        raise OSError("kaputt")

    monkeypatch.setattr(ka, "_start", boom)
    assert ka.acquire() is None
    ka.release()
