"""Sicherungen als Aufträge, die unabhängig von der angezeigten Seite laufen.

Jedes Konto hat höchstens einen Auftrag. Während er im Hintergrund läuft, kann im Assistenten
schon das nächste Konto vorbereitet und gestartet werden; die linke Leiste zeigt den Fortschritt.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from fed.core import acquisition as acq
from fed.gui.widgets import run_async


class Job(QObject):
    progress = Signal(object)  # Job
    finished = Signal(object)  # Job

    def __init__(self, st, account_id: int, email: str, evidence: str, connect, parent=None) -> None:
        super().__init__(parent)
        self.st = st
        self.case = st.case
        self.cfg = st.cfg
        self.account_id = account_id
        self.email = email
        self.evidence = evidence
        self.connect = connect  # eigene Kopie – das Formular kann schon das nächste Konto enthalten
        self.worker = None
        self.last = None  # letzter acq.Progress
        self.result = None  # acq.AttemptResult
        self.error: str = ""
        self.mode = acq.Mode.RESUME
        self.started_wall = 0.0
        self.log_from = 0  # Log-Einträge ab hier gehören (auch) zu diesem Auftrag
        self.cancelling = False

    # ------------------------------------------------------------ Zustand
    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.thread.isRunning() and self.result is None and not self.error

    @property
    def status(self) -> str:
        """running | done | cancelled | failed | error"""
        if self.running:
            return "running"
        if self.result is not None:
            return self.result.status
        return "error" if self.error else "running"

    @property
    def percent(self) -> int:
        p = self.last
        if self.result is not None and self.result.status == "done":
            return 100
        if p is None or not p.bytes_total:
            return 0
        return min(100, int(100 * p.bytes_done / p.bytes_total))

    # ------------------------------------------------------------ Ablauf
    def start(self, mode=acq.Mode.RESUME, reason: str = "", delete_old: bool = False) -> None:
        from fed.gui.assistant import stay_awake

        self.mode = mode
        self.result, self.error, self.last, self.cancelling = None, "", None, False
        self.started_wall = time.time()
        self.log_from = self.case.conn.execute("SELECT COALESCE(MAX(id), 0) FROM log").fetchone()[0]
        case, account_id, connect, cfg = self.case, self.account_id, self.connect, self.cfg

        def work(cancel, report):
            return acq.run_attempt(case, account_id, connect, mode, cancel, report, reason, delete_old, cfg)

        stay_awake(self.st, "acquisition")
        self.worker = run_async(work, self._done, self._failed, self._progress, parent=self)

    def cancel(self) -> None:
        if self.worker is not None and self.running:
            self.cancelling = True
            self.worker.cancel.cancel()

    def _progress(self, p) -> None:
        self.last = p
        self.progress.emit(self)

    def _done(self, res) -> None:
        from fed.gui.assistant import allow_sleep

        allow_sleep(self.st)
        self.result = res
        self.st.attempts.append(res)
        self.finished.emit(self)

    def _failed(self, err: str, tb: str) -> None:
        from fed.gui.assistant import allow_sleep

        allow_sleep(self.st)
        self.error = err
        self.finished.emit(self)


class JobManager(QObject):
    """Alle Aufträge des geöffneten Case (je Konto höchstens einer)."""

    changed = Signal()  # Auftrag gestartet oder beendet
    progress = Signal(object)  # Job

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.jobs: dict[int, Job] = {}

    def get(self, account_id: int | None) -> Job | None:
        return self.jobs.get(account_id) if account_id is not None else None

    def is_running(self, account_id: int | None) -> bool:
        j = self.get(account_id)
        return j is not None and j.running

    def running(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.running]

    def start(self, st, account_id: int, email: str, evidence: str, connect, mode=acq.Mode.RESUME,
              reason: str = "", delete_old: bool = False) -> Job:
        job = self.jobs.get(account_id)
        if job is not None and job.running:
            return job
        if job is None:
            job = Job(st, account_id, email, evidence, connect, self)
            job.progress.connect(self.progress.emit)
            job.finished.connect(lambda _j: self.changed.emit())
            self.jobs[account_id] = job
        else:
            job.connect = connect or job.connect
        job.start(mode, reason, delete_old)
        self.changed.emit()
        return job

    def cancel_all(self, wait_ms: int = 15000) -> None:
        jobs = self.running()
        for j in jobs:
            j.cancel()
        deadline = time.monotonic() + wait_ms / 1000
        for j in jobs:
            j.worker.thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))

    def clear(self) -> None:
        """Neuer Case: beendete Aufträge vergessen (laufende gibt es dann nicht mehr)."""
        for j in list(self.jobs.values()):
            if not j.running:
                j.deleteLater()
        self.jobs = {k: j for k, j in self.jobs.items() if j.running}
