"""Read-only reduction of job manifests, progress events, and cache state."""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from .cache import inspect_pydra_cache
from .errors import ValidationError
from .job import inspect_job_lock, read_job_manifest

RUN_STAGE_ORDER = (
    "NORDIC",
    "TOPUP",
    "SDC warp preparation",
    "motion correction and resampling",
    "QC",
    "publishing",
)

_STAGE_ALIASES = {"distortion field": "SDC warp preparation"}
_TASK_STAGES = set(RUN_STAGE_ORDER) - {"publishing"}


def resolve_status_work_dir(work_dir: str | Path) -> Path:
    """Resolve a direct preprocessing directory or a full-run parent directory."""
    root = Path(work_dir).expanduser().resolve()
    candidates = (root, root / "preprocess")
    for candidate in candidates:
        if (candidate / "job.json").is_file() or (candidate / "progress.jsonl").is_file():
            return candidate
    raise ValidationError(
        f"No cnapfmriprep job manifest or progress journal was found in {root}"
    )


def read_progress_events(path: str | Path) -> list[dict[str, Any]]:
    """Read complete JSON objects while tolerating a partially written final line."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = source.read_text().splitlines()
    except OSError as error:
        raise ValidationError(f"Could not read progress journal {source}: {error}") from error
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _job_is_active(manifest: dict[str, Any] | None, lock: dict[str, Any]) -> bool:
    if lock["state"] == "active":
        return True
    if manifest:
        # Versioned jobs always hold job.lock for their full lifetime. A
        # still-running PID may be the caller inspecting an abandoned test or
        # embedded invocation, so a missing lease is the authoritative signal.
        return manifest.get("state") == "running" and lock["state"] == "unknown"
    if lock["state"] == "unknown":
        return True
    return False


def _current_attempt_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    boundary = 0
    for index, event in enumerate(events):
        if (
            event.get("run_index") is None
            and event.get("stage") == "preprocessing"
            and event.get("status") == "started"
        ):
            boundary = index
    return events[boundary:]


def _duration_history(events: list[dict[str, Any]]) -> dict[str, float]:
    samples: dict[str, list[float]] = {}
    for event in events:
        if event.get("status") != "finished":
            continue
        try:
            elapsed = float(event.get("elapsed_seconds"))
        except (TypeError, ValueError):
            continue
        if elapsed >= 0:
            samples.setdefault(str(event.get("stage")), []).append(elapsed)
    return {stage: float(statistics.median(values)) for stage, values in samples.items()}


def _cache_usage(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    reused: list[dict[str, Any]] = []
    recomputed: list[dict[str, Any]] = []
    for event in events:
        stage = _STAGE_ALIASES.get(str(event.get("stage")), str(event.get("stage")))
        if stage not in _TASK_STAGES:
            continue
        record = {
            "run_index": event.get("run_index"),
            "run_label": event.get("run_label"),
            "stage": stage,
            "cache_key": (
                str(event.get("message")).removeprefix("reused ")
                if event.get("status") == "cached" and event.get("message")
                else None
            ),
        }
        if event.get("status") == "cached":
            reused.append(record)
        elif event.get("status") == "finished":
            recomputed.append(record)
    return {"reused": reused, "recomputed": recomputed}


def _stage_status(
    stage: str,
    events: list[dict[str, Any]],
    *,
    active: bool,
    typical_seconds: float | None,
    now: datetime,
) -> dict[str, Any]:
    latest = events[-1] if events else None
    if latest is None:
        return {"stage": stage, "state": "waiting", "eta_seconds": typical_seconds}
    raw_state = str(latest.get("status") or "")
    states = {
        "finished": "completed",
        "cached": "cached",
        "failed": "failed",
        "interrupted": "interrupted",
    }
    if raw_state in {"started", "progress", "retrying"}:
        state = "running" if active else "interrupted"
    else:
        state = states.get(raw_state, raw_state or "unknown")
    completed = latest.get("completed")
    total = latest.get("total")
    started = next(
        (
            event
            for event in reversed(events)
            if event.get("status") == "started"
        ),
        None,
    )
    started_at = _parse_timestamp(started.get("timestamp")) if started else None
    elapsed = max((now - started_at).total_seconds(), 0.0) if started_at else None
    eta: float | None = None
    try:
        completed_number = int(completed) if completed is not None else None
        total_number = int(total) if total is not None else None
    except (TypeError, ValueError):
        completed_number = total_number = None
    if (
        state == "running"
        and elapsed is not None
        and completed_number is not None
        and total_number is not None
        and completed_number > 0
        and total_number >= completed_number
    ):
        phase_events: list[dict[str, Any]] = []
        if raw_state == "progress" and latest.get("message"):
            for event in reversed(events):
                if (
                    event.get("status") == "progress"
                    and event.get("message") == latest.get("message")
                ):
                    phase_events.append(event)
                elif phase_events:
                    break
        first_phase = phase_events[-1] if phase_events else None
        first_phase_at = _parse_timestamp(first_phase.get("timestamp")) if first_phase else None
        try:
            first_completed = int(first_phase.get("completed")) if first_phase else 0
        except (TypeError, ValueError):
            first_completed = 0
        phase_elapsed = (
            max((now - first_phase_at).total_seconds(), 0.0)
            if first_phase_at is not None
            else elapsed
        )
        measured = completed_number - first_completed
        if phase_elapsed > 0 and measured > 0:
            eta = phase_elapsed * (total_number - completed_number) / measured
        else:
            eta = elapsed * (total_number - completed_number) / completed_number
    elif state == "running" and elapsed is not None and typical_seconds is not None:
        eta = max(typical_seconds - elapsed, 0.0)
    elif state == "waiting":
        eta = typical_seconds
    return {
        "stage": stage,
        "state": state,
        "message": latest.get("message"),
        "completed": completed_number,
        "total": total_number,
        "elapsed_seconds": latest.get("elapsed_seconds") if state == "completed" else elapsed,
        "eta_seconds": eta,
        "timestamp": latest.get("timestamp"),
    }


def _known_runs(
    manifest: dict[str, Any] | None,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if manifest and manifest.get("runs"):
        return [dict(run) for run in manifest["runs"]]
    found: dict[int, dict[str, Any]] = {}
    for event in events:
        index = event.get("run_index")
        if index is None:
            continue
        try:
            number = int(index)
        except (TypeError, ValueError):
            continue
        found[number] = {
            "index": number,
            "label": event.get("run_label") or f"Run {number}",
            "raw_bold": None,
        }
    return [found[index] for index in sorted(found)]


def _summarize_run(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    active: bool,
    durations: dict[str, float],
    now: datetime,
    published: bool,
) -> dict[str, Any]:
    index = int(run["index"])
    matching = [event for event in events if event.get("run_index") == index]
    by_stage: dict[str, list[dict[str, Any]]] = {stage: [] for stage in RUN_STAGE_ORDER}
    for event in matching:
        stage = _STAGE_ALIASES.get(str(event.get("stage")), str(event.get("stage")))
        if stage in by_stage:
            by_stage[stage].append(event)
    stages = [
        _stage_status(
            stage,
            by_stage[stage],
            active=active,
            typical_seconds=durations.get(stage),
            now=now,
        )
        for stage in RUN_STAGE_ORDER
    ]
    failed = next((stage for stage in stages if stage["state"] == "failed"), None)
    running = next((stage for stage in stages if stage["state"] == "running"), None)
    interrupted = next((stage for stage in stages if stage["state"] == "interrupted"), None)
    publishing = stages[-1]
    qc = stages[-2]
    if published and publishing["state"] == "waiting":
        publishing = dict(publishing) | {"state": "completed", "message": "published"}
        stages[-1] = publishing
    latest_terminal = next(
        (stage for stage in reversed(stages) if stage["state"] in {"completed", "cached"}),
        None,
    )
    if failed:
        state, current = "failed", failed
    elif publishing["state"] == "completed":
        state, current = "completed", publishing
    elif qc["state"] in {"completed", "cached"} and run.get("publish", True):
        state, current = "unpublished", qc
    elif qc["state"] in {"completed", "cached"}:
        state, current = "completed", qc
    elif running:
        state, current = "running", running
    elif interrupted:
        state, current = "interrupted", interrupted
    elif latest_terminal and latest_terminal["state"] == "cached":
        state, current = "cached", latest_terminal
    elif active:
        state = "waiting"
        current = next((stage for stage in stages if stage["state"] == "waiting"), stages[0])
    elif latest_terminal:
        state, current = "interrupted", latest_terminal
    else:
        state, current = "waiting", stages[0]
    return {
        "index": index,
        "label": run.get("label") or f"Run {index}",
        "raw_bold": run.get("raw_bold"),
        "state": state,
        "current_stage": current["stage"],
        "progress": {
            "completed": current.get("completed"),
            "total": current.get("total"),
        },
        "eta_seconds": current.get("eta_seconds"),
        "message": current.get("message"),
        "stages": stages,
    }


def inspect_job_status(work_dir: str | Path) -> dict[str, Any]:
    """Return a complete, read-only status snapshot for one preprocessing job."""
    root = resolve_status_work_dir(work_dir)
    manifest = read_job_manifest(root)
    lock = inspect_job_lock(root)
    active = _job_is_active(manifest, lock)
    all_events = read_progress_events(root / "progress.jsonl")
    events = _current_attempt_events(all_events)
    durations = _duration_history(all_events)
    now = datetime.now().astimezone()
    published_indices: set[int] = set()
    result_file = root / "preprocess_result.json"
    if result_file.is_file() and (manifest is None or manifest.get("state") == "completed"):
        try:
            result_payload = json.loads(result_file.read_text())
            published_indices = {
                int(run["workflow_index"]) + 1
                for run in result_payload.get("runs", [])
                if isinstance(run, dict) and "workflow_index" in run
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            published_indices = set()
    runs = [
        _summarize_run(
            run,
            events,
            active=active,
            durations=durations,
            now=now,
            published=int(run["index"]) in published_indices,
        )
        for run in _known_runs(manifest, events)
    ]
    shared_events = [
        event
        for event in events
        if event.get("run_index") is None and event.get("stage") == "TOPUP"
    ]
    shared_topup = _stage_status(
        "TOPUP",
        shared_events,
        active=active,
        typical_seconds=durations.get("TOPUP"),
        now=now,
    )
    manifest_state = str((manifest or {}).get("state") or "unknown")
    if manifest_state == "running" and not active:
        overall_state = "interrupted"
    elif manifest_state != "unknown":
        overall_state = manifest_state
    elif any(run["state"] == "running" for run in runs):
        overall_state = "running"
    elif runs and all(run["state"] == "completed" for run in runs):
        overall_state = "completed"
    else:
        overall_state = "unknown"
    active_etas = [
        float(run["eta_seconds"])
        for run in runs
        if run.get("eta_seconds") is not None and run["state"] == "running"
    ]
    if shared_topup["state"] == "running" and shared_topup.get("eta_seconds") is not None:
        active_etas.append(float(shared_topup["eta_seconds"]))
    cache = inspect_pydra_cache(root / "pydra-cache")
    cache_usage = _cache_usage(events)
    return {
        "work_dir": str(root),
        "state": overall_state,
        "active": active,
        "pid": (manifest or {}).get("pid"),
        "attempt": (manifest or {}).get("attempt"),
        "updated_at": (manifest or {}).get("updated_at"),
        "estimated_remaining_seconds": max(active_etas) if active_etas else None,
        "shared_topup": shared_topup,
        "runs": runs,
        "cache": cache,
        "cache_usage": cache_usage,
        "lock": lock,
        "progress_event_count": len(all_events),
    }


def format_duration(seconds: Any) -> str:
    """Format a compact human-readable duration."""
    if seconds is None:
        return ""
    try:
        value = max(int(round(float(seconds))), 0)
    except (TypeError, ValueError):
        return ""
    minutes, seconds = divmod(value, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_job_status(report: dict[str, Any]) -> str:
    """Render a terminal-oriented session status summary."""
    header = f"Job {report['state']}"
    if report.get("active") and report.get("pid"):
        header += f" (PID {report['pid']})"
    lines = [header, f"Work directory: {report['work_dir']}"]
    if report.get("attempt"):
        lines.append(f"Attempt: {report['attempt']}")
    shared = report["shared_topup"]
    shared_detail = shared["state"]
    if shared.get("elapsed_seconds") is not None:
        shared_detail += f" in {format_duration(shared['elapsed_seconds'])}"
    if shared.get("eta_seconds") is not None and shared["state"] == "running":
        shared_detail += f", ETA {format_duration(shared['eta_seconds'])}"
    lines.extend(["", f"Shared TOPUP       {shared_detail}"])
    for run in report["runs"]:
        label = str(run["label"])
        detail = f"{run['state']}: {run['current_stage']}"
        completed = run["progress"].get("completed")
        total = run["progress"].get("total")
        if completed is not None and total is not None:
            detail += f" {completed}/{total}"
        if run.get("eta_seconds") is not None and run["state"] == "running":
            detail += f", ETA {format_duration(run['eta_seconds'])}"
        if run.get("message"):
            detail += f" ({run['message']})"
        lines.append(f"{label:<18} {detail}")
    cache = report["cache"]
    valid_count = len(cache.get("valid_entries") or [])
    invalid_count = len(cache.get("invalid_entries") or [])
    lines.extend(
        [
            "",
            f"Cache: {valid_count} valid, {invalid_count} invalid, "
            f"{len(cache.get('locks') or [])} lock(s)",
            f"This attempt: {len(report['cache_usage']['reused'])} reused, "
            f"{len(report['cache_usage']['recomputed'])} recomputed",
        ]
    )
    return "\n".join(lines)
