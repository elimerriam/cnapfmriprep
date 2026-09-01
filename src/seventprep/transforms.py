"""Convert a field in Hz to an ITK/ANTs displacement field on the BOLD grid."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import nibabel as nb
import numpy as np

from .errors import ValidationError
from .utils import ensure_dir, read_json, same_nifti_grid


def fieldmap_to_itk_displacement(
    field_hz_file: Path,
    reference_file: Path,
    *,
    phase_encoding_direction: str,
    total_readout_time: float,
    output_dir: Path,
) -> dict[str, Path]:
    """Create SDCFlows-compatible ITK LPS displacements from a TOPUP field.

    Field (Hz) times total readout time (s) yields voxel shifts along the
    BIDS phase-encoding axis. The NIfTI affine converts those shifts to RAS
    millimeters, after which x/y vector components are flipped to ITK LPS.
    """
    if phase_encoding_direction not in {"i", "i-", "j", "j-", "k", "k-"}:
        raise ValidationError(
            f"Invalid PhaseEncodingDirection: {phase_encoding_direction!r}"
        )
    if total_readout_time <= 0:
        raise ValidationError("TotalReadoutTime must be positive")
    if not same_nifti_grid(field_hz_file, reference_file):
        raise ValidationError(
            "TOPUP field and BOLD are not on the same grid. This release stops rather "
            "than introducing an implicit fieldmap-to-BOLD registration."
        )

    output_dir = ensure_dir(output_dir)
    field_image = nb.load(field_hz_file)
    field = np.asanyarray(field_image.dataobj, dtype=np.float32)
    if field.ndim != 3:
        field = np.squeeze(field)
    if field.ndim != 3:
        raise ValidationError(f"Expected a 3D TOPUP field, got {field.shape}")

    sign = -1.0 if phase_encoding_direction.endswith("-") else 1.0
    voxel_shift = field * np.float32(sign * total_readout_time)
    pe_axis = "ijk".index(phase_encoding_direction[0])

    ijk_deltas = np.zeros((voxel_shift.size, 3), dtype=np.float32)
    ijk_deltas[:, pe_axis] = voxel_shift.reshape(-1)
    linear_affine = field_image.affine.copy()
    linear_affine[:3, 3] = 0.0
    ras_deltas = nb.affines.apply_affine(linear_affine, ijk_deltas).astype(np.float32)
    signed_distance = (
        voxel_shift * np.linalg.norm(field_image.affine[:3, pe_axis])
    ).astype(np.float32)

    # ITK stores displacement vectors in LPS physical coordinates.
    lps_deltas = ras_deltas.copy()
    lps_deltas[:, 0:2] *= -1.0
    field_shape = voxel_shift.shape + (1, 3)
    warp_header = field_image.header.copy()
    warp_header.set_data_shape(field_shape)
    warp_header.set_data_dtype(np.float32)
    warp_header.set_intent("vector", name="SDC")
    warp_header.set_xyzt_units("mm")
    warp_file = output_dir / "sdc_itk_warp.nii.gz"
    nb.Nifti1Image(
        lps_deltas.reshape(field_shape),
        field_image.affine,
        warp_header,
    ).to_filename(warp_file)

    scalar_header = field_image.header.copy()
    scalar_header.set_data_shape(voxel_shift.shape)
    scalar_header.set_data_dtype(np.float32)
    voxel_shift_file = output_dir / "voxel_shift_map.nii.gz"
    nb.Nifti1Image(voxel_shift, field_image.affine, scalar_header).to_filename(voxel_shift_file)
    displacement_file = output_dir / "signed_displacement_mm.nii.gz"
    nb.Nifti1Image(signed_distance, field_image.affine, scalar_header).to_filename(
        displacement_file
    )

    jacobian = (1.0 + np.gradient(voxel_shift, axis=pe_axis)).astype(np.float32)
    if not np.all(np.isfinite(jacobian)):
        raise ValidationError("Non-finite values were generated in the SDC Jacobian")
    jacobian_file = output_dir / "sdc_jacobian.nii.gz"
    nb.Nifti1Image(jacobian, field_image.affine, scalar_header).to_filename(jacobian_file)
    return {
        "field_warp": warp_file.resolve(),
        "voxel_shift_map": voxel_shift_file.resolve(),
        "displacement_mm": displacement_file.resolve(),
        "jacobian": jacobian_file.resolve(),
    }


def displacement_to_fieldmap(
    warp_file: Path,
    reference_file: Path,
    *,
    phase_encoding_direction: str,
    total_readout_time: float,
) -> np.ndarray:
    """Inverse conversion used by tests."""
    warp = nb.load(warp_file)
    reference = nb.load(reference_file)
    vectors = np.squeeze(np.asanyarray(warp.dataobj, dtype=np.float32)).reshape((-1, 3))
    vectors[:, 0:2] *= -1.0  # ITK LPS to RAS
    inverse_linear = np.linalg.inv(reference.affine)[:3, :3]
    ijk = vectors @ inverse_linear.T
    pe_axis = "ijk".index(phase_encoding_direction[0])
    vsm = ijk[:, pe_axis].reshape(reference.shape[:3])
    scale = -total_readout_time if phase_encoding_direction.endswith("-") else total_readout_time
    return vsm / scale


def run_field_transform_stage(
    topup_outputs: dict[str, Any],
    bold_file: str,
    bold_json: str,
    output_dir: str,
) -> dict[str, Any]:
    metadata = read_json(bold_json)
    pe_direction = str(metadata.get("PhaseEncodingDirection", ""))
    readout = float(metadata.get("TotalReadoutTime", 0))
    outputs = fieldmap_to_itk_displacement(
        Path(topup_outputs["field_hz"]),
        Path(bold_file),
        phase_encoding_direction=pe_direction,
        total_readout_time=readout,
        output_dir=Path(output_dir),
    )
    return {
        key: str(value)
        for key, value in outputs.items()
    } | {
        "phase_encoding_direction": pe_direction,
        "total_readout_time": readout,
    }
