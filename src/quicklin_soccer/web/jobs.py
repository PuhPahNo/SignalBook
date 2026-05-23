from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TIMEOUT = object()


@dataclass
class Job:
    id: int
    kind: str
    status: str
    started_at: float
    command: list[str]
    timeout_seconds: int | None
    finished_at: float | None = None
    returncode: int | None = None
    pid: int | None = None
    cancel_requested: bool = False
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "pid": self.pid,
            "cancel_requested": self.cancel_requested,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "command": self.command,
        }


class JobRunner:
    def __init__(self, timeout_seconds: int = 180):
        self.timeout_seconds = timeout_seconds
        self._jobs: dict[int, Job] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def start(self, kind: str, args: list[str], timeout_seconds: int | None | object = _DEFAULT_TIMEOUT) -> Job:
        timeout = self.timeout_seconds if timeout_seconds is _DEFAULT_TIMEOUT else timeout_seconds
        with self._lock:
            job = Job(
                id=self._next_id,
                kind=kind,
                status="running",
                started_at=time.time(),
                command=[sys.executable, str(ROOT / "signalbook.py"), *args],
                timeout_seconds=timeout if isinstance(timeout, int) else None,
            )
            self._jobs[job.id] = job
            self._next_id += 1

        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                job.as_dict()
                for job in sorted(self._jobs.values(), key=lambda item: item.id, reverse=True)
            ]

    def running(self, kind: str | None = None) -> list[Job]:
        with self._lock:
            return [
                job
                for job in sorted(self._jobs.values(), key=lambda item: item.id, reverse=True)
                if job.status == "running" and (kind is None or job.kind == kind)
            ]

    def cancel(self, job_id: int) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status != "running" or job.pid is None:
                return job.as_dict()
            job.cancel_requested = True
            pid = job.pid

        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        return job.as_dict()

    def cancel_kind(self, kind: str) -> list[dict[str, Any]]:
        targets = [job.id for job in self.running(kind)]
        results: list[dict[str, Any]] = []
        for job_id in targets:
            result = self.cancel(job_id)
            if result is not None:
                results.append(result)
        return results

    def _run(self, job: Job) -> None:
        process = subprocess.Popen(
            job.command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        with self._lock:
            job.pid = process.pid
        try:
            stdout, stderr = process.communicate(timeout=job.timeout_seconds)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            stderr = f"{stderr}\nJob timed out after {self.timeout_seconds} seconds."
            returncode = 124

        with self._lock:
            job.finished_at = time.time()
            job.returncode = returncode
            job.stdout = stdout[-12000:]
            job.stderr = stderr[-12000:]
            if job.cancel_requested:
                job.status = "cancelled"
            else:
                job.status = "ok" if returncode == 0 else "failed"
