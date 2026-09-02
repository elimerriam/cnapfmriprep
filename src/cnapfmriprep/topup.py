"""TOPUP field estimation from reverse phase-encoded EPI data."""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import nibabel as nb
import numpy as np

from .errors import ValidationError
from .utils import assert_same_nifti_grid, ensure_dir, read_json, require_executable, run_command


def phase_encoding_vector(direction: str) -> tuple[int, int, int]:
    if direction not in {"i", "i-", "j", "j-", "k", "k-"}:
        raise ValidationError(f"Invalid BIDS PhaseEncodingDirection: {direction!r}")
    sign = -1 if direction.endswith("-") else 1
    axis = "ijk".index(direction[0])
    vector = [0, 0, 0]
    vector[axis] = sign
    return tuple(vector)


def _extract_selected_volumes(
    source: Path,
    count: int,
    output_dir: Path,
    label: str,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    image = nb.load(source)
    n_volumes = image.shape[3] if len(image.shape) == 4 else 1
    selected = min(count, n_volumes)
    paths: list[Path] = []
    for index in range(selected):
        out_file = output_dir / f"{label}_vol-{index:02d}.nii.gz"
        data = (
            np.asanyarray(image.dataobj[..., index], dtype=np.float32)
            if len(image.shape) == 4
            else np.asanyarray(image.dataobj, dtype=np.float32)
        )
        header = image.header.copy()
        header.set_data_shape(data.shape)
        header.set_data_dtype(np.float32)
        nb.Nifti1Image(data, image.affine, header).to_filename(out_file)
        paths.append(out_file.resolve())
    return paths


def _merge_topup_inputs(volumes: Sequence[Path], out_file: Path) -> Path:
    if not volumes:
        raise ValidationError("No volumes selected for TOPUP")
    fslmerge = shutil.which("fslmerge")
    if fslmerge:
        run_command(
            [fslmerge, "-t", str(out_file), *map(str, volumes)],
            log_file=out_file.parent / "merge_topup.log",
        )
    else:
        images = [nb.load(path) for path in volumes]
        data = np.stack(
            [np.asanyarray(image.dataobj, dtype=np.float32) for image in images],
            axis=3,
        )
        header = images[0].header.copy()
        header.set_data_shape(data.shape)
        header.set_data_dtype(np.float32)
        nb.Nifti1Image(data, images[0].affine, header).to_filename(out_file)
    return out_file.resolve()


def _auto_config(image_file: Path) -> Path:
    shape = nb.load(image_file).shape[:3]
    if all(dimension % 4 == 0 for dimension in shape):
        suffix = "4"
    elif all(dimension % 2 == 0 for dimension in shape):
        suffix = "2"
    else:
        suffix = "1"
    fsldir = os.environ.get("FSLDIR")
    if not fsldir:
        raise ValidationError("FSLDIR is not set; cannot locate a stock TOPUP configuration")
    path = Path(fsldir) / "etc" / "flirtsch" / f"b02b0_{suffix}.cnf"
    if not path.exists():
        raise ValidationError(f"TOPUP configuration does not exist: {path}")
    return path.resolve()


def _resolve_config(config_value: str, image_file: Path) -> Path:
    if config_value == "auto":
        return _auto_config(image_file)
    path = Path(config_value).expanduser()
    if path.exists():
        return path.resolve()
    fsldir = os.environ.get("FSLDIR")
    if fsldir:
        candidate = Path(fsldir) / "etc" / "flirtsch" / config_value
        if candidate.exists():
            return candidate.resolve()
    raise ValidationError(f"Could not locate TOPUP configuration: {config_value}")


def _fsl_output(root: Path) -> Path:
    for candidate in (root, Path(str(root) + ".nii.gz"), Path(str(root) + ".nii")):
        if candidate.exists():
            return candidate.resolve()
    raise ValidationError(f"Expected FSL output was not created: {root}")


def run_topup_stage(
    fmap_files: list[str],
    fmap_jsons: list[str],
    output_dir: str,
    topup_config: dict[str, Any],
) -> dict[str, Any]:
    """Pydra-friendly TOPUP stage."""
    if len(fmap_files) != len(fmap_jsons) or len(fmap_files) < 2:
        raise ValidationError("TOPUP requires matching lists of at least two EPI files/sidecars")
    root = ensure_dir(output_dir)
    image_paths = [Path(path).resolve() for path in fmap_files]
    json_paths = [Path(path).resolve() for path in fmap_jsons]
    for image in image_paths[1:]:
        assert_same_nifti_grid(image_paths[0], image, context="reverse-PE TOPUP inputs")

    selected: list[Path] = []
    acqparams: list[str] = []
    directions: list[str] = []
    count = int(topup_config.get("volumes_per_direction", 1))
    fallback = topup_config.get("fallback_total_readout_time")
    for series_index, (image_path, json_path) in enumerate(zip(image_paths, json_paths, strict=True)):
        metadata = read_json(json_path)
        direction = str(metadata.get("PhaseEncodingDirection", ""))
        vector = phase_encoding_vector(direction)
        readout = float(metadata.get("TotalReadoutTime", fallback or 0))
        if readout <= 0:
            raise ValidationError(f"No valid TotalReadoutTime in {json_path}")
        series_volumes = _extract_selected_volumes(
            image_path,
            count,
            root / "selected",
            f"series-{series_index:02d}",
        )
        selected.extend(series_volumes)
        acqparams.extend(
            [f"{vector[0]} {vector[1]} {vector[2]} {readout:.10g}"] * len(series_volumes)
        )
        directions.append(direction)

    if not any(
        first[0] == second[0] and first.endswith("-") != second.endswith("-")
        for first in directions
        for second in directions
    ):
        raise ValidationError(f"TOPUP inputs are not opposite PE polarities: {directions}")

    topup_input = _merge_topup_inputs(selected, root / "topup_input.nii.gz")
    acqparams_file = root / "acqparams.txt"
    acqparams_file.write_text("\n".join(acqparams) + "\n")
    config_path = _resolve_config(str(topup_config.get("config", "auto")), topup_input)
    topup = require_executable("topup")
    output_prefix = root / "topup"
    field_root = root / "field_hz"
    corrected_root = root / "topup_corrected"
    run_command(
        [
            topup,
            f"--imain={topup_input}",
            f"--datain={acqparams_file}",
            f"--config={config_path}",
            f"--out={output_prefix}",
            f"--fout={field_root}",
            f"--iout={corrected_root}",
            f"--logout={root / 'topup.log'}",
        ],
        log_file=root / "topup_command.log",
    )
    field_hz = _fsl_output(field_root)
    topup_corrected = _fsl_output(corrected_root)
    field_coefficients = _fsl_output(Path(str(output_prefix) + "_fieldcoef"))
    motion_parameters = Path(str(output_prefix) + "_movpar.txt")
    if not motion_parameters.exists():
        raise ValidationError(f"TOPUP movement parameters are missing: {motion_parameters}")
    return {
        "topup_input": str(topup_input),
        "acqparams": str(acqparams_file.resolve()),
        "config": str(config_path),
        "field_hz": str(field_hz),
        "field_coefficients": str(field_coefficients),
        "topup_corrected": str(topup_corrected),
        "topup_motion_parameters": str(motion_parameters.resolve()),
        "phase_encoding_directions": directions,
    }
