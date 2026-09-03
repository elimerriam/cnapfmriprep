"""Session-level preprocessing with shared TOPUP and motion reference support."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from . import __version__
from .bids import bold_run_sort_key, discover_bold_runs, semantic_validate
from .cache import recover_interrupted_pydra_cache
from .config import StudyConfig
from .derivatives import publish_run_derivatives
from .errors import ValidationError
from .job import (
    WorkDirectoryLease,
    begin_job_attempt,
    finish_job_attempt,
    graceful_shutdown_signals,
    read_job_manifest,
    update_job_manifest,
)
from .progress import ProgressPrinter, emit_progress, progress_context
from .pydra_workflows import build_session_workflow, execute_session_workflow
from .utils import assert_same_nifti_grid, ensure_dir, write_json


def _run_label(path: Path) -> str:
    name = path.name.replace(".nii.gz", "").replace(".nii", "")
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _run_number(record: dict[str, Any]) -> int:
    try:
        return int(record.get("entities", {}).get("run", "0"))
    except (TypeError, ValueError):
        return 0


def _select_reference_run(
    runs: list[dict[str, Any]],
    *,
    reference_task: str | None,
    reference_run: int | None,
) -> dict[str, Any]:
    candidates = runs
    if reference_task is not None:
        candidates = [
            record
            for record in candidates
            if record.get("entities", {}).get("task") == reference_task
        ]
    if reference_run is not None:
        candidates = [record for record in candidates if _run_number(record) == reference_run]
    if not candidates:
        raise ValidationError(
            "No BOLD run matched multi_run.reference_task/reference_run: "
            f"task={reference_task!r}, run={reference_run!r}"
        )
    if len(candidates) > 1 and reference_run is not None and reference_task is None:
        labels = [str(record["bold"]) for record in candidates]
        raise ValidationError(
            "multi_run.reference_run is ambiguous across tasks. Set "
            f"multi_run.reference_task as well. Candidates: {labels}"
        )
    return sorted(candidates, key=bold_run_sort_key)[0]


def _is_target(record: dict[str, Any], task: str | None, run: int | None) -> bool:
    entities = record.get("entities", {})
    if task is not None and entities.get("task") != task:
        return False
    if run is not None and _run_number(record) != run:
        return False
    return True


def _attempt_cache_usage(progress_file: Path) -> dict[str, list[dict[str, str | None]]]:
    """Summarize cache hits and recomputed task stages in the latest attempt."""
    if not progress_file.is_file():
        return {"reused": [], "recomputed": []}
    events: list[dict[str, Any]] = []
    for line in progress_file.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    boundary = 0
    for index, event in enumerate(events):
        if (
            event.get("run_index") is None
            and event.get("stage") == "preprocessing"
            and event.get("status") == "started"
        ):
            boundary = index
    task_stages = {"NORDIC", "TOPUP", "SDC warp preparation", "QC", "motion correction and resampling"}
    reused: list[dict[str, str | None]] = []
    recomputed: list[dict[str, str | None]] = []
    for event in events[boundary:]:
        stage = str(event.get("stage"))
        if stage not in task_stages:
            continue
        record = {
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


def _fieldmap_signature(record: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(sorted(str(Path(path).resolve()) for path in record["fieldmaps"])),
        tuple(sorted(str(Path(path).resolve()) for path in record["fieldmap_jsons"])),
    )


def _validate_shared_inputs(
    runs: list[dict[str, Any]],
    *,
    shared_topup: bool,
    shared_motion_reference: bool,
    reference_run: dict[str, Any],
) -> None:
    if shared_topup:
        expected = _fieldmap_signature(reference_run)
        mismatches = [
            str(record["bold"])
            for record in runs
            if _fieldmap_signature(record) != expected
        ]
        if mismatches:
            raise ValidationError(
                "multi_run.shared_topup is true, but not every selected BOLD run resolves "
                "to the same reverse-PE files. Mismatching runs: " + ", ".join(mismatches)
            )

    if shared_motion_reference:
        reference_bold = Path(reference_run["bold"])
        for record in runs:
            assert_same_nifti_grid(
                reference_bold,
                Path(record["bold"]),
                context="BOLD runs sharing one session motion reference",
            )


def preprocess_dataset(
    bids_dir: str | Path,
    derivatives_dir: str | Path,
    *,
    config: StudyConfig,
    subject: str,
    session: str | None,
    work_dir: str | Path,
    task: str | None = None,
    run: int | None = None,
) -> dict[str, Any]:
    """Run preprocessing under a durable, restart-safe work-directory lease."""
    work_root = ensure_dir(work_dir)
    invocation = {
        "bids_dir": str(Path(bids_dir).expanduser().resolve()),
        "derivatives_dir": str(Path(derivatives_dir).expanduser().resolve()),
        "config": config.model_dump(mode="json"),
        "subject": subject,
        "session": session,
        "task": task,
        "run": run,
        "work_dir": str(work_root),
        "pipeline_version": __version__,
    }
    with WorkDirectoryLease(work_root) as lease:
        manifest = begin_job_attempt(
            work_root,
            command="preprocess",
            invocation=invocation,
        )
        job_progress = progress_context(work_root / "progress.jsonl", run_label="Session")
        emit_progress(
            job_progress,
            "preprocessing",
            "started",
            message=f"attempt {manifest['attempt']}: validating inputs and building workflow",
        )
        if lease.archived_stale_lock:
            update_job_manifest(work_root, archived_stale_lock=lease.archived_stale_lock)
        try:
            with graceful_shutdown_signals():
                result = _preprocess_dataset(
                    bids_dir,
                    derivatives_dir,
                    config=config,
                    subject=subject,
                    session=session,
                    work_dir=work_root,
                    task=task,
                    run=run,
                    attempt=int(manifest["attempt"]),
                )
        except (KeyboardInterrupt, SystemExit) as error:
            finish_job_attempt(work_root, "interrupted", error=type(error).__name__)
            emit_progress(
                job_progress,
                "preprocessing",
                "interrupted",
                message=f"{type(error).__name__}; safe to resume",
            )
            raise
        except BaseException as error:
            finish_job_attempt(
                work_root,
                "failed",
                error=f"{type(error).__name__}: {error}",
            )
            emit_progress(
                job_progress,
                "preprocessing",
                "failed",
                message=f"{type(error).__name__}: {error}",
            )
            raise
        finish_job_attempt(work_root, "completed")
        return result


def resume_preprocessing(work_dir: str | Path) -> dict[str, Any]:
    """Replay a recorded preprocessing invocation and reuse valid cached tasks."""
    root = Path(work_dir).expanduser().resolve()
    if not (root / "job.json").is_file() and (root / "preprocess" / "job.json").is_file():
        root = root / "preprocess"
    manifest = read_job_manifest(root)
    if manifest is None:
        raise ValidationError(
            f"No resumable job manifest was found in {root}. Restart older jobs with "
            "their original preprocess command."
        )
    if manifest.get("command") != "preprocess":
        raise ValidationError(f"Unsupported resumable command: {manifest.get('command')!r}")
    invocation = manifest.get("invocation")
    if not isinstance(invocation, dict):
        raise ValidationError(f"Job manifest has no valid invocation: {root / 'job.json'}")
    try:
        config = StudyConfig.model_validate(invocation["config"])
        return preprocess_dataset(
            invocation["bids_dir"],
            invocation["derivatives_dir"],
            config=config,
            subject=str(invocation["subject"]),
            session=invocation.get("session"),
            work_dir=root,
            task=invocation.get("task"),
            run=invocation.get("run"),
        )
    except KeyError as error:
        raise ValidationError(f"Job manifest is missing invocation field {error}") from error


def _preprocess_dataset(
    bids_dir: str | Path,
    derivatives_dir: str | Path,
    *,
    config: StudyConfig,
    subject: str,
    session: str | None,
    work_dir: str | Path,
    task: str | None = None,
    run: int | None = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """Preprocess selected runs in one Pydra graph.

    By default, TOPUP is estimated once from the shared AP/PA pair. A robust
    undistorted reference is built from the configured reference run (the first
    BIDS-ordered run by default), and every other volume is rigidly registered
    to that same reference before one combined SDC/HMC interpolation.
    """
    bids_root = Path(bids_dir).resolve()
    derivatives_root = ensure_dir(derivatives_dir)
    work_root = Path(work_dir).expanduser().resolve()
    cache_recovery = recover_interrupted_pydra_cache(
        work_root / "pydra-cache",
        current_job_pid=os.getpid(),
    )
    semantic_validate(
        bids_root,
        subject=subject,
        session=session,
        expected_no_rf_volumes=config.ingest.trailing_no_rf_volumes,
    )

    all_runs = discover_bold_runs(
        bids_root,
        subject=subject,
        session=session,
    )
    targets = [record for record in all_runs if _is_target(record, task, run)]
    if not targets:
        raise ValidationError(
            f"No matching BOLD runs found for task={task!r}, run={run!r}"
        )

    if config.multi_run.shared_motion_reference:
        if (
            config.multi_run.reference_task is None
            and config.multi_run.reference_run is None
        ):
            # With no explicit selector, use the first *selected* run rather
            # than an unrelated task that happens to sort first in the session.
            reference_run = sorted(targets, key=bold_run_sort_key)[0]
        else:
            reference_run = _select_reference_run(
                all_runs,
                reference_task=config.multi_run.reference_task,
                reference_run=config.multi_run.reference_run,
            )
    else:
        reference_run = sorted(targets, key=bold_run_sort_key)[0]
    needed_paths = {str(Path(record["bold"]).resolve()) for record in targets}
    if config.multi_run.shared_motion_reference:
        needed_paths.add(str(Path(reference_run["bold"]).resolve()))
    workflow_runs = [
        record
        for record in all_runs
        if str(Path(record["bold"]).resolve()) in needed_paths
    ]
    reference_path = str(Path(reference_run["bold"]).resolve())
    reference_index = next(
        index
        for index, record in enumerate(workflow_runs)
        if str(Path(record["bold"]).resolve()) == reference_path
    )
    target_paths = {str(Path(record["bold"]).resolve()) for record in targets}

    _validate_shared_inputs(
        workflow_runs,
        shared_topup=config.multi_run.shared_topup,
        shared_motion_reference=config.multi_run.shared_motion_reference,
        reference_run=reference_run,
    )

    resolved = config.model_dump(mode="json")
    graph_inputs: list[dict[str, Any]] = []
    for record in workflow_runs:
        raw_bold = Path(record["bold"]).resolve()
        bids_run = _run_number(record)
        graph_inputs.append(
            {
                "raw_bold": str(raw_bold),
                "raw_bold_json": str(record["bold_json"]),
                "no_rf_file": str(record["noise"]),
                "fmap_files": [str(path) for path in record["fieldmaps"]],
                "fmap_jsons": [str(path) for path in record["fieldmap_jsons"]],
                "display_label": f"BIDS run {bids_run}" if bids_run else raw_bold.name,
            }
        )

    update_job_manifest(
        work_root,
        runs=[
            {
                "index": index + 1,
                "label": run_input["display_label"],
                "raw_bold": run_input["raw_bold"],
                "publish": run_input["raw_bold"] in target_paths,
            }
            for index, run_input in enumerate(graph_inputs)
        ],
    )

    session_label = f"sub_{subject}" + (f"_ses_{session}" if session else "")
    progress_file = work_root / "progress.jsonl"
    workflow, plan = build_session_workflow(
        name=f"cnapfmriprep_{session_label}_attempt_{attempt:03d}",
        cache_dir=work_root / "pydra-cache",
        runs=graph_inputs,
        session_work_dir=work_root / "session",
        resolved_config=resolved,
        reference_index=reference_index,
        shared_topup=config.multi_run.shared_topup,
        shared_motion_reference=config.multi_run.shared_motion_reference,
        progress_file=progress_file,
        progress_interval_percent=config.execution.progress_interval_percent,
    )
    session_progress = progress_context(
        progress_file,
        run_count=len(workflow_runs),
        run_label="Session",
        interval_percent=config.execution.progress_interval_percent,
    )
    with ProgressPrinter(progress_file, enabled=config.execution.show_progress):
        emit_progress(
            session_progress,
            "preprocessing",
            "started",
            message=(
                f"{len(workflow_runs)} run(s), execution profile "
                f"{config.execution.resolved_profile}, attempt {attempt}"
            ),
        )
        if cache_recovery["recovered"]:
            emit_progress(
                session_progress,
                "cache recovery",
                "finished",
                message=f"quarantined {len(cache_recovery['recovered'])} invalid entries",
            )
        try:
            graph_result = execute_session_workflow(
                workflow,
                plan,
                plugin=config.execution.pydra_plugin,
                n_procs=config.execution.n_procs,
            )
        except (KeyboardInterrupt, SystemExit) as error:
            emit_progress(
                session_progress,
                "preprocessing",
                "interrupted",
                message=f"{type(error).__name__}; restart or use the resume command",
            )
            raise
        except BaseException as error:
            emit_progress(
                session_progress,
                "preprocessing",
                "failed",
                message=f"{type(error).__name__}: {error}",
            )
            raise
        emit_progress(session_progress, "workflow graph", "finished")

    results: list[dict[str, Any]] = []
    for index, (record, run_graph) in enumerate(
        zip(workflow_runs, graph_result["runs"], strict=True)
    ):
        raw_bold = Path(record["bold"]).resolve()
        if str(raw_bold) not in target_paths:
            continue
        publish_context = progress_context(
            progress_file,
            run_index=index + 1,
            run_count=len(workflow_runs),
            run_label=graph_inputs[index]["display_label"],
            interval_percent=config.execution.progress_interval_percent,
        )
        emit_progress(publish_context, "publishing", "started")
        try:
            published = publish_run_derivatives(
                raw_bold=str(raw_bold),
                nordic_outputs=run_graph["nordic"],
                topup_outputs=run_graph["topup"],
                field_outputs=run_graph["field"],
                motion_outputs=run_graph["motion"],
                qc_outputs=run_graph["qc"],
                derivatives_root=str(derivatives_root),
                resolved_config=resolved,
                pipeline_version=__version__,
            )
        except BaseException as error:
            emit_progress(
                publish_context,
                "publishing",
                "failed",
                message=f"{type(error).__name__}: {error}",
            )
            raise
        emit_progress(publish_context, "publishing", "finished")
        run_result = {
            "raw_bold": str(raw_bold),
            "workflow_index": index,
            "is_reference_run": index == reference_index,
            "workflow": run_graph,
            "derivatives": published,
        }
        run_work = ensure_dir(work_root / "runs" / _run_label(raw_bold))
        write_json(run_work / "run_result.json", run_result)
        results.append(run_result)

    reference_motion = graph_result["runs"][reference_index]["motion"]
    cache_usage = _attempt_cache_usage(progress_file)
    emit_progress(
        session_progress,
        "cache reuse",
        "finished",
        message=(
            f"{len(cache_usage['reused'])} task(s) reused; "
            f"{len(cache_usage['recomputed'])} task(s) recomputed"
        ),
    )
    output = {
        "bids_dir": str(bids_root),
        "derivatives_dir": str(derivatives_root),
        "subject": subject,
        "session": session,
        "reference_run": reference_path,
        "shared_reference": (
            reference_motion["bold_reference"]
            if config.multi_run.shared_motion_reference
            else None
        ),
        "shared_topup": config.multi_run.shared_topup,
        "shared_motion_reference": config.multi_run.shared_motion_reference,
        "workflow_run_count": len(workflow_runs),
        "published_run_count": len(results),
        "cache_recovery": cache_recovery,
        "cache_usage": cache_usage,
        "runs": results,
    }
    if "shared_topup" in graph_result:
        output["shared_topup_outputs"] = graph_result["shared_topup"]
    write_json(work_root / "preprocess_result.json", output)
    with ProgressPrinter(progress_file, enabled=config.execution.show_progress):
        emit_progress(session_progress, "preprocessing", "finished")
    return output
