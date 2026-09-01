"""Command-line interface.

The CLI deliberately keeps scientific and workflow-engine imports lazy. This
allows lightweight commands such as ``version``, ``doctor``, and ``inventory``
to start even when an optional preprocessing dependency is broken or has the
wrong version.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from . import __version__
from .errors import SeventPrepError

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
    except SeventPrepError as error:
        typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(code=2) from error


def _load_attr(module_name: str, attribute: str):
    """Import one internal attribute and turn dependency failures into useful errors."""
    qualified = f"{__package__}.{module_name}"
    try:
        module = importlib.import_module(f".{module_name}", package=__package__)
        return getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        if os.environ.get("SEVENTPREP_DEBUG_IMPORTS") == "1":
            raise
        typer.echo(
            "ERROR: SevenTPrep could not load "
            f"{qualified}.{attribute}: {type(error).__name__}: {error}\n"
            "Run 'seventprep doctor' for interpreter and dependency details. "
            "Set SEVENTPREP_DEBUG_IMPORTS=1 to show the full traceback.",
            err=True,
        )
        raise typer.Exit(code=2) from error


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


@app.command("version")
def version_command() -> None:
    """Print the seventprep version."""
    typer.echo(__version__)


@app.command("doctor")
def doctor_command() -> None:
    """Report the active interpreter, package locations, and import health."""
    distributions = [
        "seventprep",
        "typer",
        "click",
        "pydra",
        "nibabel",
        "pydicom",
        "pydantic",
        "numpy",
        "scipy",
        "nitransforms",
        "PyYAML",
    ]
    modules = [
        "seventprep.config",
        "seventprep.dicom",
        "seventprep.ingest",
        "seventprep.pydra_workflows",
        "seventprep.preprocess",
    ]
    imports: dict[str, dict[str, Any]] = {}
    failed = False
    for module_name in modules:
        try:
            module = importlib.import_module(module_name)
            imports[module_name] = {
                "ok": True,
                "file": str(getattr(module, "__file__", "")),
            }
        except Exception as error:  # Diagnostic command must report all import failures.
            failed = True
            imports[module_name] = {
                "ok": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }

    try:
        pydra_module = importlib.import_module("pydra")
        pydra_api = {
            "import_ok": True,
            "file": str(getattr(pydra_module, "__file__", "")),
            "has_Submitter": hasattr(pydra_module, "Submitter"),
            "has_Workflow": hasattr(pydra_module, "Workflow"),
            "has_mark": hasattr(pydra_module, "mark"),
        }
        if not all(
            pydra_api[key]
            for key in ("has_Submitter", "has_Workflow", "has_mark")
        ):
            failed = True
    except Exception as error:
        failed = True
        pydra_api = {
            "import_ok": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }

    executable_names = [
        "python",
        "seventprep",
        "dcm2niix",
        "topup",
        "antsRegistration",
        "antsApplyTransforms",
        "matlab",
        "bids-validator-deno",
        "bids-validator",
    ]
    report = {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "conda_prefix": os.environ.get("CONDA_PREFIX"),
        "seventprep_version": __version__,
        "seventprep_package": str(Path(__file__).resolve().parent),
        "console_script": shutil.which("seventprep"),
        "executables": {name: shutil.which(name) for name in executable_names},
        "distributions": {name: _distribution_version(name) for name in distributions},
        "pydra_025_api": pydra_api,
        "imports": imports,
    }
    _emit(report)
    if failed:
        raise typer.Exit(code=1)


@app.command("check-deps")
def check_dependencies(
    config_file: Path = typer.Option(..., "--config", exists=True, readable=True),
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
    work_dir: Path = typer.Option(..., "--work-dir", exists=True, file_okay=False),
) -> None:
    """Quarantine interrupted empty Pydra results while preserving valid tasks."""
    recover = _load_attr("cache", "recover_interrupted_pydra_cache")
    result = _run_guarded(recover, work_dir / "pydra-cache")
    _emit(result)


@app.command("inventory")
def inventory_command(
    archive: Path = typer.Argument(..., exists=True, readable=True),
    output_dir: Path = typer.Option(..., "--output-dir"),
    config_file: Optional[Path] = typer.Option(None, "--config", exists=True, readable=True),
) -> None:
    """Safely extract and inventory an XNAT ZIP or tar archive; optionally test series rules."""
    load_config = _load_attr("config", "load_config")
    inventory_archive = _load_attr("ingest", "inventory_archive")
    config = _run_guarded(load_config, config_file) if config_file else None
    result = _run_guarded(inventory_archive, archive, output_dir, config=config)
    _emit(result)


@app.command("setup")
def setup_command(
    archive: Optional[Path] = typer.Argument(None),
    inventory_tsv: Optional[Path] = typer.Option(None, "--inventory"),
    template_file: Path = typer.Option(..., "--template", exists=True, readable=True),
    output_file: Path = typer.Option(..., "--output"),
    work_dir: Path = typer.Option(Path("work/setup"), "--work-dir"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    no_browser: bool = typer.Option(False, "--no-browser"),
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
    archive: Path = typer.Argument(..., exists=True, readable=True),
    bids_dir: Path = typer.Argument(...),
    config_file: Path = typer.Option(..., "--config", exists=True, readable=True),
    subject: str = typer.Option(..., "--subject"),
    session: Optional[str] = typer.Option(None, "--session"),
    work_dir: Path = typer.Option(..., "--work-dir"),
    skip_official_validator: bool = typer.Option(False, "--skip-official-validator"),
    overwrite: bool = typer.Option(False, "--overwrite"),
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
    bids_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    subject: Optional[str] = typer.Option(None, "--subject"),
    session: Optional[str] = typer.Option(None, "--session"),
    expected_no_rf_volumes: int = typer.Option(2, "--expected-no-rf-volumes"),
    skip_official_validator: bool = typer.Option(False, "--skip-official-validator"),
) -> None:
    """Run the official validator and seventprep semantic checks."""
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
    bids_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    derivatives_dir: Path = typer.Argument(...),
    config_file: Path = typer.Option(..., "--config", exists=True, readable=True),
    subject: str = typer.Option(..., "--subject"),
    session: Optional[str] = typer.Option(None, "--session"),
    work_dir: Path = typer.Option(..., "--work-dir"),
    task: Optional[str] = typer.Option(None, "--task"),
    run: Optional[int] = typer.Option(None, "--run", min=1),
    execution_profile: Optional[str] = typer.Option(None, "--execution-profile"),
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
    archive: Path = typer.Argument(..., exists=True, readable=True),
    bids_dir: Path = typer.Argument(...),
    derivatives_dir: Path = typer.Argument(...),
    config_file: Path = typer.Option(..., "--config", exists=True, readable=True),
    subject: str = typer.Option(..., "--subject"),
    session: Optional[str] = typer.Option(None, "--session"),
    work_dir: Path = typer.Option(..., "--work-dir"),
    skip_official_validator: bool = typer.Option(False, "--skip-official-validator"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    execution_profile: Optional[str] = typer.Option(None, "--execution-profile"),
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
    app()


if __name__ == "__main__":
    main()
