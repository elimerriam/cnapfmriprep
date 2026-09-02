"""Portable, side-effect-free dependency diagnostics and shell guidance."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_REQUIRED_EXECUTABLES = (
    "dcm2niix",
    "topup",
    "antsRegistration",
    "antsApplyTransforms",
    "matlab",
)
_IMPORTS = (
    "cnapfmriprep.config",
    "cnapfmriprep.dicom",
    "cnapfmriprep.ingest",
    "cnapfmriprep.pydra_workflows",
    "cnapfmriprep.preprocess",
)
_DISTRIBUTIONS = (
    "cnapfmriprep",
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
)
_EXECUTABLE_GUIDANCE = {
    "dcm2niix": "Install dcm2niix (the Conda package is supported) and add it to PATH.",
    "topup": "Install FSL and source its shell configuration so topup is on PATH.",
    "antsRegistration": "Install ANTs (the Conda package is supported) and add it to PATH.",
    "antsApplyTransforms": "Install ANTs (the Conda package is supported) and add it to PATH.",
    "matlab": (
        "Install MATLAB and add it to PATH, or set nordic.matlab_command to its executable."
    ),
    "bids_validator": (
        "Install the official bids-validator-deno or bids-validator command and add it to PATH."
    ),
}


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _candidate_in_prefix(prefix: str | None, executable: str) -> Path | None:
    if not prefix:
        return None
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(prefix).expanduser() / ("Scripts" if os.name == "nt" else "bin")
    candidate = candidate / f"{executable}{suffix}"
    return candidate.resolve() if candidate.is_file() else None


def _resolve_executable(
    executable: str,
    *,
    environment: Mapping[str, str],
    which: Callable[..., str | None],
) -> dict[str, Any]:
    on_path = which(executable, path=environment.get("PATH"))
    conda_candidate = _candidate_in_prefix(environment.get("CONDA_PREFIX"), executable)
    return {
        "ok": bool(on_path),
        "path": on_path,
        "conda_candidate": str(conda_candidate) if conda_candidate else None,
        "message": (
            "available"
            if on_path
            else (
                "installed in the active Conda environment but absent from PATH"
                if conda_candidate
                else "not found"
            )
        ),
    }


def _infer_fsldir(
    environment: Mapping[str, str],
    executable_records: Mapping[str, Mapping[str, Any]],
) -> Path | None:
    configured = environment.get("FSLDIR")
    if configured:
        return Path(configured).expanduser().resolve()
    topup = executable_records.get("topup", {}).get("path")
    if topup:
        path = Path(str(topup)).resolve()
        if path.parent.name == "bin":
            return path.parent.parent
    return None


def _shell_quote(value: str | Path) -> str:
    return shlex.quote(str(value))


def render_shell_setup(
    shell: str,
    *,
    fsldir: str | Path | None,
    conda_prefix: str | Path | None,
) -> str:
    """Render commands for review; this function never edits a shell file."""
    normalized = shell.lower()
    if normalized not in {"tcsh", "csh", "bash", "zsh", "fish"}:
        raise ValueError("shell must be one of: tcsh, csh, bash, zsh, fish")
    fsl = str(Path(fsldir).expanduser()) if fsldir else "/PATH/TO/FSL"
    conda = str(Path(conda_prefix).expanduser()) if conda_prefix else None
    if normalized in {"tcsh", "csh"}:
        path_parts = ([f"{conda}/bin"] if conda else []) + ["${FSLDIR}/bin", "${PATH}"]
        path_value = ":".join(path_parts)
        return "\n".join(
            [
                "# Review these commands, then run them in the current shell.",
                f"setenv FSLDIR {_shell_quote(fsl)}",
                'if ( -e "${FSLDIR}/etc/fslconf/fsl.csh" ) source "${FSLDIR}/etc/fslconf/fsl.csh"',
                f'setenv PATH "{path_value}"',
                "rehash",
            ]
        )
    if normalized == "fish":
        path_parts = ([f"{conda}/bin"] if conda else []) + ["$FSLDIR/bin", "$PATH"]
        return "\n".join(
            [
                "# Review these commands, then run them in the current shell.",
                f"set -gx FSLDIR {_shell_quote(fsl)}",
                "test -f $FSLDIR/etc/fslconf/fsl.fish; and source $FSLDIR/etc/fslconf/fsl.fish",
                "set -gx PATH " + " ".join(path_parts),
            ]
        )
    path_parts = ([f"{conda}/bin"] if conda else []) + ["${FSLDIR}/bin", "${PATH}"]
    path_value = ":".join(path_parts)
    return "\n".join(
        [
            "# Review these commands, then run them in the current shell.",
            f"export FSLDIR={_shell_quote(fsl)}",
            '[ -f "${FSLDIR}/etc/fslconf/fsl.sh" ] && . "${FSLDIR}/etc/fslconf/fsl.sh"',
            f'export PATH="{path_value}"',
            "hash -r",
        ]
    )


def _probe_matlab_license(
    executable: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    marker = "CNAPFMRIPREP_LICENSE_OK"
    try:
        completed = runner(
            [executable, "-batch", f"disp('{marker}')"],
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "status": "timeout",
            "message": "MATLAB did not complete the license probe within 90 seconds",
        }
    except OSError as error:
        return {"ok": False, "status": "error", "message": str(error)}
    combined = "\n".join((completed.stdout or "", completed.stderr or ""))
    lowered = combined.lower()
    license_failure = any(
        phrase in lowered
        for phrase in (
            "license manager error",
            "license checkout failed",
            "checkout failed",
            "no licenses available",
        )
    )
    ok = completed.returncode == 0 and marker in combined and not license_failure
    message = "license checkout succeeded" if ok else "MATLAB license probe failed"
    if license_failure:
        message = "MATLAB reported that a license could not be checked out"
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "returncode": completed.returncode,
        "message": message,
    }


def collect_diagnostics(
    *,
    config_file: str | Path | None = None,
    check_matlab_license: bool = False,
    environment: Mapping[str, str] | None = None,
    which: Callable[..., str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    distribution_version: Callable[[str], str | None] = _distribution_version,
    import_module: Callable[[str], Any] = importlib.import_module,
) -> dict[str, Any]:
    """Collect actionable dependency checks without changing the environment."""
    from . import __version__

    env = dict(os.environ if environment is None else environment)
    recommendations: list[str] = []
    failures: list[str] = []
    warnings: list[str] = []

    python_ok = (3, 11) <= sys.version_info[:2] < (3, 13)
    if not python_ok:
        failures.append("python")
        recommendations.append("Use Python 3.11 or 3.12.")

    distributions = {name: distribution_version(name) for name in _DISTRIBUTIONS}
    installed_version = distributions["cnapfmriprep"]
    package_ok = installed_version == __version__
    if not package_ok:
        failures.append("package_version")
        recommendations.append(
            "Reinstall the checkout with `python -m pip install --no-build-isolation -e .`."
        )

    imports: dict[str, dict[str, Any]] = {}
    for module_name in _IMPORTS:
        try:
            module = import_module(module_name)
            imports[module_name] = {
                "ok": True,
                "file": str(getattr(module, "__file__", "")),
            }
        except Exception as error:
            imports[module_name] = {
                "ok": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            failures.append(f"import:{module_name}")
    if any(not record["ok"] for record in imports.values()):
        recommendations.append(
            "Repair the Python environment before preprocessing; rerun with "
            "CNAPFMRIPREP_DEBUG_IMPORTS=1 for a traceback."
        )

    try:
        pydra_module = import_module("pydra")
        api_fields = {
            "has_Submitter": hasattr(pydra_module, "Submitter"),
            "has_Workflow": hasattr(pydra_module, "Workflow"),
            "has_mark": hasattr(pydra_module, "mark"),
        }
        pydra_ok = distributions["pydra"] in {"0.25", "0.25.0"} and all(
            api_fields.values()
        )
        pydra = {
            "ok": pydra_ok,
            "version": distributions["pydra"],
            "file": str(getattr(pydra_module, "__file__", "")),
            **api_fields,
        }
    except Exception as error:
        pydra_ok = False
        pydra = {
            "ok": False,
            "version": distributions["pydra"],
            "error_type": type(error).__name__,
            "error": str(error),
        }
    if not pydra_ok:
        failures.append("pydra")
        recommendations.append(
            "Install the pinned workflow engine with `python -m pip install 'pydra==0.25.0'`."
        )

    nordic_root: Path | None = None
    matlab_command = "matlab"
    config_error: str | None = None
    if config_file is not None:
        try:
            from .config import load_config

            config = load_config(config_file)
            nordic_root = config.nordic.checkout.resolve()
            matlab_command = config.nordic.matlab_command
        except Exception as error:
            config_error = str(error)
            failures.append("config")
            recommendations.append(f"Correct the study configuration: {error}")
    elif env.get("NORDIC_ROOT"):
        nordic_root = Path(env["NORDIC_ROOT"]).expanduser().resolve()

    executables = {
        name: _resolve_executable(name, environment=env, which=which)
        for name in _REQUIRED_EXECUTABLES
    }
    if matlab_command != "matlab":
        configured_matlab = which(matlab_command, path=env.get("PATH"))
        configured_path = Path(matlab_command).expanduser()
        if configured_matlab is None and configured_path.is_file():
            configured_matlab = str(configured_path.resolve())
        executables["matlab"] = {
            "ok": bool(configured_matlab),
            "path": configured_matlab,
            "conda_candidate": None,
            "command": matlab_command,
            "message": "available" if configured_matlab else "not found",
        }
    validator = which("bids-validator-deno", path=env.get("PATH")) or which(
        "bids-validator", path=env.get("PATH")
    )
    executables["bids_validator"] = {
        "ok": bool(validator),
        "path": validator,
        "message": "available" if validator else "not found",
    }
    for name, record in executables.items():
        if not record["ok"]:
            failures.append(f"executable:{name}")
            if record.get("conda_candidate"):
                recommendations.append(
                    f"Add the active Conda bin directory to PATH so {name} resolves to "
                    f"{record['conda_candidate']}."
                )
            else:
                recommendations.append(_EXECUTABLE_GUIDANCE[name])

    fsldir = _infer_fsldir(env, executables)
    fsl_config_dir = fsldir / "etc" / "flirtsch" if fsldir else None
    fsl_configs = (
        sorted(path.name for path in fsl_config_dir.glob("b02b0_*.cnf"))
        if fsl_config_dir and fsl_config_dir.is_dir()
        else []
    )
    fsldir_ok = bool(fsldir and fsldir.is_dir())
    fsl_output_type = env.get("FSLOUTPUTTYPE")
    fsl_ok = fsldir_ok and bool(fsl_output_type) and bool(fsl_configs)
    if not fsldir_ok:
        failures.append("fsl:FSLDIR")
        recommendations.append("Set FSLDIR to the FSL installation directory.")
    if not fsl_output_type:
        failures.append("fsl:FSLOUTPUTTYPE")
        recommendations.append(
            "Source FSL's shell configuration so FSLOUTPUTTYPE and related variables are set."
        )
    if not fsl_configs:
        failures.append("fsl:topup_config")
        recommendations.append("Verify that FSLDIR contains etc/flirtsch/b02b0_*.cnf.")
    fsl = {
        "ok": fsl_ok,
        "FSLDIR": str(fsldir) if fsldir else None,
        "FSLDIR_exists": fsldir_ok,
        "FSLOUTPUTTYPE": fsl_output_type,
        "topup_configs": fsl_configs,
    }

    nordic_script = nordic_root / "NIFTI_NORDIC.m" if nordic_root else None
    nordic_ok = bool(nordic_script and nordic_script.is_file())
    nordic_status = "valid" if nordic_ok else ("not configured" if nordic_root is None else "missing")
    if nordic_root is None:
        warnings.append("nordic:not_configured")
        recommendations.append(
            "Pass `--config config/my_study.yaml` to doctor or set NORDIC_ROOT to verify NORDIC."
        )
    elif not nordic_ok:
        failures.append("nordic")
        recommendations.append(f"Place NIFTI_NORDIC.m in the configured checkout: {nordic_root}")
    nordic = {
        "ok": nordic_ok if nordic_root is not None else None,
        "status": nordic_status,
        "checkout": str(nordic_root) if nordic_root else None,
        "script": str(nordic_script) if nordic_script else None,
        "config_error": config_error,
    }

    matlab_path = executables["matlab"]["path"]
    if matlab_command != "matlab":
        matlab_path = which(matlab_command, path=env.get("PATH"))
        if matlab_path is None and Path(matlab_command).expanduser().is_file():
            matlab_path = str(Path(matlab_command).expanduser().resolve())
    if check_matlab_license and matlab_path:
        matlab_license = _probe_matlab_license(matlab_path, runner=runner)
        if not matlab_license["ok"]:
            failures.append("matlab_license")
            recommendations.append(
                "Confirm the MATLAB license server or wait for a license, then rerun the probe."
            )
    else:
        matlab_license = {
            "ok": None,
            "status": "not_run",
            "message": (
                "pass --check-matlab-license to perform a bounded license checkout"
                if matlab_path
                else "MATLAB is unavailable"
            ),
        }

    unique_recommendations = list(dict.fromkeys(recommendations))
    return {
        "ok": not failures,
        "python": {
            "ok": python_ok,
            "executable": sys.executable,
            "version": sys.version,
            "supported": ">=3.11,<3.13",
        },
        "environment": {
            "CONDA_PREFIX": env.get("CONDA_PREFIX"),
            "PATH": env.get("PATH"),
        },
        "package": {
            "ok": package_ok,
            "module_version": __version__,
            "distribution_version": installed_version,
            "package_dir": str(Path(__file__).resolve().parent),
        },
        "distributions": distributions,
        "imports": imports,
        "pydra": pydra,
        "executables": executables,
        "fsl": fsl,
        "nordic": nordic,
        "matlab_license": matlab_license,
        "failures": failures,
        "warnings": warnings,
        "recommendations": unique_recommendations,
    }
