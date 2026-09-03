"""Detection and recoverable quarantine of interrupted Pydra cache entries."""

from __future__ import annotations

import shutil
import socket
from datetime import datetime
from pathlib import Path
from typing import Any

import cloudpickle

from .errors import ValidationError
from .job import inspect_job_lock, process_is_running
from .utils import write_json


def _cache_result_state(path: Path) -> tuple[bool, str]:
    try:
        result = cloudpickle.loads(path.read_bytes())
    except Exception as error:
        return False, f"unreadable result: {type(error).__name__}: {error}"
    if bool(getattr(result, "errored", False)):
        return False, "result was marked errored"
    if getattr(result, "output", None) is None:
        return False, "result was marked successful but contains no output"
    return True, "valid"


def _task_name(entry: Path) -> str | None:
    task_file = entry / "_task.pklz"
    if not task_file.is_file():
        return None
    try:
        task = cloudpickle.loads(task_file.read_bytes())
    except Exception:
        return None
    value = getattr(task, "name", None)
    return str(value) if value else None


def _lock_state(path: Path) -> dict[str, Any]:
    """Classify modern filelock records and conservatively handle legacy locks."""
    try:
        content = path.read_text()
        age = max(0.0, datetime.now().timestamp() - path.stat().st_mtime)
    except OSError:
        return {"path": str(path), "state": "unknown", "pid": None, "hostname": None}
    lines = content.splitlines()
    try:
        pid = int(lines[0]) if lines else 0
    except ValueError:
        pid = 0
    hostname = lines[1] if len(lines) >= 2 else ""
    if pid > 0 and (not hostname or hostname == socket.gethostname()):
        state = "active" if process_is_running(pid) else "stale"
    elif pid > 0 and hostname != socket.gethostname():
        state = "unknown"
    else:
        # Age alone cannot prove that a legacy empty lock is stale because a
        # valid external command can run for hours. Leave malformed ownership
        # records untouched.
        state = "unknown"
    return {
        "path": str(path),
        "state": state,
        "pid": pid or None,
        "hostname": hostname or None,
        "age_seconds": age,
    }


def inspect_pydra_cache(cache_dir: str | Path) -> dict[str, Any]:
    """Inspect only active top-level cache entries; backup folders are ignored."""
    root = Path(cache_dir).expanduser().resolve()
    lock_details = (
        [_lock_state(path) for path in sorted(root.glob("*.lock"))]
        if root.exists()
        else []
    )
    invalid: list[dict[str, str]] = []
    valid_entries: list[dict[str, str | None]] = []
    incomplete: list[dict[str, str]] = []
    active_lock_names = {
        Path(record["path"]).stem
        for record in lock_details
        if record["state"] != "stale"
    }
    if root.exists():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(("FunctionTask_", "Workflow_")):
                continue
            result_file = entry / "_result.pklz"
            if not result_file.is_file():
                record = {"entry": str(entry), "reason": "result file is missing"}
                if entry.name in active_lock_names:
                    incomplete.append(record)
                else:
                    invalid.append(record)
                continue
            is_valid, reason = _cache_result_state(result_file)
            if is_valid:
                valid_entries.append(
                    {
                        "entry": str(entry),
                        "cache_key": entry.name,
                        "task_name": _task_name(entry),
                    }
                )
            else:
                invalid.append({"entry": str(entry), "reason": reason})
    return {
        "cache_dir": str(root),
        "locks": [record["path"] for record in lock_details],
        "lock_details": lock_details,
        "active_locks": [
            record["path"] for record in lock_details if record["state"] != "stale"
        ],
        "stale_locks": [
            record["path"] for record in lock_details if record["state"] == "stale"
        ],
        "valid_entries": valid_entries,
        "incomplete_entries": incomplete,
        "invalid_entries": invalid,
    }


def recover_interrupted_pydra_cache(
    cache_dir: str | Path,
    *,
    current_job_pid: int | None = None,
) -> dict[str, Any]:
    """Move invalid cache entries aside while preserving all valid completed work."""
    root = Path(cache_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    job_lock = inspect_job_lock(root.parent)
    lock_owner = job_lock.get("owner") or {}
    owned_by_caller = (
        current_job_pid is not None
        and job_lock["state"] == "active"
        and lock_owner.get("pid") == current_job_pid
    )
    if job_lock["state"] in {"active", "unknown"} and not owned_by_caller:
        raise ValidationError(
            "The work directory belongs to an active or uncertain job. Cache recovery is "
            "read-write and will not run until that job exits. Inspect it with "
            "'cnapfmriprep status --work-dir ...'."
        )
    inspection = inspect_pydra_cache(root)
    if inspection["active_locks"]:
        raise ValidationError(
            "The Pydra cache is locked by an active or unclean workflow. Do not start "
            "a second process in the same work directory. Lock files: "
            + ", ".join(inspection["active_locks"])
        )
    invalid = inspection["invalid_entries"]
    stale_locks = inspection["stale_locks"]
    if not invalid and not stale_locks:
        return {
            **inspection,
            "recovered": [],
            "recovered_locks": [],
            "backup_dir": None,
        }
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    backup = root / "interrupted-cache-backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    recovered: list[dict[str, str]] = []
    for record in invalid:
        source = Path(record["entry"])
        target = backup / source.name
        shutil.move(str(source), str(target))
        recovered.append(
            {
                "entry": str(source),
                "backup": str(target),
                "reason": record["reason"],
            }
        )
    recovered_locks: list[dict[str, str]] = []
    for lock_name in stale_locks:
        source = Path(lock_name)
        target = backup / source.name
        try:
            source.replace(target)
        except FileNotFoundError:
            continue
        recovered_locks.append({"lock": str(source), "backup": str(target)})
    report = {
        "cache_dir": str(root),
        "locks": [],
        "lock_details": [],
        "active_locks": [],
        "stale_locks": [],
        "valid_entries": inspection["valid_entries"],
        "incomplete_entries": [],
        "invalid_entries": [],
        "recovered": recovered,
        "recovered_locks": recovered_locks,
        "backup_dir": str(backup),
    }
    write_json(backup / "recovery_report.json", report)
    return report
