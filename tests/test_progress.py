import io
import json
from pathlib import Path

from cnapfmriprep.progress import (
    ProgressPrinter,
    emit_progress,
    format_progress_event,
    progress_context,
)


def test_progress_event_is_written_and_printed(tmp_path: Path) -> None:
    event_file = tmp_path / "progress.jsonl"
    context = progress_context(
        event_file,
        run_index=2,
        run_count=4,
        run_label="BIDS run 2",
    )
    output = io.StringIO()
    with ProgressPrinter(event_file, stream=output):
        emit_progress(context, "NORDIC", "started")
        emit_progress(context, "NORDIC", "finished", elapsed_seconds=3.2)

    rows = [json.loads(line) for line in event_file.read_text().splitlines()]
    assert [row["status"] for row in rows] == ["started", "finished"]
    assert "BIDS run 2: NORDIC started" in output.getvalue()
    assert "NORDIC finished (3s)" in output.getvalue()


def test_volume_progress_format() -> None:
    message = format_progress_event(
        {
            "timestamp": "2026-08-31T12:34:56-04:00",
            "run_label": "BIDS run 1",
            "stage": "motion correction and resampling",
            "status": "progress",
            "message": "final one-step resampling",
            "completed": 25,
            "total": 100,
        }
    )
    assert message == "[12:34:56] BIDS run 1: final one-step resampling: 25/100 (25%)"

