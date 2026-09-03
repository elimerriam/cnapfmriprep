from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cnapfmriprep.config import load_config
from cnapfmriprep.errors import ValidationError
from cnapfmriprep.job import (
    WorkDirectoryLease,
    begin_job_attempt,
    inspect_job_lock,
    update_job_manifest,
)
from cnapfmriprep.progress import emit_progress, progress_context
from cnapfmriprep.status import format_job_status, inspect_job_status


def test_work_directory_lease_blocks_active_writer_and_archives_stale_lock(
    tmp_path: Path,
) -> None:
    with WorkDirectoryLease(tmp_path):
        assert inspect_job_lock(tmp_path)["state"] == "active"
        with pytest.raises(ValidationError, match="already in use"):
            WorkDirectoryLease(tmp_path).acquire()
    assert inspect_job_lock(tmp_path)["state"] == "absent"

    (tmp_path / "job.lock").write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "hostname": "",
                "created_at": "2000-01-01T00:00:00+00:00",
                "token": "old",
            }
        )
    )
    with WorkDirectoryLease(tmp_path) as lease:
        assert lease.archived_stale_lock is not None
        assert Path(lease.archived_stale_lock).is_file()


def test_status_distinguishes_running_unpublished_and_waiting_runs(tmp_path: Path) -> None:
    progress_file = tmp_path / "progress.jsonl"
    invocation = {"work_dir": str(tmp_path)}
    with WorkDirectoryLease(tmp_path):
        begin_job_attempt(tmp_path, command="preprocess", invocation=invocation)
        update_job_manifest(
            tmp_path,
            runs=[
                {"index": 1, "label": "BIDS run 1", "raw_bold": "/tmp/run-01.nii.gz"},
                {"index": 2, "label": "BIDS run 2", "raw_bold": "/tmp/run-02.nii.gz"},
                {"index": 3, "label": "BIDS run 3", "raw_bold": "/tmp/run-03.nii.gz"},
            ],
        )
        shared = progress_context(progress_file, run_label="Shared session", run_count=3)
        run1 = progress_context(
            progress_file, run_index=1, run_count=3, run_label="BIDS run 1"
        )
        run2 = progress_context(
            progress_file, run_index=2, run_count=3, run_label="BIDS run 2"
        )
        emit_progress(shared, "preprocessing", "started")
        emit_progress(shared, "TOPUP", "finished", elapsed_seconds=120)
        emit_progress(run1, "NORDIC", "cached", message="reused FunctionTask_one")
        emit_progress(run1, "motion correction and resampling", "started")
        emit_progress(
            run1,
            "motion correction and resampling",
            "progress",
            message="final one-step resampling",
            completed=5,
            total=10,
        )
        emit_progress(run2, "QC", "finished", elapsed_seconds=10)

        report = inspect_job_status(tmp_path)

        assert report["state"] == "running"
        assert report["shared_topup"]["state"] == "completed"
        assert [run["state"] for run in report["runs"]] == [
            "running",
            "unpublished",
            "waiting",
        ]
        assert report["runs"][0]["progress"] == {"completed": 5, "total": 10}
        assert len(report["cache_usage"]["reused"]) == 1
        rendered = format_job_status(report)
        assert "BIDS run 1" in rendered
        assert "unpublished" in rendered

    assert inspect_job_status(tmp_path)["state"] == "interrupted"


def test_resume_replays_saved_preprocess_invocation(tmp_path: Path, monkeypatch) -> None:
    from cnapfmriprep import preprocess

    config = load_config(Path(__file__).parents[1] / "config" / "example_study.yaml")
    invocation = {
        "bids_dir": "/tmp/bids",
        "derivatives_dir": "/tmp/derivatives",
        "config": config.model_dump(mode="json"),
        "subject": "001",
        "session": "01",
        "task": None,
        "run": None,
        "work_dir": str(tmp_path),
    }
    begin_job_attempt(tmp_path, command="preprocess", invocation=invocation)
    captured: dict = {}

    def fake_preprocess(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"resumed": True}

    monkeypatch.setattr(preprocess, "preprocess_dataset", fake_preprocess)
    assert preprocess.resume_preprocessing(tmp_path) == {"resumed": True}
    assert captured["args"] == ("/tmp/bids", "/tmp/derivatives")
    assert captured["kwargs"]["subject"] == "001"
    assert captured["kwargs"]["session"] == "01"
    assert captured["kwargs"]["work_dir"] == tmp_path


def test_interruption_leaves_resumable_manifest_and_releases_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cnapfmriprep import preprocess

    config = load_config(Path(__file__).parents[1] / "config" / "example_study.yaml")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(preprocess, "_preprocess_dataset", interrupt)
    with pytest.raises(KeyboardInterrupt):
        preprocess.preprocess_dataset(
            tmp_path / "bids",
            tmp_path / "derivatives",
            config=config,
            subject="001",
            session="01",
            work_dir=tmp_path / "work",
        )

    manifest = json.loads((tmp_path / "work" / "job.json").read_text())
    assert manifest["state"] == "interrupted"
    assert manifest["attempts"][-1]["state"] == "interrupted"
    assert not (tmp_path / "work" / "job.lock").exists()


def test_status_estimates_from_completed_stage_history(tmp_path: Path) -> None:
    progress_file = tmp_path / "progress.jsonl"
    now = datetime.now().astimezone()
    rows = [
        {
            "timestamp": (now - timedelta(minutes=5)).isoformat(timespec="seconds"),
            "stage": "preprocessing",
            "status": "started",
            "run_index": None,
            "run_count": 1,
            "run_label": "Session",
            "message": None,
            "completed": None,
            "total": None,
            "elapsed_seconds": None,
            "pid": os.getpid(),
        },
        {
            "timestamp": (now - timedelta(minutes=4)).isoformat(timespec="seconds"),
            "stage": "SDC warp preparation",
            "status": "finished",
            "run_index": 1,
            "run_count": 1,
            "run_label": "BIDS run 1",
            "message": None,
            "completed": None,
            "total": None,
            "elapsed_seconds": 60,
            "pid": os.getpid(),
        },
        {
            "timestamp": (now - timedelta(minutes=3)).isoformat(timespec="seconds"),
            "stage": "preprocessing",
            "status": "started",
            "run_index": None,
            "run_count": 1,
            "run_label": "Session",
            "message": None,
            "completed": None,
            "total": None,
            "elapsed_seconds": None,
            "pid": os.getpid(),
        },
        {
            "timestamp": (now - timedelta(minutes=2)).isoformat(timespec="seconds"),
            "stage": "NORDIC",
            "status": "finished",
            "run_index": 1,
            "run_count": 1,
            "run_label": "BIDS run 1",
            "message": None,
            "completed": None,
            "total": None,
            "elapsed_seconds": 600,
            "pid": os.getpid(),
        },
    ]
    progress_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = inspect_job_status(tmp_path)
    sdc = report["runs"][0]["stages"][2]
    assert sdc["state"] == "waiting"
    assert sdc["eta_seconds"] == 60
