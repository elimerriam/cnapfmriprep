"""Command-line interface.

The CLI deliberately keeps scientific and workflow-engine imports lazy. This
allows lightweight commands such as ``version``, ``doctor``, and ``inventory``
to start even when an optional preprocessing dependency is broken or has the
wrong version.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .errors import CnapFmriPrepError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="High-resolution 7T fMRI ingestion, NORDIC, TOPUP, and ANTs motion correction.",
)


def _emit(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


def _run_guarded(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except CnapFmriPrepError as error:
        typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=2) from error


def _load_attr(module_name: str, attribute: str):
    """Import one internal attribute and turn dependency failures into useful errors."""
    qualified = f"{__package__}.{module_name}"
    try:
        module = importlib.import_module(f".{module_name}", package=__package__)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        if os.environ.get("CNAPFMRIPREP_DEBUG_IMPORTS") == "1":
            raise
        typer.echo(
            "ERROR: CNAP fMRI Prep could not load "
            f"{qualified}.{attribute}: {type(error).__name__}: {error}\n"
            "Run 'cnapfmriprep doctor' for interpreter and dependency details. "
            "Set CNAPFMRIPREP_DEBUG_IMPORTS=1 to show the full traceback.",
            err=True,
        )
        raise typer.Exit(code=2) from error


@app.command("version")
def version_command() -> None:
    """Print the cnapfmriprep version."""
    typer.echo(__version__)


@app.command("doctor")
def doctor_command(
    config_file: Annotated[
        Path | None, typer.Option("--config", exists=True, readable=True)
    ] = None,
    check_matlab_license: Annotated[
        bool, typer.Option("--check-matlab-license")
    ] = False,
    fix_shell: Annotated[str | None, typer.Option("--fix-shell")] = None,
    fsldir: Annotated[Path | None, typer.Option("--fsldir")] = None,
) -> None:
    """Diagnose portability issues or print reviewed shell setup commands."""
    collect_diagnostics = _load_attr("diagnostics", "collect_diagnostics")
    render_shell_setup = _load_attr("diagnostics", "render_shell_setup")
    if fix_shell:
        inferred_fsldir = fsldir or (
            Path(os.environ["FSLDIR"]) if os.environ.get("FSLDIR") else None
        )
        try:
            typer.echo(
                render_shell_setup(
                    fix_shell,
                    fsldir=inferred_fsldir,
                    conda_prefix=os.environ.get("CONDA_PREFIX"),
                )
            )
        except ValueError as error:
            typer.echo(f"ERROR: {error}", err=True)
            raise typer.Exit(code=2) from error
        return
    report = collect_diagnostics(
        config_file=config_file,
        check_matlab_license=check_matlab_license,
    )
    _emit(report)
    if not report["ok"]:
        raise typer.Exit(code=1)


@app.command("check-deps")
def check_dependencies(
    config_file: Annotated[
        Path, typer.Option("--config", exists=True, readable=True)
    ],
) -> None:
    """Check external programs and the configured NORDIC checkout."""
    load_config = _load_attr("config", "load_config")
    config = _run_guarded(load_config, config_file)
    commands = [
        "dcm2niix",
        "topup",
        "fslmerge",
        "fslsplit",
        "fslroi",
        "fslmaths",
        "antsRegistration",
        "antsApplyTransforms",
        config.nordic.matlab_command,
    ]
    status = {command: shutil.which(command) for command in commands}
    status["FSLDIR"] = os.environ.get("FSLDIR")
    status["NIFTI_NORDIC.m"] = str(config.nordic.checkout / "NIFTI_NORDIC.m")
    status["NIFTI_NORDIC_exists"] = (config.nordic.checkout / "NIFTI_NORDIC.m").is_file()
    status["bids_validator"] = shutil.which("bids-validator-deno") or shutil.which(
        "bids-validator"
    )
    _emit(status)
    missing = [
        key
        for key, value in status.items()
        if key not in {"FSLDIR", "NIFTI_NORDIC.m"} and not value
    ]
    if not status["FSLDIR"]:
        missing.append("FSLDIR")
    if not status["NIFTI_NORDIC_exists"]:
        missing.append("NIFTI_NORDIC.m")
    if missing:
        raise typer.Exit(code=1)


@app.command("profiles")
def profiles_command() -> None:
    """Show execution profiles and the profile selected by auto detection."""
    choose_auto_profile = _load_attr("execution", "choose_auto_profile")
    execution_profiles = _load_attr("execution", "execution_profiles")
    physical_memory_gb = _load_attr("execution", "physical_memory_gb")
    _emit(
        {
            "profiles": execution_profiles(),
            "auto_selected": choose_auto_profile(),
            "physical_memory_gb": physical_memory_gb(),
            "logical_cpus": os.cpu_count(),
        }
    )


@app.command("recover-cache")
def recover_cache_command(
    work_dir: Annotated[
        Path, typer.Option("--work-dir", exists=True, file_okay=False)
    ],
) -> None:
    """Quarantine interrupted empty Pydra results while preserving valid tasks."""
    recover = _load_attr("cache", "recover_interrupted_pydra_cache")
    result = _run_guarded(recover, work_dir / "pydra-cache")
    _emit(result)


@app.command("status")
def status_command(
    work_dir: Annotated[
        Path, typer.Option("--work-dir", exists=True, file_okay=False)
    ],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show live, read-only session and per-run job status."""
    format_job_status = _load_attr("status", "format_job_status")
    inspect_job_status = _load_attr("status", "inspect_job_status")
    report = _run_guarded(inspect_job_status, work_dir)
    if json_output:
        _emit(report)
    else:
        typer.echo(format_job_status(report))


@app.command("resume")
def resume_command(
    work_dir: Annotated[
        Path, typer.Option("--work-dir", exists=True, file_okay=False)
    ],
) -> None:
    """Resume the recorded preprocessing invocation using valid cached results."""
    resume_preprocessing = _load_attr("preprocess", "resume_preprocessing")
    result = _run_guarded(resume_preprocessing, work_dir)
    _emit(result)


@app.command("inventory")
def inventory_command(
    archive: Annotated[Path, typer.Argument(exists=True, readable=True)],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    config_file: Annotated[
        Path | None, typer.Option("--config", exists=True, readable=True)
    ] = None,
) -> None:
    """Safely extract and inventory an XNAT ZIP or tar archive; optionally test series rules."""
    load_config = _load_attr("config", "load_config")
    inventory_archive = _load_attr("ingest", "inventory_archive")
    config = _run_guarded(load_config, config_file) if config_file else None
    result = _run_guarded(inventory_archive, archive, output_dir, config=config)
    _emit(result)


@app.command("setup")
def setup_command(
    template_file: Annotated[
        Path, typer.Option("--template", exists=True, readable=True)
    ],
    output_file: Annotated[Path, typer.Option("--output")],
    archive: Annotated[Path | None, typer.Argument()] = None,
    inventory_tsv: Annotated[Path | None, typer.Option("--inventory")] = None,
    work_dir: Annotated[Path, typer.Option("--work-dir")] = Path("work/setup"),
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
) -> None:
    """Open a local browser assistant that generates a session study YAML."""
    if (archive is None) == (inventory_tsv is None):
        typer.echo("ERROR: Provide either an archive argument or --inventory, but not both.", err=True)
        raise typer.Exit(code=2)
    if inventory_tsv is not None:
        inventory_path = inventory_tsv.expanduser().resolve()
        if not inventory_path.is_file():
            typer.echo(f"ERROR: Inventory does not exist: {inventory_path}", err=True)
            raise typer.Exit(code=2)
    else:
        archive_path = archive.expanduser().resolve() if archive else None
        if archive_path is None or not archive_path.is_file():
            typer.echo(f"ERROR: Archive does not exist: {archive_path}", err=True)
            raise typer.Exit(code=2)
        inventory_archive = _load_attr("ingest", "inventory_archive")
        inventory_result = _run_guarded(
            inventory_archive,
            archive_path,
            work_dir,
            config=None,
        )
        inventory_path = Path(inventory_result["inventory_tsv"])
    load_inventory_tsv = _load_attr("setup_assistant", "load_inventory_tsv")
    run_setup_server = _load_attr("setup_assistant", "run_setup_server")
    rows = _run_guarded(load_inventory_tsv, inventory_path)
    result = _run_guarded(
        run_setup_server,
        rows,
        template_file=template_file,
        output_file=output_file,
        overwrite=overwrite,
        open_browser=not no_browser,
    )
    _emit(result)


@app.command("ingest")
def ingest_command(
    archive: Annotated[Path, typer.Argument(exists=True, readable=True)],
    bids_dir: Annotated[Path, typer.Argument()],
    config_file: Annotated[
        Path, typer.Option("--config", exists=True, readable=True)
    ],
    subject: Annotated[str, typer.Option("--subject")],
    work_dir: Annotated[Path, typer.Option("--work-dir")],
    session: Annotated[str | None, typer.Option("--session")] = None,
    skip_official_validator: Annotated[
        bool, typer.Option("--skip-official-validator")
    ] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Convert one archive into staged BIDS, validate it, then publish it."""
    load_config = _load_attr("config", "load_config")
    ingest_archive = _load_attr("ingest", "ingest_archive")
    config = _run_guarded(load_config, config_file)
    result = _run_guarded(
        ingest_archive,
        archive,
        bids_dir,
        config=config,
        subject=subject,
        session=session,
        work_dir=work_dir,
        run_validator=not skip_official_validator,
        overwrite=overwrite,
    )
    _emit(result)


@app.command("validate")
def validate_command(
    bids_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    subject: Annotated[str | None, typer.Option("--subject")] = None,
    session: Annotated[str | None, typer.Option("--session")] = None,
    expected_no_rf_volumes: Annotated[
        int, typer.Option("--expected-no-rf-volumes")
    ] = 2,
    skip_official_validator: Annotated[
        bool, typer.Option("--skip-official-validator")
    ] = False,
) -> None:
    """Run the official validator and cnapfmriprep semantic checks."""
    run_official_validator = _load_attr("bids", "run_official_validator")
    semantic_validate = _load_attr("bids", "semantic_validate")
    official = _run_guarded(
        run_official_validator,
        bids_dir,
        output_json=Path.cwd() / "bids-validator.json",
        required=not skip_official_validator,
    )
    semantic = _run_guarded(
        semantic_validate,
        bids_dir,
        subject=subject,
        session=session,
        expected_no_rf_volumes=expected_no_rf_volumes,
    )
    _emit({"official_validator": str(official) if official else None, "semantic": semantic})


@app.command("preprocess")
def preprocess_command(
    bids_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    derivatives_dir: Annotated[Path, typer.Argument()],
    config_file: Annotated[
        Path, typer.Option("--config", exists=True, readable=True)
    ],
    subject: Annotated[str, typer.Option("--subject")],
    work_dir: Annotated[Path, typer.Option("--work-dir")],
    session: Annotated[str | None, typer.Option("--session")] = None,
    task: Annotated[str | None, typer.Option("--task")] = None,
    run: Annotated[int | None, typer.Option("--run", min=1)] = None,
    execution_profile: Annotated[
        str | None, typer.Option("--execution-profile")
    ] = None,
) -> None:
    """Run per-run NORDIC, shared TOPUP/reference motion, resampling, and QC."""
    load_config = _load_attr("config", "load_config")
    preprocess_dataset = _load_attr("preprocess", "preprocess_dataset")
    config = _run_guarded(load_config, config_file)
    if execution_profile:
        apply_execution_profile = _load_attr("execution", "apply_execution_profile")
        config = _run_guarded(apply_execution_profile, config, execution_profile)
    result = _run_guarded(
        preprocess_dataset,
        bids_dir,
        derivatives_dir,
        config=config,
        subject=subject,
        session=session,
        work_dir=work_dir,
        task=task,
        run=run,
    )
    _emit(result)


@app.command("run")
def run_command_cli(
    archive: Annotated[Path, typer.Argument(exists=True, readable=True)],
    bids_dir: Annotated[Path, typer.Argument()],
    derivatives_dir: Annotated[Path, typer.Argument()],
    config_file: Annotated[
        Path, typer.Option("--config", exists=True, readable=True)
    ],
    subject: Annotated[str, typer.Option("--subject")],
    work_dir: Annotated[Path, typer.Option("--work-dir")],
    session: Annotated[str | None, typer.Option("--session")] = None,
    skip_official_validator: Annotated[
        bool, typer.Option("--skip-official-validator")
    ] = False,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    execution_profile: Annotated[
        str | None, typer.Option("--execution-profile")
    ] = None,
) -> None:
    """Ingest one XNAT archive, then preprocess all BOLD runs as one session graph."""
    load_config = _load_attr("config", "load_config")
    ingest_archive = _load_attr("ingest", "ingest_archive")
    preprocess_dataset = _load_attr("preprocess", "preprocess_dataset")
    config = _run_guarded(load_config, config_file)
    if execution_profile:
        apply_execution_profile = _load_attr("execution", "apply_execution_profile")
        config = _run_guarded(apply_execution_profile, config, execution_profile)
    ingest_result = _run_guarded(
        ingest_archive,
        archive,
        bids_dir,
        config=config,
        subject=subject,
        session=session,
        work_dir=work_dir / "ingest",
        run_validator=not skip_official_validator,
        overwrite=overwrite,
    )
    preprocess_result = _run_guarded(
        preprocess_dataset,
        bids_dir,
        derivatives_dir,
        config=config,
        subject=subject,
        session=session,
        work_dir=work_dir / "preprocess",
    )
    _emit({"ingest": ingest_result, "preprocess": preprocess_result})


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except KeyboardInterrupt as error:
        typer.echo("Interrupted. Completed cache entries were preserved; use 'resume' to continue.", err=True)
        raise SystemExit(130) from error


if __name__ == "__main__":
    main()
