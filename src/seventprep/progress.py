"""Multiprocess-safe progress events and a terminal event printer."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


def progress_context(
    event_file: str | Path | None,
    *,
    run_index: int | None = None,
    run_count: int | None = None,
    run_label: str | None = None,
    interval_percent: int = 10,
) -> dict[str, Any] | None:
    if event_file is None:
        return None
    return {
        "event_file": str(Path(event_file).expanduser().resolve()),
        "run_index": run_index,
        "run_count": run_count,
        "run_label": run_label,
        "interval_percent": max(1, min(int(interval_percent), 100)),
    }


def emit_progress(
    context: dict[str, Any] | None,
    stage: str,
    status: str,
    *,
    message: str | None = None,
    completed: int | None = None,
    total: int | None = None,
    elapsed_seconds: float | None = None,
) -> None:
    """Append one short JSON event using a single atomic write."""
    if not context or not context.get("event_file"):
        return
    event = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage": stage,
        "status": status,
        "run_index": context.get("run_index"),
        "run_count": context.get("run_count"),
        "run_label": context.get("run_label"),
        "message": message,
        "completed": completed,
        "total": total,
        "elapsed_seconds": elapsed_seconds,
        "pid": os.getpid(),
    }
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = Path(str(context["event_file"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


@contextmanager
def progress_stage(
    context: dict[str, Any] | None,
    stage: str,
) -> Iterator[None]:
    started = time.monotonic()
    emit_progress(context, stage, "started")
    try:
        yield
    except BaseException as error:
        emit_progress(
            context,
            stage,
            "failed",
            message=f"{type(error).__name__}: {error}",
            elapsed_seconds=time.monotonic() - started,
        )
        raise
    emit_progress(
        context,
        stage,
        "finished",
        elapsed_seconds=time.monotonic() - started,
    )


def milestone_callback(
    context: dict[str, Any] | None,
    stage: str,
    phase: str,
) -> Callable[[int, int], None]:
    """Return a throttled volume callback that emits percentage milestones."""
    last_bucket = -1
    interval = int((context or {}).get("interval_percent", 10))

    def report(completed: int, total: int) -> None:
        nonlocal last_bucket
        percentage = int(completed * 100 / max(total, 1))
        bucket = percentage // interval
        if completed not in {1, total} and bucket <= last_bucket:
            return
        last_bucket = bucket
        emit_progress(
            context,
            stage,
            "progress",
            message=phase,
            completed=completed,
            total=total,
        )

    return report


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return ""
    rounded = max(int(round(seconds)), 0)
    minutes, remainder = divmod(rounded, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f" ({hours}h {minutes}m {remainder}s)"
    if minutes:
        return f" ({minutes}m {remainder}s)"
    return f" ({remainder}s)"


def format_progress_event(event: dict[str, Any]) -> str:
    timestamp = str(event.get("timestamp", ""))
    clock = timestamp[11:19] if len(timestamp) >= 19 else "--:--:--"
    label = event.get("run_label")
    if not label and event.get("run_index") and event.get("run_count"):
        label = f"Run {event['run_index']}/{event['run_count']}"
    prefix = f"[{clock}] " + (f"{label}: " if label else "")
    stage = str(event.get("stage", "stage"))
    status = str(event.get("status", ""))
    if status == "progress":
        completed = int(event.get("completed") or 0)
        total = int(event.get("total") or 0)
        percentage = int(completed * 100 / max(total, 1))
        phase = event.get("message") or stage
        return f"{prefix}{phase}: {completed}/{total} ({percentage}%)"
    if status == "failed":
        return f"{prefix}{stage} failed: {event.get('message') or 'unknown error'}"
    elapsed = _format_elapsed(event.get("elapsed_seconds"))
    message = f" - {event['message']}" if event.get("message") else ""
    return f"{prefix}{stage} {status}{elapsed}{message}"


class ProgressPrinter:
    """Tail a JSONL event file in a background thread and print new events."""

    def __init__(
        self,
        event_file: str | Path,
        *,
        enabled: bool = True,
        stream: TextIO | None = None,
    ) -> None:
        self.event_file = Path(event_file).expanduser().resolve()
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offset = 0

    def start(self) -> None:
        if not self.enabled:
            return
        self.event_file.parent.mkdir(parents=True, exist_ok=True)
        self.event_file.touch(exist_ok=True)
        self._offset = self.event_file.stat().st_size
        self._thread = threading.Thread(target=self._tail, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        try:
            with self.event_file.open() as stream:
                stream.seek(self._offset)
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    self._offset = stream.tell()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    print(format_progress_event(event), file=self.stream, flush=True)
        except FileNotFoundError:
            return

    def _tail(self) -> None:
        while not self._stop.wait(0.2):
            self._drain()
        self._drain()

    def stop(self) -> None:
        if not self.enabled or self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        self._thread = None

    def __enter__(self) -> "ProgressPrinter":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
