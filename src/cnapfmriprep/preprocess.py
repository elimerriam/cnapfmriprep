"""Session-level preprocessing with shared TOPUP and motion reference support."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import __version__
from .bids import bold_run_sort_key, discover_bold_runs, semantic_validate
from .cache import recover_interrupted_pydra_cache
from .config import StudyConfig
from .derivatives import publish_run_derivatives
from .errors import ValidationError
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
    """Preprocess selected runs in one Pydra graph.

    By default, TOPUP is estimated once from the shared AP/PA pair. A robust
    undistorted reference is built from the configured reference run (the first
    BIDS-ordered run by default), and every other volume is rigidly registered
    to that same reference before one combined SDC/HMC interpolation.
    """
    bids_root = Path(bids_dir).resolve()
    derivatives_root = ensure_dir(derivatives_dir)
    work_root = ensure_dir(work_dir)
    cache_recovery = recover_interrupted_pydra_cache(work_root / "pydra-cache")
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

    session_label = f"sub_{subject}" + (f"_ses_{session}" if session else "")
    progress_file = work_root / "progress.jsonl"
    workflow, plan = build_session_workflow(
        name=f"cnapfmriprep_{session_label}",
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
                f"{config.execution.resolved_profile}"
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
        except BaseException as error:
            emit_progress(
                session_progress,
                "preprocessing",
                "failed",
                message=f"{type(error).__name__}: {error}",
            )
            raise
        emit_progress(session_progress, "workflow graph", "finished")

    target_paths = {str(Path(record["bold"]).resolve()) for record in targets}
    results: list[dict[str, Any]] = []
    for index, (record, run_graph) in enumerate(
        zip(workflow_runs, graph_result["runs"], strict=True)
    ):
        raw_bold = Path(record["bold"]).resolve()
        if str(raw_bold) not in target_paths:
            continue
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
        "runs": results,
    }
    if "shared_topup" in graph_result:
        output["shared_topup_outputs"] = graph_result["shared_topup"]
    write_json(work_root / "preprocess_result.json", output)
    with ProgressPrinter(progress_file, enabled=config.execution.show_progress):
        emit_progress(session_progress, "preprocessing", "finished")
    return output
