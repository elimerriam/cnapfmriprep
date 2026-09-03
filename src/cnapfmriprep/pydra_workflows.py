"""Pydra task graphs for one run or a shared multi-run session.

Pydra 0.25 is pinned for this runnable release. Scientific stages remain
ordinary Python functions, while this module only defines data dependencies.
"""

from pathlib import Path
from typing import Any

from pydra import Submitter, Workflow, mark

from .errors import WorkflowError
from .progress import emit_progress
from .progress import progress_context as make_progress_context


def _report_cache_hit(
    task: Any,
    *,
    progress_context: dict[str, Any] | None,
    stage: str,
) -> None:
    """Emit a durable cache-hit event before Pydra returns a saved result."""
    try:
        result = task.result()
    except Exception:
        return
    if (
        result is not None
        and not bool(getattr(result, "errored", False))
        and getattr(result, "output", None) is not None
    ):
        emit_progress(
            progress_context,
            stage,
            "cached",
            message=f"reused {task.checksum}",
        )


def _add_task(
    workflow: Workflow,
    task: Any,
    *,
    progress_context: dict[str, Any] | None,
    stage: str,
) -> None:
    """Add a task with a cache-use hook that also works in worker processes."""
    from functools import partial

    task.hooks.pre_run = partial(
        _report_cache_hit,
        progress_context=progress_context,
        stage=stage,
    )
    workflow.add(task)


def _new_workflow(*, name: str, cache_dir: str | Path) -> Workflow:
    """Create a Pydra 0.25 workflow with its required non-empty input spec."""
    return Workflow(
        name=name,
        cache_dir=str(Path(cache_dir).resolve()),
        input_spec=["cnapfmriprep_context"],
        cnapfmriprep_context=name,
    )


@mark.task
def nordic_task(
    bold_file: str,
    no_rf_file: str,
    output_dir: str,
    nordic_config: dict[str, Any],
    expected_noise_volumes: int,
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .nordic import run_nordic_stage
    from .progress import progress_stage

    with progress_stage(progress_context, "NORDIC"):
        return run_nordic_stage(
            bold_file,
            no_rf_file,
            output_dir,
            nordic_config,
            expected_noise_volumes,
            progress_context,
        )


@mark.task
def topup_task(
    fmap_files: list[str],
    fmap_jsons: list[str],
    output_dir: str,
    topup_config: dict[str, Any],
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .progress import progress_stage
    from .topup import run_topup_stage

    with progress_stage(progress_context, "TOPUP"):
        return run_topup_stage(fmap_files, fmap_jsons, output_dir, topup_config)


@mark.task
def field_task(
    topup_outputs: dict[str, Any],
    bold_file: str,
    bold_json: str,
    output_dir: str,
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .progress import progress_stage
    from .transforms import run_field_transform_stage

    with progress_stage(progress_context, "SDC warp preparation"):
        return run_field_transform_stage(
            topup_outputs,
            bold_file,
            bold_json,
            output_dir,
        )


@mark.task
def motion_task(
    nordic_outputs: dict[str, Any],
    field_outputs: dict[str, Any],
    output_dir: str,
    ants_config: dict[str, Any],
    resampling_config: dict[str, Any],
    execution_config: dict[str, Any],
    reference_motion_outputs: dict[str, Any] | None = None,
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .motion import run_motion_stage
    from .progress import progress_stage

    with progress_stage(progress_context, "motion correction and resampling"):
        return run_motion_stage(
            nordic_outputs["nordic_bold"],
            field_outputs["field_warp"],
            output_dir,
            ants_config,
            resampling_config,
            execution_config,
            reference_outputs=reference_motion_outputs,
            progress_context=progress_context,
        )


@mark.task
def qc_task(
    raw_bold: str,
    nordic_outputs: dict[str, Any],
    topup_outputs: dict[str, Any],
    field_outputs: dict[str, Any],
    motion_outputs: dict[str, Any],
    output_dir: str,
    progress_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .progress import progress_stage
    from .qc import run_qc_stage

    with progress_stage(progress_context, "QC"):
        return run_qc_stage(
            raw_bold=raw_bold,
            nordic_bold=nordic_outputs["nordic_bold"],
            corrected_bold=motion_outputs["corrected_bold"],
            preview_bold=motion_outputs["preview_bold"],
            field_hz=topup_outputs["field_hz"],
            displacement_mm=field_outputs["displacement_mm"],
            jacobian=field_outputs["jacobian"],
            topup_input=topup_outputs["topup_input"],
            topup_corrected=topup_outputs["topup_corrected"],
            motion_tsv=motion_outputs["motion_tsv"],
            displacement_tsv=motion_outputs["displacement_tsv"],
            no_rf_stats_json=nordic_outputs["no_rf_stats_json"],
            transform_order_json=motion_outputs["transform_order_json"],
            output_dir=output_dir,
        )


def build_run_workflow(
    *,
    name: str,
    cache_dir: str | Path,
    raw_bold: str,
    raw_bold_json: str,
    no_rf_file: str,
    fmap_files: list[str],
    fmap_jsons: list[str],
    run_work_dir: str | Path,
    resolved_config: dict[str, Any],
    progress_file: str | Path | None = None,
    progress_interval_percent: int = 10,
) -> Workflow:
    """Build the backward-compatible one-run graph."""
    run_work = Path(run_work_dir).resolve()
    workflow = _new_workflow(name=name, cache_dir=cache_dir)
    run_progress = make_progress_context(
        progress_file,
        run_index=1,
        run_count=1,
        run_label="Run 1/1",
        interval_percent=progress_interval_percent,
    )
    _add_task(
        workflow,
        nordic_task(
            name="nordic",
            bold_file=raw_bold,
            no_rf_file=no_rf_file,
            output_dir=str(run_work / "nordic"),
            nordic_config=resolved_config["nordic"],
            expected_noise_volumes=resolved_config["ingest"]["trailing_no_rf_volumes"],
            progress_context=run_progress,
        ),
        progress_context=run_progress,
        stage="NORDIC",
    )
    _add_task(
        workflow,
        topup_task(
            name="topup",
            fmap_files=fmap_files,
            fmap_jsons=fmap_jsons,
            output_dir=str(run_work / "topup"),
            topup_config=resolved_config["topup"],
            progress_context=run_progress,
        ),
        progress_context=run_progress,
        stage="TOPUP",
    )
    _add_task(
        workflow,
        field_task(
            name="field",
            topup_outputs=workflow.topup.lzout.out,
            bold_file=raw_bold,
            bold_json=raw_bold_json,
            output_dir=str(run_work / "field"),
            progress_context=run_progress,
        ),
        progress_context=run_progress,
        stage="SDC warp preparation",
    )
    _add_task(
        workflow,
        motion_task(
            name="motion",
            nordic_outputs=workflow.nordic.lzout.out,
            field_outputs=workflow.field.lzout.out,
            output_dir=str(run_work / "motion"),
            ants_config=resolved_config["ants"],
            resampling_config=resolved_config["resampling"],
            execution_config=resolved_config["execution"],
            reference_motion_outputs=None,
            progress_context=run_progress,
        ),
        progress_context=run_progress,
        stage="motion correction and resampling",
    )
    _add_task(
        workflow,
        qc_task(
            name="qc",
            raw_bold=raw_bold,
            nordic_outputs=workflow.nordic.lzout.out,
            topup_outputs=workflow.topup.lzout.out,
            field_outputs=workflow.field.lzout.out,
            motion_outputs=workflow.motion.lzout.out,
            output_dir=str(run_work / "qc"),
            progress_context=run_progress,
        ),
        progress_context=run_progress,
        stage="QC",
    )
    workflow.set_output(
        [
            ("nordic", workflow.nordic.lzout.out),
            ("topup", workflow.topup.lzout.out),
            ("field", workflow.field.lzout.out),
            ("motion", workflow.motion.lzout.out),
            ("qc", workflow.qc.lzout.out),
        ]
    )
    return workflow


def build_session_workflow(
    *,
    name: str,
    cache_dir: str | Path,
    runs: list[dict[str, Any]],
    session_work_dir: str | Path,
    resolved_config: dict[str, Any],
    reference_index: int,
    shared_topup: bool,
    shared_motion_reference: bool,
    progress_file: str | Path | None = None,
    progress_interval_percent: int = 10,
) -> tuple[Workflow, dict[str, Any]]:
    """Build one graph for several runs with optional shared TOPUP/reference.

    ``runs`` is already ordered by the caller. The reference run is added to
    the graph first so every non-reference motion task can depend on its output.
    """
    if not runs:
        raise ValueError("A session workflow requires at least one BOLD run")
    if reference_index < 0 or reference_index >= len(runs):
        raise ValueError(f"Invalid reference_index {reference_index} for {len(runs)} runs")

    root = Path(session_work_dir).resolve()
    workflow = _new_workflow(name=name, cache_dir=cache_dir)
    run_progress = {
        index: make_progress_context(
            progress_file,
            run_index=index + 1,
            run_count=len(runs),
            run_label=str(run.get("display_label") or f"Run {index + 1}/{len(runs)}"),
            interval_percent=progress_interval_percent,
        )
        for index, run in enumerate(runs)
    }
    shared_progress = make_progress_context(
        progress_file,
        run_count=len(runs),
        run_label="Shared session",
        interval_percent=progress_interval_percent,
    )
    topup_tasks: dict[int, Any] = {}
    if shared_topup:
        first = runs[0]
        _add_task(
            workflow,
            topup_task(
                name="topup_shared",
                fmap_files=list(first["fmap_files"]),
                fmap_jsons=list(first["fmap_jsons"]),
                output_dir=str(root / "shared_topup"),
                topup_config=resolved_config["topup"],
                progress_context=shared_progress,
            ),
            progress_context=shared_progress,
            stage="TOPUP",
        )
        for index in range(len(runs)):
            topup_tasks[index] = workflow.topup_shared
    else:
        for index, run in enumerate(runs):
            task_name = f"topup_{index:03d}"
            _add_task(
                workflow,
                topup_task(
                    name=task_name,
                    fmap_files=list(run["fmap_files"]),
                    fmap_jsons=list(run["fmap_jsons"]),
                    output_dir=str(root / f"run-{index:03d}" / "topup"),
                    topup_config=resolved_config["topup"],
                    progress_context=run_progress[index],
                ),
                progress_context=run_progress[index],
                stage="TOPUP",
            )
            topup_tasks[index] = getattr(workflow, task_name)

    nordic_tasks: dict[int, Any] = {}
    field_tasks: dict[int, Any] = {}
    for index, run in enumerate(runs):
        run_root = root / f"run-{index:03d}"
        nordic_name = f"nordic_{index:03d}"
        field_name = f"field_{index:03d}"
        _add_task(
            workflow,
            nordic_task(
                name=nordic_name,
                bold_file=str(run["raw_bold"]),
                no_rf_file=str(run["no_rf_file"]),
                output_dir=str(run_root / "nordic"),
                nordic_config=resolved_config["nordic"],
                expected_noise_volumes=resolved_config["ingest"][
                    "trailing_no_rf_volumes"
                ],
                progress_context=run_progress[index],
            ),
            progress_context=run_progress[index],
            stage="NORDIC",
        )
        nordic_tasks[index] = getattr(workflow, nordic_name)
        _add_task(
            workflow,
            field_task(
                name=field_name,
                topup_outputs=topup_tasks[index].lzout.out,
                bold_file=str(run["raw_bold"]),
                bold_json=str(run["raw_bold_json"]),
                output_dir=str(run_root / "field"),
                progress_context=run_progress[index],
            ),
            progress_context=run_progress[index],
            stage="SDC warp preparation",
        )
        field_tasks[index] = getattr(workflow, field_name)

    motion_tasks: dict[int, Any] = {}
    motion_order = [reference_index] + [
        index for index in range(len(runs)) if index != reference_index
    ]
    for index in motion_order:
        motion_name = f"motion_{index:03d}"
        reference_output = None
        if shared_motion_reference and index != reference_index:
            reference_output = motion_tasks[reference_index].lzout.out
        _add_task(
            workflow,
            motion_task(
                name=motion_name,
                nordic_outputs=nordic_tasks[index].lzout.out,
                field_outputs=field_tasks[index].lzout.out,
                output_dir=str(root / f"run-{index:03d}" / "motion"),
                ants_config=resolved_config["ants"],
                resampling_config=resolved_config["resampling"],
                execution_config=resolved_config["execution"],
                reference_motion_outputs=reference_output,
                progress_context=run_progress[index],
            ),
            progress_context=run_progress[index],
            stage="motion correction and resampling",
        )
        motion_tasks[index] = getattr(workflow, motion_name)

    qc_tasks: dict[int, Any] = {}
    for index, run in enumerate(runs):
        qc_name = f"qc_{index:03d}"
        _add_task(
            workflow,
            qc_task(
                name=qc_name,
                raw_bold=str(run["raw_bold"]),
                nordic_outputs=nordic_tasks[index].lzout.out,
                topup_outputs=topup_tasks[index].lzout.out,
                field_outputs=field_tasks[index].lzout.out,
                motion_outputs=motion_tasks[index].lzout.out,
                output_dir=str(root / f"run-{index:03d}" / "qc"),
                progress_context=run_progress[index],
            ),
            progress_context=run_progress[index],
            stage="QC",
        )
        qc_tasks[index] = getattr(workflow, qc_name)

    outputs: list[tuple[str, Any]] = []
    if shared_topup:
        outputs.append(("topup_shared", workflow.topup_shared.lzout.out))
    for index in range(len(runs)):
        prefix = f"run_{index:03d}"
        run_outputs = [
            (f"{prefix}_nordic", nordic_tasks[index].lzout.out),
            (f"{prefix}_field", field_tasks[index].lzout.out),
            (f"{prefix}_motion", motion_tasks[index].lzout.out),
            (f"{prefix}_qc", qc_tasks[index].lzout.out),
        ]
        if not shared_topup:
            run_outputs.insert(1, (f"{prefix}_topup", topup_tasks[index].lzout.out))
        outputs.extend(run_outputs)
    workflow.set_output(outputs)
    plan = {
        "run_count": len(runs),
        "reference_index": reference_index,
        "shared_topup": shared_topup,
        "shared_motion_reference": shared_motion_reference,
    }
    return workflow, plan


def _run_workflow(workflow: Workflow, *, plugin: str, n_procs: int) -> Any:
    if plugin == "serial":
        workflow(plugin="serial")
    else:
        with Submitter(plugin=plugin, n_procs=n_procs) as submitter:
            submitter(workflow)
    result = workflow.result()
    if result is None:
        raise WorkflowError(
            "Pydra completed without returning a workflow result. Inspect the work "
            "directory for task crash reports."
        )
    if isinstance(result, list):
        if len(result) != 1:
            raise RuntimeError(f"Unexpected mapped workflow result count: {len(result)}")
        result = result[0]
    if result is None or getattr(result, "output", None) is None:
        raise WorkflowError(
            "Pydra returned an empty workflow result. This commonly follows an interrupted "
            "task. Restart with the same work directory; CNAP fMRI Prep will quarantine invalid "
            "cache records and preserve completed tasks."
        )
    return result.output


def execute_workflow(
    workflow: Workflow,
    *,
    plugin: str,
    n_procs: int,
) -> dict[str, Any]:
    """Execute the backward-compatible one-run workflow."""
    output = _run_workflow(workflow, plugin=plugin, n_procs=n_procs)
    return {
        "nordic": output.nordic,
        "topup": output.topup,
        "field": output.field,
        "motion": output.motion,
        "qc": output.qc,
    }


def execute_session_workflow(
    workflow: Workflow,
    plan: dict[str, Any],
    *,
    plugin: str,
    n_procs: int,
) -> dict[str, Any]:
    """Execute a multi-run graph and restore a nested result structure."""
    output = _run_workflow(workflow, plugin=plugin, n_procs=n_procs)
    runs: list[dict[str, Any]] = []
    for index in range(int(plan["run_count"])):
        prefix = f"run_{index:03d}"
        topup_output = (
            output.topup_shared
            if bool(plan["shared_topup"])
            else getattr(output, f"{prefix}_topup")
        )
        runs.append(
            {
                "nordic": getattr(output, f"{prefix}_nordic"),
                "topup": topup_output,
                "field": getattr(output, f"{prefix}_field"),
                "motion": getattr(output, f"{prefix}_motion"),
                "qc": getattr(output, f"{prefix}_qc"),
            }
        )
    result: dict[str, Any] = {"runs": runs, "plan": dict(plan)}
    if bool(plan["shared_topup"]):
        result["shared_topup"] = output.topup_shared
    return result
