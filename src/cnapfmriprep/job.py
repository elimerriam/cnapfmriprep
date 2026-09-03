"""Durable job metadata and single-writer work-directory leases."""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .utils import ensure_dir, read_json, write_json

JOB_MANIFEST_NAME = "job.json"
JOB_LOCK_NAME = "job.lock"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def process_is_running(pid: int) -> bool:
    """Return whether *pid* exists and is signalable by the current user."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def graceful_shutdown_signals() -> Iterator[None]:
    """Convert SIGTERM into the same restart-safe path used for Ctrl-C."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def request_shutdown(signum: int, frame: Any) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def inspect_job_lock(work_dir: str | Path) -> dict[str, Any]:
    """Inspect a job lease without changing it."""
    root = Path(work_dir).expanduser().resolve()
    path = root / JOB_LOCK_NAME
    if not path.exists():
        return {"path": str(path), "state": "absent", "owner": None}
    try:
        owner = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        age = max(0.0, datetime.now().timestamp() - path.stat().st_mtime)
        return {"path": str(path), "state": "unknown", "owner": None, "age_seconds": age}
    if not isinstance(owner, dict):
        return {"path": str(path), "state": "unknown", "owner": None}
    hostname = str(owner.get("hostname") or "")
    try:
        pid = int(owner.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if hostname and hostname != socket.gethostname():
        state = "unknown"
    else:
        state = "active" if process_is_running(pid) else "stale"
    return {"path": str(path), "state": state, "owner": owner}


class WorkDirectoryLease:
    """Prevent simultaneous writers while safely replacing known-stale leases."""

    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = ensure_dir(work_dir)
        self.path = self.work_dir / JOB_LOCK_NAME
        self.token = uuid.uuid4().hex
        self.owner = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": _now(),
            "token": self.token,
        }
        self.archived_stale_lock: str | None = None
        self._held = False

    def acquire(self) -> WorkDirectoryLease:
        payload = (json.dumps(self.owner, sort_keys=True) + "\n").encode()
        for _ in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o664,
                )
            except FileExistsError:
                try:
                    stale_stat = self.path.stat()
                    stale_identity = stale_stat.st_dev, stale_stat.st_ino
                except FileNotFoundError:
                    continue
                inspection = inspect_job_lock(self.work_dir)
                if inspection["state"] != "stale":
                    owner = inspection.get("owner") or {}
                    detail = (
                        f"pid {owner.get('pid')} on {owner.get('hostname')}"
                        if owner
                        else "an unknown process"
                    )
                    raise ValidationError(
                        f"Work directory is already in use by {detail}: {self.work_dir}. "
                        "Use 'cnapfmriprep status --work-dir ...' from another terminal."
                    ) from None
                stale_dir = ensure_dir(self.work_dir / "stale-locks")
                stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
                archived = stale_dir / f"job-{stamp}.lock"
                try:
                    current_stat = self.path.stat()
                    if (current_stat.st_dev, current_stat.st_ino) != stale_identity:
                        continue
                    self.path.replace(archived)
                except FileNotFoundError:
                    continue
                self.archived_stale_lock = str(archived)
                continue
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            self._held = True
            return self
        raise ValidationError(f"Could not acquire work-directory lease: {self.work_dir}")

    def release(self) -> None:
        if not self._held:
            return
        try:
            current = json.loads(self.path.read_text())
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self._held = False
            return
        if isinstance(current, dict) and current.get("token") == self.token:
            self.path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> WorkDirectoryLease:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


def read_job_manifest(work_dir: str | Path) -> dict[str, Any] | None:
    """Read a job manifest when present."""
    path = Path(work_dir).expanduser().resolve() / JOB_MANIFEST_NAME
    if not path.is_file():
        return None
    return read_json(path)


def begin_job_attempt(
    work_dir: str | Path,
    *,
    command: str,
    invocation: dict[str, Any],
) -> dict[str, Any]:
    """Record a new execution attempt while preserving prior attempt history."""
    root = ensure_dir(work_dir)
    previous = read_job_manifest(root) or {}
    attempts = list(previous.get("attempts") or [])
    if attempts and attempts[-1].get("state") == "running":
        attempts[-1] = dict(attempts[-1]) | {
            "state": "interrupted",
            "finished_at": _now(),
            "error": "previous owner was no longer active when the next attempt began",
        }
    attempt_number = len(attempts) + 1
    attempt = {
        "number": attempt_number,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": _now(),
        "finished_at": None,
        "state": "running",
        "error": None,
    }
    attempts.append(attempt)
    manifest = {
        "schema_version": 1,
        "command": command,
        "state": "running",
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "created_at": previous.get("created_at") or attempt["started_at"],
        "updated_at": attempt["started_at"],
        "attempt": attempt_number,
        "attempts": attempts,
        "invocation": invocation,
        "runs": list(previous.get("runs") or []),
    }
    write_json(root / JOB_MANIFEST_NAME, manifest)
    return manifest


def update_job_manifest(work_dir: str | Path, **updates: Any) -> dict[str, Any]:
    """Atomically merge fields into the current job manifest."""
    root = Path(work_dir).expanduser().resolve()
    manifest = read_job_manifest(root)
    if manifest is None:
        raise ValidationError(f"No job manifest exists in {root}")
    manifest.update(updates)
    manifest["updated_at"] = _now()
    write_json(root / JOB_MANIFEST_NAME, manifest)
    return manifest


def finish_job_attempt(
    work_dir: str | Path,
    state: str,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    """Finish the current manifest attempt as completed, failed, or interrupted."""
    root = Path(work_dir).expanduser().resolve()
    manifest = read_job_manifest(root)
    if manifest is None:
        raise ValidationError(f"No job manifest exists in {root}")
    attempts = list(manifest.get("attempts") or [])
    if attempts:
        attempts[-1] = dict(attempts[-1]) | {
            "state": state,
            "finished_at": _now(),
            "error": error,
        }
    manifest.update(
        {
            "state": state,
            "updated_at": _now(),
            "attempts": attempts,
            "error": error,
        }
    )
    write_json(root / JOB_MANIFEST_NAME, manifest)
    return manifest
