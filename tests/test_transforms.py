from __future__ import annotations

import shutil
from pathlib import Path

import nibabel as nb
import numpy as np
import pytest
from scipy import ndimage

from cnapfmriprep.transforms import (
    displacement_to_fieldmap,
    fieldmap_to_itk_displacement,
)
from cnapfmriprep.utils import run_command


@pytest.mark.parametrize("pe_dir", ["i", "i-", "j", "j-", "k", "k-"])
def test_field_displacement_round_trip(tmp_path: Path, pe_dir: str) -> None:
    shape = (7, 8, 5)
    angle = 0.13
    rotation = np.array(
        [[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]]
    )
    affine = np.eye(4)
    affine[:3, :3] = rotation @ np.diag([0.7, 0.8, 1.1])
    rng = np.random.default_rng(5)
    field = rng.normal(0, 8, shape).astype("float32")
    field_file = tmp_path / "field.nii.gz"
    reference = tmp_path / "bold.nii.gz"
    nb.Nifti1Image(field, affine).to_filename(field_file)
    nb.Nifti1Image(np.zeros(shape + (3,), dtype="float32"), affine).to_filename(reference)
    outputs = fieldmap_to_itk_displacement(
        field_file,
        reference,
        phase_encoding_direction=pe_dir,
        total_readout_time=0.031,
        output_dir=tmp_path / pe_dir.replace("-", "neg"),
    )
    recovered = displacement_to_fieldmap(
        outputs["field_warp"],
        reference,
        phase_encoding_direction=pe_dir,
        total_readout_time=0.031,
    )
    assert np.allclose(recovered, field, atol=2e-5)
    warp = nb.load(outputs["field_warp"])
    assert warp.shape == shape + (1, 3)
    assert warp.header.get_intent()[0] == "vector"


def test_constant_field_has_unit_jacobian(tmp_path: Path) -> None:
    shape = (6, 7, 5)
    affine = np.diag([0.8, 0.8, 0.8, 1.0])
    field_file = tmp_path / "field.nii.gz"
    ref = tmp_path / "ref.nii.gz"
    nb.Nifti1Image(np.full(shape, 3, dtype="float32"), affine).to_filename(field_file)
    nb.Nifti1Image(np.zeros(shape + (2,), dtype="float32"), affine).to_filename(ref)
    outputs = fieldmap_to_itk_displacement(
        field_file,
        ref,
        phase_encoding_direction="j-",
        total_readout_time=0.03,
        output_dir=tmp_path / "out",
    )
    assert np.allclose(nb.load(outputs["jacobian"]).get_fdata(), 1.0)


@pytest.mark.skipif(shutil.which("antsApplyTransforms") is None, reason="ANTs is not installed")
def test_ants_warp_matches_voxel_shift_sampling(tmp_path: Path) -> None:
    shape = (24, 24, 12)
    affine = np.diag([1.0, 1.2, 1.5, 1.0])
    x, y, z = np.indices(shape)
    phantom = np.exp(-((x - 12) ** 2 + (y - 12) ** 2 + (z - 6) ** 2) / 35).astype("float32")
    field = np.full(shape, 4.0, dtype="float32")
    moving = tmp_path / "moving.nii.gz"
    field_file = tmp_path / "field.nii.gz"
    nb.Nifti1Image(phantom, affine).to_filename(moving)
    nb.Nifti1Image(field, affine).to_filename(field_file)
    outputs = fieldmap_to_itk_displacement(
        field_file,
        moving,
        phase_encoding_direction="j",
        total_readout_time=0.05,
        output_dir=tmp_path / "warp",
    )
    warped = tmp_path / "warped.nii.gz"
    run_command(
        [
            "antsApplyTransforms",
            "-d",
            "3",
            "-i",
            str(moving),
            "-r",
            str(moving),
            "-o",
            str(warped),
            "-n",
            "Linear",
            "-t",
            str(outputs["field_warp"]),
        ]
    )
    coords = np.indices(shape, dtype="float32")
    coords[1] += field * 0.05
    expected = ndimage.map_coordinates(phantom, coords, order=1, mode="constant", cval=0)
    actual = nb.load(warped).get_fdata(dtype="float32")
    assert np.mean(np.abs(actual[2:-2, 2:-2, 2:-2] - expected[2:-2, 2:-2, 2:-2])) < 1e-3
