"""Magnitude-only NORDIC wrapper with two trailing RF-off volumes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Sequence

import nibabel as nb
import numpy as np

from .errors import ExternalCommandError, ValidationError
from .utils import ensure_dir, run_command, same_nifti_grid, write_json


def _matlab_escape(path: Path) -> str:
    return str(path).replace("'", "''")


def _find_executable(command: str) -> str:
    path = Path(command).expanduser()
    if path.is_absolute() and path.exists():
        return str(path)
    found = shutil.which(command)
    if found is None:
        raise ExternalCommandError(f"MATLAB command was not found: {command}")
    return found


def concatenate_bold_and_noise(
    bold_file: Path,
    no_rf_file: Path,
    out_file: Path,
    *,
    expected_noise_volumes: int,
    log_file: Path | None = None,
) -> tuple[Path, int]:
    bold = nb.load(bold_file)
    noise = nb.load(no_rf_file)
    if len(bold.shape) != 4 or len(noise.shape) != 4:
        raise ValidationError("Both BOLD and no-RF inputs must be four-dimensional")
    if noise.shape[3] != expected_noise_volumes:
        raise ValidationError(
            f"Expected {expected_noise_volumes} no-RF volumes, found {noise.shape[3]}"
        )
    if not same_nifti_grid(bold_file, no_rf_file):
        raise ValidationError("BOLD and no-RF images are not on the same spatial grid")
    out_file = Path(out_file).resolve()
    ensure_dir(out_file.parent)
    fslmerge = shutil.which("fslmerge")
    if fslmerge:
        run_command(
            [fslmerge, "-t", str(out_file), str(bold_file), str(no_rf_file)],
            log_file=log_file,
        )
    else:
        data = np.concatenate(
            [
                np.asanyarray(bold.dataobj),
                np.asanyarray(noise.dataobj),
            ],
            axis=3,
        )
        header = bold.header.copy()
        header.set_data_shape(data.shape)
        nb.Nifti1Image(data, bold.affine, header).to_filename(out_file)
    merged = nb.load(out_file)
    expected = bold.shape[3] + expected_noise_volumes
    if merged.shape[3] != expected:
        raise ValidationError(f"NORDIC input has {merged.shape[3]} volumes; expected {expected}")
    return out_file, int(bold.shape[3])


def _run_nordic_matlab(
    magnitude_file: Path,
    output_dir: Path,
    *,
    checkout: Path,
    matlab_command: str,
    noise_volume_last: int,
    factor_error: float,
    save_gfactor_map: bool,
    save_additional_info: bool,
) -> dict[str, Path | None]:
    checkout = checkout.expanduser().resolve()
    if not (checkout / "NIFTI_NORDIC.m").is_file():
        raise ValidationError(
            f"NIFTI_NORDIC.m was not found in the configured checkout: {checkout}"
        )
    output_dir = ensure_dir(output_dir)
    prefix = "nordic_full"
    job = {
        "nordic_path": str(checkout),
        "magnitude_file": str(magnitude_file.resolve()),
        "output_directory": str(output_dir),
        "output_prefix": prefix,
        "noise_volume_last": int(noise_volume_last),
        "factor_error": float(factor_error),
        "save_gfactor_map": bool(save_gfactor_map),
        "save_additional_info": bool(save_additional_info),
    }
    job_file = write_json(output_dir / "nordic_job.json", job)
    wrapper_dir = Path(__file__).resolve().parent / "matlab"
    expression = (
        f"addpath('{_matlab_escape(wrapper_dir)}'); "
        f"run_nordic_job('{_matlab_escape(job_file)}');"
    )
    run_command(
        [_find_executable(matlab_command), "-batch", expression],
        log_file=output_dir / "matlab_nordic.log",
    )

    output_candidates = [
        output_dir / f"{prefix}.nii.gz",
        output_dir / f"{prefix}.nii",
    ]
    output = next((path for path in output_candidates if path.exists()), None)
    if output is None:
        raise ExternalCommandError(
            f"NORDIC completed without creating {prefix}.nii[.gz]; see {output_dir / 'matlab_nordic.log'}"
        )
    gfactor = next(
        (
            path
            for path in (
                output_dir / f"gfactor_{prefix}.nii.gz",
                output_dir / f"gfactor_{prefix}.nii",
            )
            if path.exists()
        ),
        None,
    )
    info = output_dir / f"{prefix}.mat"
    return {
        "full_output": output.resolve(),
        "gfactor": gfactor.resolve() if gfactor else None,
        "additional_info": info.resolve() if info.exists() else None,
        "job_json": job_file,
    }


def _split_nordic_output(
    full_output: Path,
    bold_count: int,
    noise_count: int,
    bold_out: Path,
    noise_out: Path,
) -> tuple[Path, Path]:
    image = nb.load(full_output)
    if len(image.shape) != 4 or image.shape[3] != bold_count + noise_count:
        raise ValidationError(
            f"NORDIC output shape {image.shape} does not retain the expected "
            f"{bold_count + noise_count} volumes"
        )
    fslroi = shutil.which("fslroi")
    if fslroi:
        run_command(
            [fslroi, str(full_output), str(bold_out), "0", str(bold_count)],
            log_file=bold_out.parent / "trim_bold.log",
        )
        run_command(
            [fslroi, str(full_output), str(noise_out), str(bold_count), str(noise_count)],
            log_file=noise_out.parent / "trim_noise.log",
        )
    else:
        dataobj = image.dataobj
        header = image.header.copy()
        bold_data = np.asanyarray(dataobj[..., :bold_count], dtype=np.float32)
        noise_data = np.asanyarray(dataobj[..., bold_count:], dtype=np.float32)
        bold_header = header.copy()
        bold_header.set_data_shape(bold_data.shape)
        noise_header = header.copy()
        noise_header.set_data_shape(noise_data.shape)
        nb.Nifti1Image(bold_data, image.affine, bold_header).to_filename(bold_out)
        nb.Nifti1Image(noise_data, image.affine, noise_header).to_filename(noise_out)
    return bold_out.resolve(), noise_out.resolve()


def _volume_stats(path: Path) -> list[dict[str, float | int]]:
    image = nb.load(path)
    if len(image.shape) != 4:
        raise ValidationError(f"Expected 4D no-RF image for QC: {path}")
    output = []
    for index in range(image.shape[3]):
        data = np.asanyarray(image.dataobj[..., index], dtype=np.float64)
        finite = data[np.isfinite(data)]
        output.append(
            {
                "volume": index,
                "mean": float(np.mean(finite)),
                "standard_deviation": float(np.std(finite, ddof=1)),
                "median": float(np.median(finite)),
                "p01": float(np.percentile(finite, 1)),
                "p99": float(np.percentile(finite, 99)),
            }
        )
    return output


def run_nordic_stage(
    bold_file: str,
    no_rf_file: str,
    output_dir: str,
    nordic_config: dict[str, Any],
    expected_noise_volumes: int = 2,
) -> dict[str, Any]:
    """Pydra-friendly NORDIC stage."""
    root = ensure_dir(output_dir)
    bold_path = Path(bold_file).resolve()
    noise_path = Path(no_rf_file).resolve()
    concatenated, bold_count = concatenate_bold_and_noise(
        bold_path,
        noise_path,
        root / "bold_plus_noRF.nii.gz",
        expected_noise_volumes=expected_noise_volumes,
        log_file=root / "concatenate.log",
    )
    matlab_outputs = _run_nordic_matlab(
        concatenated,
        root / "matlab",
        checkout=Path(nordic_config["checkout"]),
        matlab_command=str(nordic_config.get("matlab_command", "matlab")),
        noise_volume_last=int(nordic_config.get("noise_volume_last", 2)),
        factor_error=float(nordic_config.get("factor_error", 1.0)),
        save_gfactor_map=bool(nordic_config.get("save_gfactor_map", True)),
        save_additional_info=bool(nordic_config.get("save_additional_info", True)),
    )
    nordic_bold, nordic_noise = _split_nordic_output(
        Path(matlab_outputs["full_output"]),
        bold_count,
        expected_noise_volumes,
        root / "desc-nordic_bold.nii.gz",
        root / "desc-nordic_noRF.nii.gz",
    )
    if not same_nifti_grid(bold_path, nordic_bold):
        raise ValidationError("NORDIC changed the BOLD spatial grid")
    if nb.load(nordic_bold).shape != nb.load(bold_path).shape:
        raise ValidationError("NORDIC/trimmed BOLD shape differs from the raw BOLD shape")

    original_stats = _volume_stats(noise_path)
    nordic_stats = _volume_stats(nordic_noise)
    stds = [float(row["standard_deviation"]) for row in original_stats]
    ratio = max(stds) / max(min(stds), np.finfo(float).eps)
    stats_payload: dict[str, Any] = {
        "number_of_no_rf_volumes": expected_noise_volumes,
        "NORDIC_noise_volume_index_from_end": int(
            nordic_config.get("noise_volume_last", 2)
        ),
        "selected_zero_based_index_within_no_rf_file": (
            expected_noise_volumes - int(nordic_config.get("noise_volume_last", 2))
        ),
        "original_no_rf": original_stats,
        "nordic_no_rf": nordic_stats,
        "original_standard_deviation_max_to_min_ratio": float(ratio),
        "warning": (
            "The upstream compatibility mode uses the penultimate appended volume when "
            "noise_volume_last=2; it does not pool both no-RF volumes."
        ),
    }
    stats_json = write_json(root / "no_rf_stats.json", stats_payload)
    return {
        "nordic_bold": str(nordic_bold),
        "nordic_no_rf": str(nordic_noise),
        "nordic_full": str(matlab_outputs["full_output"]),
        "gfactor": str(matlab_outputs["gfactor"]) if matlab_outputs["gfactor"] else None,
        "additional_info": (
            str(matlab_outputs["additional_info"])
            if matlab_outputs["additional_info"]
            else None
        ),
        "job_json": str(matlab_outputs["job_json"]),
        "no_rf_stats_json": str(stats_json),
        "functional_volume_count": bold_count,
    }
